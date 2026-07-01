import asyncio
import csv
import json
import time
import os
import re
from typing import Dict, Any, List, Optional
import httpx
from pydantic import BaseModel, Field, field_validator, model_validator
from dotenv import load_dotenv
import sys
import argparse
import pandas as pd
from datetime import datetime
import logging

# Загружаем конфигурацию из .env
load_dotenv()

# ==================== СЧИТЫВАНИЕ CONFIG ИЗ .env ====================
ALBUM_ID = os.getenv("ALBUM_ID", "_ZZajIW0nut8N3cyxzlhAGRf9BcyL4ZIU")
SHOP_ID = os.getenv("SHOP_ID", "").strip()
TARGET_URL = f"https://www.szwego.com/album/personal/all"

AI_PROVIDER = os.getenv("AI_PROVIDER", "openrouter").lower()
TOTAL_TARGET_PRODUCTS = int(os.getenv("TOTAL_TARGET_PRODUCTS", 100))
SZWEGO_SAFE_PAUSE = float(os.getenv("SZWEGO_SAFE_PAUSE", 12.0))

# Считываем токен из .env
SZWEGO_TOKEN = os.getenv("SZWEGO_TOKEN")
# Файл-черновик для хранения поштучно собранных ссылок
RAW_DUMP_FILE = "raw_selected_products.json"

if not SZWEGO_TOKEN:
    raise ValueError("[CRITICAL-ERROR] Токен SZWEGO_TOKEN не найден в файле .env!")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Считываем модель из .env. По умолчанию — точная gemini-3.1-pro-preview в формате OpenRouter
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-3.1-pro-preview")
# Модель для нативного провайдера Gemini (без OpenRouter)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-pro-preview")
# Температура генерации: ниже = меньше выдумок (важно для артикулов)
AI_TEMPERATURE = float(os.getenv("AI_TEMPERATURE", 0.4))
# ===================================================================
# ИИ-Клиенты
openai_client = None
openrouter_client = None
gemini_client = None

# Путь к файлу промпта в папке скрипта
PROMPT_FILE = os.path.join(os.path.dirname(__file__), "ai_prompt.txt")

