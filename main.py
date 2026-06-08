import asyncio
import json
import time
import os
import re
from typing import Dict, Any, List, Optional
import httpx
from pydantic import BaseModel, Field
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

# Считываем модель из .env, по умолчанию ставим дешевую gpt-4o-mini в формате OpenRouter
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash")
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
    print("[INIT] Режим: Боевой Google Gemini (gemini-2.5-flash)")

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
                        model='gemini-2.5-flash',
                        contents=user_prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=system_prompt,
                            response_mime_type="application/json",
                            response_schema=ProductCard,
                            temperature=0.7
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
                    temperature=0.7,
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

                # Обновленный пример с демонстрацией HTML-тегов для ИИ
                json_example = (
                    "{\n"
                    '  "title_ru_short": "Сумка женская через плечо - Louis Vuitton - Speedy P9",\n'
                    '  "title_ru": "Сумка Louis Vuitton Speedy P9 Bandoulière 25",\n'
                    '  "brand": "Louis Vuitton",\n'
                    '  "sku": "M27769",\n'
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
                    f"2. Все ключи в JSON должны быть плоскими (без вложений)."
                )

                payload = {
                    "model": OPENROUTER_MODEL,
                    "messages": [
                        {"role": "system", "content": mcp_system_content},  # <-- Просто передаем переменную
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.7
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
    """Вытаскивает id товара (последний сегмент пути после theme_detail/)"""
    match = re.search(r"theme_detail/[^/]+/([^/?#]+)", url)
    return match.group(1) if match else None

async def fetch_single_item_raw_url(original_url: str) -> Dict[str, Any]:
    """
    Вытаскивает параметры из браузерной ссылки и делает прямой запрос 
    к эндпоинту /commodity/view на основном домене www.szwego.com.
    """
    # 1. Извлекаем targetAlbumId и itemId (theme_id) из хвоста ссылки
    ids_match = re.search(r"theme_detail/([^/]+)/([^/?#]+)", original_url)
    if not ids_match:
        print("❌ Ошибка: Не удалось извлечь идентификаторы товара из ссылки!")
        return {}
        
    target_album_id = ids_match.group(1)
    item_id = ids_match.group(2)
    
    # 2. Извлекаем shop_id из параметров ссылки
    shop_match = re.search(r"shop_id=([^&]+)", original_url)
    if not shop_match:
        print("❌ Ошибка: Не удалось найти shop_id в ссылке!")
        return {}
    shop_id = shop_match.group(1)

    # 3. Собираем правильный URL на основном домене www.szwego.com
    api_url = (
        f"https://szwego.com/commodity/view"
        f"?targetAlbumId={target_album_id}"
        f"&itemId={item_id}"
        f"&shopId={shop_id}"
        f"&transLang=en"
    )
        
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
    
    try:
        # Важно: отключаем автоматический редирект, чтобы httpx не прыгал, если сессия моргнет
        async with httpx.AsyncClient(headers=local_headers, cookies=local_cookies, timeout=15.0) as client:
            response = await client.get(api_url, follow_redirects=False)

            # try:
            #     # Предполагаем, что Szwego отдает JSON. Если отдает HTML — запишем как текст.
            #     try:
            #         debug_data = response.json()
            #         with open("szwego_debug_response.json", "w", encoding="utf-8") as f:
            #             json.dump(debug_data, f, ensure_ascii=False, indent=4)
            #         logging.info(f" [DEBUG] Сырой JSON успешно сохранен в szwego_debug_response.json. Ключи: {list(debug_data.keys())}")
            #     except Exception:
            #         with open("szwego_debug_response.html", "w", encoding="utf-8") as f:
            #             f.write(response.text)
            #         logging.info(f" [DEBUG] Сервер вернул HTML. Сохранено в szwego_debug_response.html (Длина: {len(response.text)})")
            # except Exception as debug_err:
            #     logging.error(f" Не удалось сохранить отладочный дамп: {debug_err}")

            if response.status_code == 200:
                res_json = response.json()
                if res_json.get("errcode") != 0:
                    print(f"⚠️ Ошибка Szwego API: {res_json.get('errmsg')}")
                    return {}
                return res_json.get("result", res_json)
                
            print(f"⚠️ Неожиданный ответ сервера. Код: {response.status_code}. Текст: {response.text[:200]}")
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
    item_id_match = re.search(r"theme_detail/[^/]+/([^/?#]+)", original_url)
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

    df = pd.DataFrame(final_cards)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel_name = f"products_export_{timestamp}.xlsx"
    
    df.to_excel(excel_name, index=False)
    print(f"\n📊 БОМБА! Итоговая таблица создана: {excel_name}")

async def main_cli():
    parser = argparse.ArgumentParser(description="Szwego Pipeline CLI")
    parser.add_argument("--all", action="store_true", help="Парсить весь каталог")
    parser.add_argument("--limit", type=int, help="Переопределить лимит количества товаров")
    parser.add_argument("--single", action="store_true", help="Парсить один товар по ссылке")
    parser.add_argument("--build-table", action="store_true", help="Собрать накопленные товары в Excel через ИИ")
    
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
        
        # По желанию: очищаем файл-черновик после успешного экспорта в Excel
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