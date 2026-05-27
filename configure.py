import os
import subprocess
import sys

def get_current_env_values() -> dict:
    """Парсит существующий .env, чтобы вытащить старые значения"""
    values = {
        "SZWEGO_TOKEN": "",
        "ALBUM_ID": "_ZZajIW0nut8N3cyxzlhAGRf9BcyL4ZIU",
        "AI_PROVIDER": "mock",
        "GEMINI_API_KEY": "",
        "OPENAI_API_KEY": "",
        "OPENROUTER_API_KEY": ""
    }
    if os.path.exists(".env"):
        try:
            with open(".env", "r", encoding="utf-8") as f:
                for line in f:
                    if "=" in line and not line.strip().startswith("#"):
                        key, val = line.strip().split("=", 1)
                        # Убираем кавычки, если они есть
                        val = val.strip().strip('"').strip("'")
                        if key in values:
                            values[key] = val
        except Exception:
            pass
    return values

def check_and_install_requirements():
    print("[📥] Проверка и автоматическая установка библиотек...")
    if os.path.exists("requirements.txt"):
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], stdout=subprocess.DEVNULL)
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
            print("[✅] Все библиотеки успешно подготовлены.")
            return True
        except Exception as e:
            print(f"[❌ OШИБКА] Не удалось установить библиотеки: {e}")
            return False
    else:
        print("[❌ OШИБКА] Файл requirements.txt не найден в папке проекта!")
        return False

def main():
    print("=" * 60)
    print("  ⚙️ НАСТРОЙКА КОНФИГУРАЦИИ ПАРСЕРА")
    print("=" * 60 + "\n")
    
    # Подгружаем старые параметры, если они есть
    current = get_current_env_values()
    
    # 1. Настройка токена
    if current["SZWEGO_TOKEN"]:
        # Показываем первые и последние 8 символов токена для визуального ориентира
        short_token = f"{current['SZWEGO_TOKEN'][:8]}...{current['SZWEGO_TOKEN'][-8:]}"
        print(f"ℹ️ Найден текущий токен: {short_token}")
        token = input("👉 Введите новый SZWEGO_TOKEN или нажмите Enter для сохранения текущего: ").strip()
        if not token:
            token = current["SZWEGO_TOKEN"]
    else:
        token = input("👉 Введите или вставьте ваш SZWEGO_TOKEN из браузера: ").strip()
        if not token:
            print("❌ Ошибка: Токен не может быть пустым при первом запуске!")
            return False
            
    # 2. Настройка ID альбома
    album_prompt = f"👉 Введите ID альбома (Enter для текущего '{current['ALBUM_ID']}'): "
    album = input(album_prompt).strip()
    if not album:
        album = current["ALBUM_ID"]
        
    # 3. Настройка ИИ
    ai_prompt = f"👉 Выберите ИИ (gemini / openai / openrouter / mock, Enter для текущего '{current['AI_PROVIDER']}'): "
    ai_provider = input(ai_prompt).strip().lower()
    if not ai_provider:
        ai_provider = current["AI_PROVIDER"]
    if ai_provider not in ["gemini", "openai", "openrouter", "mock"]:
        ai_provider = "mock"
        
    gemini_key = current["GEMINI_API_KEY"]
    openai_key = current["OPENAI_API_KEY"]
    openrouter_key = current["OPENROUTER_API_KEY"]
    
    # 4. Проверка ключей в зависимости от выбора
    if ai_provider == "gemini":
        default_gemini = f" (Enter для текущего: {gemini_key[:6]}...)" if gemini_key else ""
        gemini_key = input(f"👉 Введите ваш GEMINI_API_KEY{default_gemini}: ").strip()
        if not gemini_key:
            gemini_key = current["GEMINI_API_KEY"]
        if not gemini_key:
            print("⚠️ Ключ не введен! Переключаю в режим 'mock'.")
            ai_provider = "mock"
            
    elif ai_provider == "openai":
        default_openai = f" (Enter для текущего: {openai_key[:6]}...)" if openai_key else ""
        openai_key = input(f"👉 Введите ваш OPENAI_API_KEY{default_openai}: ").strip()
        if not openai_key:
            openai_key = current["OPENAI_API_KEY"]
        if not openai_key:
            print("⚠️ Ключ не введен! Переключаю в режим 'mock'.")
            ai_provider = "mock"

    elif ai_provider == "openrouter":
        default_or = f" (Enter для текущего: {openrouter_key[:6]}...)" if openrouter_key else ""
        openrouter_key = input(f" Введите ваш OPENROUTER_API_KEY{default_or}: ").strip()
        if not openrouter_key:
            openrouter_key = current["OPENROUTER_API_KEY"]
        if not openrouter_key:
            print(" Ключ не введен! Переключаю в режим 'mock'.")
            ai_provider = "mock"

    # Запись новой конфигурации
    with open(".env", "w", encoding="utf-8") as f:
        f.write("# Настройки парсера Szwego\n")
        f.write(f'SZWEGO_TOKEN="{token}"\n')
        f.write(f'ALBUM_ID="{album}"\n')
        f.write(f'AI_PROVIDER="{ai_provider}"\n')
        f.write("TOTAL_TARGET_PRODUCTS=100\n")
        f.write("SZWEGO_SAFE_PAUSE=12.0\n")
        f.write(f'GEMINI_API_KEY="{gemini_key}"\n')
        f.write(f'OPENAI_API_KEY="{openai_key}"\n')
        f.write(f'OPENROUTER_API_KEY="{openrouter_key}"\n')
        f.write(f'OPENROUTER_MODEL="google/gemini-2.5-flash"\n')
        
    print("\n[✅] Настройки файла .env успешно обновлены!")
    
    check_and_install_requirements()
    return True

if __name__ == "__main__":
    main()