def load_system_prompt() -> str:
    """Безопасно загружает промпт из TXT файла с резервным фоллбеком"""
    if os.path.exists(PROMPT_FILE):
        try:
            with open(PROMPT_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return content
        except Exception as e:
            print(f"[WARNING] Не удалось прочитать ai_prompt.txt: {e}. Применяю дефолтный промпт.")
    
    # Резервный хардкод на случай, если Павел удалит файл
    return (
        "Ты — эксперт по контенту для премиальных брендов одежды и аксессуаров. "
        "Переведи китайский текст поставщика в карточку на русском. "
        "В 'title_ru_short' держи формат: [Вид товара] - [Бренд] - [Модель]."
    )

# Инициализация выбранного ИИ
if AI_PROVIDER == "openai":
    from openai import AsyncOpenAI
    if not OPENAI_API_KEY:
        raise ValueError("[ERROR] Выбран OpenAI, но OPENAI_API_KEY не найден в .env!")
    openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    print("[INIT] Режим: Боевой OpenAI (GPT-4o-mini)")

elif AI_PROVIDER == "openrouter":
    from openai import AsyncOpenAI
    if not OPENROUTER_API_KEY:
        raise ValueError("[ERROR] Выбран OpenRouter, но OPENROUTER_API_KEY не найден в .env!")
    # Подменяем базовый URL для шлюза OpenRouter
    openrouter_client = AsyncOpenAI(
        base_url="https://openrouter.ai",
        api_key=OPENROUTER_API_KEY,
        default_headers={
            "HTTP-Referer": "https://github.com",  # Для аналитики OpenRouter
            "X-Title": "Szwego AI Parser"
        }
    )
    print("[INIT] Режим: Боевой OpenRouter (Шлюз ИИ подключен)")

elif AI_PROVIDER == "gemini":
    from google import genai
    if not GEMINI_API_KEY:
        raise ValueError("[ERROR] Выбран Gemini, но GEMINI_API_KEY не найден в .env!")
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    from google.genai import types
    print(f"[INIT] Режим: Боевой Google Gemini ({GEMINI_MODEL})")

else:
    print("[INIT] Режим: Локальный автономный MOCK (ИИ отключен, лимиты не тратятся)")

class ProductCard(BaseModel):
    title_ru_short: str = Field(description="Короткое название товара на русском языке")
    title_ru: str = Field(description="SEO-оптимизированное название товара на русском языке")
    brand: str = Field(description="Бренд товара (название производителя или торговой марки)")
    sku: str = Field(description="Артикул или уникальный код модели")
    description_ru: str = Field(description="Описание товара для покупателя")
    category: str = Field(description="Категория товара")
    tags: List[str] = Field(description="Список ключевых слов")

    @model_validator(mode="before")
    @classmethod
    def normalize_ai_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        if "product_card" in data and isinstance(data["product_card"], dict):
            data = data["product_card"]

        aliases = {
            "title": "title_ru",
            "description": "description_ru",
            "category_ru": "category",
            "short_title": "title_ru_short",
        }
        for old_key, new_key in aliases.items():
            if old_key in data and new_key not in data:
                data[new_key] = data[old_key]

        title = data.get("title_ru") or data.get("title_ru_short") or data.get("title") or "Товар"
        data.setdefault("title_ru_short", title)
        data.setdefault("title_ru", title)
        data.setdefault("description_ru", data.get("description") or "")
        data.setdefault("brand", "Премиальный бренд")
        data.setdefault("sku", "N/A")
        data.setdefault("category", "Одежда и Аксессуары")
        data.setdefault("tags", [])

        return data

    @field_validator("tags", mode="before")
    @classmethod
    def coerce_tags(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [tag.strip() for tag in re.split(r"[,;|]", value) if tag.strip()]
        if isinstance(value, list):
            return [str(tag).strip() for tag in value if str(tag).strip()]
        return []

    @field_validator("sku", "category", "brand", mode="before")
    @classmethod
    def coerce_required_strings(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @model_validator(mode="after")
    def fill_empty_strings(self) -> "ProductCard":
        if not self.sku:
            object.__setattr__(self, "sku", "N/A")
        if not self.category:
            object.__setattr__(self, "category", "Одежда и Аксессуары")
        if not self.brand:
            object.__setattr__(self, "brand", "Премиальный бренд")
        if not self.tags:
            fallback_tags = [tag for tag in (self.brand, self.category) if tag and tag not in ("N/A", "Одежда и Аксессуары", "Премиальный бренд")]
            object.__setattr__(self, "tags", fallback_tags or ["lux"])
        return self

HEADERS = {
    "accept": "application/json, text/javascript, */*; q=0.01",
        "accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "origin": "https://www.szwego.com",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "x-requested-with": "XMLHttpRequest",
        # Специфичные заголовки платформы Szwego
        "wego-channel": "net",
        "wego-staging": "1",
        "x-wg-language": "zh"
}

COOKIES = {
    'token': SZWEGO_TOKEN
}

async def generate_ai_card(raw_text: str, max_retries: int = 3) -> Optional[ProductCard]:
    if not raw_text or not raw_text.strip():
        return None
        
    # Динамически подгружаем актуальный промпт перед каждым запросом к ИИ
    system_prompt = load_system_prompt()
    
    user_prompt = f"Данные:\n{raw_text}"
    
    for attempt in range(1, max_retries + 1):
        try:
            if AI_PROVIDER == "mock":
                await asyncio.sleep(0.05)
                sku_match = re.search(r'[A-Za-z0-9\-_]{4,15}', raw_text)
                extracted_sku = sku_match.group(0) if sku_match else "Brand-Lux"
                return ProductCard(
                    title_ru_short=f"Премиальная модель {extracted_sku}",
                    title_ru=f"Премиальная модель {extracted_sku}",
                    brand="Премиальный бренд",
                    sku=extracted_sku,
                    description_ru=f"Локальный тест. Сырой текст: {raw_text.strip()[:40]}...",
                    category="Одежда и Аксессуары",
                    tags=["mock", "test", "lux"]
                )
    
            elif AI_PROVIDER == "gemini":
                loop = asyncio.get_event_loop()
                
                # Явный вызов синхронной функции в пуле потоков без ломающих lambda-оберток
                response = await loop.run_in_executor(
                    None,
                    lambda: gemini_client.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=user_prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=system_prompt,
                            response_mime_type="application/json",
                            response_schema=ProductCard,
                            temperature=AI_TEMPERATURE
                        )
                    )
                )
                # Важно: берем текст ответа и валидируем через Pydantic-модель
                return ProductCard.model_validate_json(response.text)
                
            elif AI_PROVIDER == "openai":
                response = await openai_client.beta.chat.completions.parse(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format=ProductCard,
                    temperature=AI_TEMPERATURE,
                    timeout=30.0
                )
                # ИСПРАВЛЕНО: добавлен индекс [0] для выбора первого варианта ответа
                return response.choices[0].message.parsed
            
            elif AI_PROVIDER == "openrouter":
                import httpx
                import json
                
                headers = {
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com",
                    "X-Title": "Szwego AI Parser"
                }

                # Пример показывает ТОЛЬКО формат ключей и HTML-разметку.
                # Значение sku здесь специально "N/A", чтобы модель не копировала чужой артикул.
                json_example = (
                    "{\n"
                    '  "title_ru_short": "Сумка женская через плечо - Louis Vuitton - Speedy P9",\n'
                    '  "title_ru": "Сумка Louis Vuitton Speedy P9 Bandoulière 25",\n'
                    '  "brand": "Louis Vuitton",\n'
                    '  "sku": "N/A",\n'
                    '  "description_ru": "Сумка Louis Vuitton Speedy P9 — это ультрасовременное переосмысление культового силуэта. Модель выполнена из мягкой кожи теленка премиум-качества.\\n\\n<h3>Особенности модели</h3>\\n• <strong>Эксклюзивный принт:</strong> Монограмма нанесена методом высокоточной печати.\\n• <strong>Внимание к деталям:</strong> Вся фурнитура покрыта золотым напылением.",\n'
                    '  "category": "Сумки",\n'
                    '  "tags": ["Louis Vuitton", "Speedy", "Сумка"]\n'
                    "}"
                )

                mcp_system_content = (
                    f"{system_prompt}\n"
                    f"Выдай ответ СТРОГО в формате JSON, соответствующем этой структуре ключей:\n{json_example}\n"
                    f"КРИТИЧЕСКИЕ ПРАВИЛА:\n"
                    f"1. Не используй markdown разметку ```json и ```. Начни ответ сразу с {{ и закончи }}.\n"
                    f"2. Все ключи в JSON должны быть плоскими (без вложений).\n"
                    f"3. Поле tags — только JSON-массив строк, не строка через запятую.\n"
                    f"4. Поля sku и category всегда строки.\n"
                    f"5. АРТИКУЛ (sku): бери ТОЛЬКО реальный код модели из текста поставщика выше. "
                    f"ЗАПРЕЩЕНО придумывать артикул, брать его из примера в этой инструкции "
                    f"(значение \"N/A\" в примере — это НЕ артикул товара) или использовать ID/ссылку Szwego. "
                    f"Если в тексте поставщика нет явного артикула — пиши строго sku: \"N/A\"."
                )

                payload = {
                    "model": OPENROUTER_MODEL,
                    "messages": [
                        {"role": "system", "content": mcp_system_content},  # <-- Просто передаем переменную
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": AI_TEMPERATURE
                }
                
                async with httpx.AsyncClient(timeout=45.0) as client:
                    res = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
                    
                    # Если OpenRouter вернул ошибку, мы выведем её ТЕКСТ в консоль, а не упадем
                    if res.status_code != 200:
                        raise ValueError(f"OpenRouter Error {res.status_code}: {res.text}")
                        
                    data = res.json()
                    raw_json_text = data["choices"][0]["message"]["content"]
                    
                    # Чистим от возможных markdown тегов ИИ
                    raw_json_text = raw_json_text.replace("```json", "").replace("```", "").strip()
                    
                    # Наш QA-хак очистки обертки
                    parsed_dict = json.loads(raw_json_text)
                    if "product_card" in parsed_dict:
                        parsed_dict = parsed_dict["product_card"]
                        
                    return ProductCard.model_validate(parsed_dict)
                
        except Exception as e:
            print(f"[Попытка {attempt}/{max_retries}] Ошибка вызова ИИ: {e}")
            if attempt < max_retries:
                await asyncio.sleep(2)  # Небольшая пауза перед повтором при ошибке сети/лимитов
            else:
                print(f"❌ Не удалось обработать товар после {max_retries} попыток.")
                
    if not raw_text or not raw_text.strip():
        return None

async def fetch_catalog_page(target_timestamp: Optional[int] = None) -> Dict[str, Any]:
    current_timestamp = target_timestamp if target_timestamp is not None else int(time.time() * 1000)
    params = {
        'albumId': str(ALBUM_ID), 'searchValue': '', 'searchImg': '', 'startDate': '', 'endDate': '',
        'sourceId': '', 'slipType': '1', 'timestamp': str(current_timestamp), 'requestDataType': '', 'transLang': 'en'
    }
    data = {'tagList': '[]'}
    
    async with httpx.AsyncClient(headers=HEADERS, cookies=COOKIES, timeout=30.0) as client:
        try:
            response = await client.post(TARGET_URL, params=params, data=data, follow_redirects=True)
            if response.status_code == 200:
                return response.json()
            return {}
        except Exception:
            return {}

async def process_and_generate_catalog(goods_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    final_catalog = []
    total_items = len(goods_list)
    print(f"\n[STAGE-2] Конвейер ИИ для {total_items} товаров (Провайдер: {AI_PROVIDER.upper()})...")
    
    for index, item in enumerate(goods_list, 1):
        goods_id = item.get("id") or item.get("goods_id")
        raw_text = item.get("content", "") or item.get("title", "")
        images = item.get("imgs_Src", []) or item.get("imgs", [])
        
        print(f"[AI] Обработка {index}/{total_items} (ID: {goods_id})...")
        ai_card = await generate_ai_card(raw_text)
        
        if ai_card:
            product_data = ai_card.model_dump()
            product_data["original_images"] = images
            product_data["szwego_id"] = goods_id
            final_catalog.append(product_data)
            print(f" -> [OK] {product_data['title_ru']} (SKU: {product_data['sku']})")
        else:
            print(f" -> [SKIP] Ошибка ИИ на товаре ID: {goods_id}")
    return final_catalog

async def main(target_limit: int = None):
    all_raw_goods = []
    next_timestamp_cursor = None
    
    # Подставляем лимит: если передан из батника — берем его, иначе — дефолт из .env
    limit = target_limit if target_limit is not None else TOTAL_TARGET_PRODUCTS
    
    print(f"[START] Парсер запущен из .env настроек.")
    print(f"Цель: {limit} товаров из альбома {ALBUM_ID}. Пауза: {SZWEGO_SAFE_PAUSE} сек.")
    
    # Заменяем старый TOTAL_TARGET_PRODUCTS на нашу новую переменную limit
    while len(all_raw_goods) < limit:
        raw_data = await fetch_catalog_page(target_timestamp=next_timestamp_cursor)
        if not raw_data:
            break
            
        result_data = raw_data.get("result", {})
        page_items = result_data.get("items", []) if isinstance(result_data, dict) else []
        
        if not page_items:
            print("[INFO] Достигнут конец каталога.")
            break
            
        print(f"[SZWEGO] Собрано {len(page_items)} позиций со страницы.")
        all_raw_goods.extend(page_items)
        
        next_timestamp_cursor = page_items[-1].get("update_time")
        if not next_timestamp_cursor:
            break
            
        if len(all_raw_goods) < limit:
            print(f"[SAFE-MODE] Защитная пауза {SZWEGO_SAFE_PAUSE} секунд...")
            await asyncio.sleep(SZWEGO_SAFE_PAUSE)
            
    all_raw_goods = all_raw_goods[:limit]
    print(f"\n[STAGE-1 COMPLETED] Сбор окончен. Позиций в пуле: {len(all_raw_goods)}")
    
    if not all_raw_goods:
        return
        
    final_catalog = await process_and_export_table(all_raw_goods)
    
    if final_catalog:
        with open("final_products.json", "w", encoding="utf-8") as f:
            json.dump(final_catalog, f, indent=2, ensure_ascii=False)

def extract_item_id(url: str) -> Optional[str]:
    """Вытаскивает id товара (последний сегмент после theme_detail/ или goods_detail/)."""
    match = re.search(r"(?:theme_detail|goods_detail)/[^/]+/([^/?#]+)", url)
    return match.group(1) if match else None

def extract_shop_id_from_url(url: str) -> Optional[str]:
    """shop_id может быть в query (?shop_id=...) или в поддомене (a123....szwego.com)."""
    shop_match = re.search(r"[?&]shop_id=([^&]+)", url)
    if shop_match:
        return shop_match.group(1)

    subdomain_match = re.search(r"https?://([a-zA-Z0-9_-]+)\.szwego\.com", url)
    if subdomain_match:
        subdomain = subdomain_match.group(1)
        if subdomain not in ("www", "m", "api", "szwego"):
            return subdomain

    return None


def normalize_shop_id(value: str) -> str:
    value = value.strip()
    if re.match(r"^A\d+$", value):
        return f"a{value[1:]}"
    return value


def is_numeric_shop_id(value: str) -> bool:
    return bool(re.match(r"^[Aa]\d{10,}$", value))


def resolve_shop_id_candidates(url: str) -> List[Optional[str]]:
    """Список shop_id для перебора: из URL, .env, либо запрос без shopId (None)."""
    candidates: List[Optional[str]] = []

    url_shop_id = extract_shop_id_from_url(url)
    if url_shop_id:
        candidates.append(normalize_shop_id(url_shop_id))
    else:
        if SHOP_ID:
            candidates.append(normalize_shop_id(SHOP_ID))
        if is_numeric_shop_id(ALBUM_ID):
            normalized_album_shop = normalize_shop_id(ALBUM_ID)
            if normalized_album_shop not in candidates:
                candidates.append(normalized_album_shop)
        candidates.append(None)

    seen = set()
    unique_candidates: List[Optional[str]] = []
    for candidate in candidates:
        key = candidate or ""
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(candidate)

    return unique_candidates


async def _request_commodity_view(
    target_album_id: str,
    item_id: str,
    shop_id: Optional[str] = None,
) -> Dict[str, Any]:
    api_url = (
        f"https://szwego.com/commodity/view"
        f"?targetAlbumId={target_album_id}"
        f"&itemId={item_id}"
        f"&transLang=en"
    )
    if shop_id:
        api_url += f"&shopId={shop_id}"

    local_headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "ru,ru-RU;q=0.9,en-US;q=0.8,en;q=0.7",
        "bundle_id": "",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "priority": "u=1, i",
        "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "wego-albumid": "",
        "wego-channel": "net",
        "wego-staging": "0",
        "wego-uuid": "",
        "wego-version": "",
        "x-wg-language": "zh"
    }

    local_cookies = {
        "token": str(SZWEGO_TOKEN),
        "googtrans": "/en/ru"
    }

    async with httpx.AsyncClient(headers=local_headers, cookies=local_cookies, timeout=15.0) as client:
        response = await client.get(api_url, follow_redirects=False)

        if response.status_code != 200:
            print(f"⚠️ Неожиданный ответ сервера. Код: {response.status_code}. Текст: {response.text[:200]}")
            return {}

        res_json = response.json()
        if res_json.get("errcode") != 0:
            shop_hint = shop_id or "(без shopId)"
            print(f"⚠️ Szwego API [{shop_hint}]: {res_json.get('errmsg')}")
            return {}

        return res_json.get("result", res_json)


def parse_szwego_url(url: str) -> Dict[str, Optional[str]]:
    """Разбирает ссылку Szwego: товар (theme_detail) или магазин (shop_detail)."""
    shop_id = extract_shop_id_from_url(url)

    product_match = re.search(r"(?:theme_detail|goods_detail)/([^/]+)/([^/?#]+)", url)
    if product_match:
        return {
            "link_type": "product",
            "target_album_id": product_match.group(1),
            "item_id": product_match.group(2),
            "shop_id": shop_id,
        }

    shop_match = re.search(r"shop_detail/([^/?#]+)", url)
    if shop_match:
        return {
            "link_type": "shop",
            "target_album_id": shop_match.group(1),
            "item_id": None,
            "shop_id": shop_id,
        }

    return {
        "link_type": "unknown",
        "target_album_id": None,
        "item_id": None,
        "shop_id": shop_id,
    }


def print_shop_link_hint(album_id: str) -> None:
    print("❌ Это ссылка на МАГАЗИН (shop_detail), а не на конкретный товар.")
    print("   Режим «поштучного сбора» работает только со ссылкой на товар.")
    print()
    print("   Как получить правильную ссылку:")
    print("   1. Откройте магазин в браузере")
    print("   2. Кликните на нужный товар")
    print("   3. Скопируйте ссылку — в ней должен быть theme_detail и ДВА ID:")
    print(f"      ...#/theme_detail/{album_id}/XXXXX_товара")
    print()
    print("   Либо используйте «Массовый парсинг каталога» и укажите в .env:")
    print(f'      ALBUM_ID="{album_id}"')


async def fetch_single_item_raw_url(original_url: str) -> Dict[str, Any]:
    """
    Вытаскивает параметры из браузерной ссылки и делает прямой запрос 
    к эндпоинту /commodity/view на основном домене www.szwego.com.
    """
    parsed = parse_szwego_url(original_url)

    if parsed["link_type"] == "shop":
        print_shop_link_hint(parsed["target_album_id"] or "")
        return {}

    if parsed["link_type"] != "product":
        print("❌ Ошибка: Не удалось распознать ссылку Szwego!")
        print("   Ожидается формат: ...#/theme_detail/АЛЬБОМ/ТОВАР")
        return {}

    target_album_id = parsed["target_album_id"]
    item_id = parsed["item_id"]
    shop_candidates = resolve_shop_id_candidates(original_url)

    if extract_shop_id_from_url(original_url) is None:
        print(f"ℹ️ shop_id в ссылке не найден — пробуем {len(shop_candidates)} вариант(ов) запроса...")

    try:
        for shop_id in shop_candidates:
            item_data = await _request_commodity_view(target_album_id, item_id, shop_id)
            if item_data:
                if shop_id:
                    print(f"✅ Товар получен (shopId={shop_id})")
                else:
                    print("✅ Товар получен (запрос без shopId)")
                return item_data

        print("❌ Не удалось получить данные товара ни одним из способов.")
        if not extract_shop_id_from_url(original_url) and not SHOP_ID and not is_numeric_shop_id(ALBUM_ID):
            print("   Подсказка: добавьте в .env SHOP_ID=a201903291406004270013266")
            print("   (ID магазина из поддомена ссылки или из DevTools → Network).")
        return {}
    except Exception as e:
        print(f"\n❌ Ошибка сети при поштучном запросе: {e}")
        return {}

def save_to_raw_dump(item_data: dict, original_url: str):
    """Сохраняет сырой товар в черновик json-файла (накопительный режим)"""
    data = []
    if os.path.exists(RAW_DUMP_FILE):
        try:
            with open(RAW_DUMP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = []
            
    # Защита: вытаскиваем уникальный ID товара из самой ссылки Павла, если API его не вернуло
    item_id_match = re.search(r"(?:theme_detail|goods_detail)/[^/]+/([^/?#]+)", original_url)
    fallback_id = item_id_match.group(1) if item_id_match else f"id_{int(time.time()*1000)}"
    
    current_id = item_data.get("id") or item_data.get("itemId") or fallback_id
    item_data["id"] = current_id  # Гарантируем наличие ключа id для всей системы

    # Проверка на реальные дубликаты
    if not any(str(x.get("id")) == str(current_id) for x in data):
        data.append(item_data)
        with open(RAW_DUMP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        print(f"✅ Товар добавлен в черновик (Всего в списке: {len(data)}).")
    else:
        print("ℹ️ Этот товар уже есть в списке черновиков.")
    # Проверка на дубликаты, чтобы не добавлять один товар дважды
    if not any(x.get("id") == item_data.get("id") for x in data):
        data.append(item_data)
        with open(RAW_DUMP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        print(f"✅ Товар '{item_data.get('title', 'Без названия')}' добавлен в черновик (Всего: {len(data)}).")
    else:
        print("ℹ️ Этот товар уже есть в списке черновиков.")

async def run_single_mode(url: str):
    """Точка входа для поштучного режима"""
    if not url.startswith("http"):
        print("❌ Строка не похожа на ссылку! Проверьте ввод.")
        return
        
    item_data = await fetch_single_item_raw_url(url)
    if item_data:
        # Передаем url аргументом для генерации fallback_id
        save_to_raw_dump(item_data, url)
    else:
        print("❌ Не удалось получить данные товара.")

def format_card_for_export(card_data: dict) -> dict:
    """Подготавливает строку для CSV: пустой sku, теги через запятую, одна строка на товар."""
    row = dict(card_data)
    sku = str(row.get("sku", "")).strip()
    if not sku or sku.upper() == "N/A":
        row["sku"] = ""
    tags = row.get("tags", [])
    if isinstance(tags, list):
        row["tags"] = ", ".join(str(tag).strip() for tag in tags if str(tag).strip())
    elif tags is None:
        row["tags"] = ""
    else:
        row["tags"] = str(tags).strip()
    for field in ("description_ru", "title_ru", "title_ru_short"):
        if field in row and row[field]:
            row[field] = re.sub(r"\s*\n\s*", " ", str(row[field])).strip()
    return row

async def process_and_export_table(raw_items: list):
    """Принимает список сырых товаров, прогоняет через ИИ и делает экспорт"""
    if not raw_items:
        print("❌ Нет данных для обработки ИИ!")
        return

    print(f"🤖 Запуск пакетной генерации для {len(raw_items)} товаров...")
    final_cards = []

    for idx, item in enumerate(raw_items, 1):
        # === ОБНОВЛЕННЫЙ СБОР ТЕКСТА ПОД КОНТРАКТ COMMODITY ===
        # 1. Проверяем вложенную структуру (result -> commodity)
        commodity_block = item.get('commodity', item.get('result', {}).get('commodity', {})) if isinstance(item, dict) else {}
        
        # 2. Извлекаем заголовок и описание с учетом вложенности
        if commodity_block:
            title = commodity_block.get('title', '')
            desc = commodity_block.get('description', commodity_block.get('content', ''))
        else:
            # Резервный вариант для старой плоской структуры
            title = item.get('title', item.get('theme_name', ''))
            desc = item.get('description', item.get('content', ''))
            
        # 3. Собираем финальный текст для отправки в OpenRouter
        raw_text = f"Заголовок: {title}\nОписание: {desc}"

        # === ИСПРАВЛЕННЫЙ БЛОК: Сбор ID под обе структуры Szwego ===
        item_id = (
            item.get('goods_id') or 
            item.get('id') or 
            item.get('itemId') or 
            (commodity_block.get('goods_id') if isinstance(commodity_block, dict) else None) or
            (commodity_block.get('id') if isinstance(commodity_block, dict) else None) or
            f"unknown_{idx}"
        )
        # ==========================================================

        print(f" 🤖 [{idx}/{len(raw_items)}] Обработка товара ID: {item_id}...")
        ai_card = await generate_ai_card(raw_text)

        if ai_card:
            card_data = ai_card.model_dump() if hasattr(ai_card, 'model_dump') else ai_card
            card_data['szwego_id'] = item_id
        
        if ai_card:
            card_data = ai_card.model_dump() if hasattr(ai_card, 'model_dump') else ai_card
            
            # --- ИСПРАВЛЕННЫЙ БЛОК СБОРА ИЗОБРАЖЕНИЙ ПО СКРИНШОТУ DEVTOOLS ---
            img_urls = []
            
            # 1. Проверяем поштучный режим (result -> commodity -> imgsSrc)
            if "commodity" in item and isinstance(item["commodity"], dict):
                commodity_data = item["commodity"]
                img_urls = commodity_data.get("imgsSrc") or commodity_data.get("imgs") or []
            
            # 2. Если структура плоская (как в режиме полного парсинга альбома)
            if not img_urls:
                img_urls = item.get("imgsSrc") or item.get("imgs") or item.get("image_list") or item.get("img_list") or []
                
            # 3. Дополнительная очистка, если внутри массива лежат словари, а не строки
            if img_urls and isinstance(img_urls, list) and isinstance(img_urls[0], dict):
                img_urls = [img.get("url", img.get("thumb", "")) for img in img_urls if img.get("url") or img.get("thumb")]
                
            # Записываем ссылки на фото через запятую в финальную карточку
            card_data['original_imgs'] = ", ".join(img_urls) if isinstance(img_urls, list) else str(img_urls)
            final_cards.append(card_data)
        else:
            print(f"⚠️ [SKIP] Ошибка генерации для товара ID: {item.get('id')}")
            
    if not final_cards:
        print("❌ Ни один товар не был успешно обработан.")
        return

    with open("final_products.json", "w", encoding="utf-8") as f:
        json.dump(final_cards, f, ensure_ascii=False, indent=4)

    export_rows = [format_card_for_export(card) for card in final_cards]
    df = pd.DataFrame(export_rows)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_name = f"products_export_{timestamp}.csv"
    
    df.to_csv(
        csv_name,
        index=False,
        encoding="utf-8-sig",
        sep=";",
        quoting=csv.QUOTE_NONNUMERIC,
        lineterminator="\n",
    )
    print(f"\n📊 БОМБА! Итоговая таблица создана: {csv_name}")

async def main_cli():
    parser = argparse.ArgumentParser(description="Szwego Pipeline CLI")
    parser.add_argument("--all", action="store_true", help="Парсить весь каталог")
    parser.add_argument("--limit", type=int, help="Переопределить лимит количества товаров")
    parser.add_argument("--single", action="store_true", help="Парсить один товар по ссылке")
    parser.add_argument("--build-table", action="store_true", help="Собрать накопленные товары в CSV через ИИ")
    
    args = parser.parse_args()

    # РЕЖИМ 1: Парсинг всего каталога
    if args.all:
        # Если передан лимит из батника — берем его, иначе — из .env, иначе — 100 по умолчанию
        target_limit = args.limit if args.limit is not None else int(os.getenv("TOTAL_TARGET_PRODUCTS", 100))
        print(f"🚀 Запуск полного парсинга. Цель: {target_limit} товаров.")
        
        # --- ЦИКЛ ПАГИНАЦИИ (STAGE-1) ---
        await main(target_limit)

    # РЕЖИМ 2: Поштучный сбор по ссылке
    elif args.single:
        url_arg = input("👉 Вставьте ссылку на товар Szwego: ").strip()
        if url_arg:
            await run_single_mode(url_arg)

    # РЕЖИМ 3: Пакетная сборка таблицы из поштучных черновиков
    elif args.build_table:
        if not os.path.exists(RAW_DUMP_FILE):
            print(f"❌ Черновик {RAW_DUMP_FILE} не найден! Сначала добавьте товары через пункт 3.")
            return
            
        try:
            with open(RAW_DUMP_FILE, "r", encoding="utf-8") as f:
                raw_selected_items = json.load(f)
        except Exception as e:
            print(f"❌ Ошибка чтения черновика: {e}")
            return

        print(f"📂 Загружено {len(raw_selected_items)} товаров из поштучного списка черновиков.")
        await process_and_export_table(raw_selected_items)
        
        # По желанию: очищаем файл-черновик после успешного экспорта в CSV
        try:
            os.remove(RAW_DUMP_FILE)
            print(f"🧹 Черновик {RAW_DUMP_FILE} успешно очищен.")
        except Exception:
            pass
    else:
        parser.print_help()

if __name__ == "__main__":
    # Заменяем старый запуск asyncio.run(main()) на новый CLI-обработчик
    asyncio.run(main_cli())