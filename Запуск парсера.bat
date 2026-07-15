@echo off
chcp 65001 > nul
title Конвейер Szwego AI Парсера
:MainMenu
cd /d "%~dp0"
cls
echo ============================================================
echo ГЛАВНОЕ МЕНЮ ПАРСЕРА SZWEGO AI
echo ============================================================
echo.
echo 1. Запустить парсер всего альбома (С указанием количества)
echo 2. Настроить / Перезаписать параметры (.env)
echo 3. Добавить конкретный товар по ссылке (Поштучно)
echo 4. Создать итоговую Excel-таблицу через ИИ
echo 5. Редактировать системный промпт ИИ
echo 6. Выйти из программы
echo.
echo ============================================================
echo.
set "CHOICE="
set /p "CHOICE= Выберите пункт меню (1-6): "
if "%CHOICE%"=="1" goto :RunFullParser
if "%CHOICE%"=="2" goto :PrepareEnvConfig
if "%CHOICE%"=="3" goto :RunSingleParserLoop
if "%CHOICE%"=="4" goto :BuildFinalTable
if "%CHOICE%"=="5" goto :EditAiPrompt
if "%CHOICE%"=="6" exit /b

echo [!] Неверный выбор!
timeout /t 2 > nul
goto :MainMenu

:PrepareEnvConfig
cd /d "%~dp0"
if not exist .venv python -m venv .venv
cls
.venv\Scripts\python.exe configure.py
echo.
pause
goto :MainMenu

:RunFullParser
cd /d "%~dp0"
cls
echo ============================================================
echo НАСТРОЙКА МАССОВОГО ПАРСИНГА
echo ============================================================
echo.
set "LIMIT_INPUT="
set /p "LIMIT_INPUT= Сколько товаров нужно запарсить? (Нажмите Enter для значения из .env): "
cls
echo [ ] Запуск сканирования альбома...
if "%LIMIT_INPUT%"=="" (
    .venv\Scripts\python.exe main.py --all
) else (
    .venv\Scripts\python.exe main.py --all --limit %LIMIT_INPUT%
)
echo.
pause
goto :MainMenu

:RunSingleParserLoop
cd /d "%~dp0"
cls
echo ============================================================
echo РЕЖИМ СБОРА ТОВАРОВ ПО ССЫЛКАМ
echo ============================================================
echo.
.venv\Scripts\python.exe main.py --single
echo.
echo ------------------------------------------------------------
echo 1. Добавить ЕЩЕ ОДИН товар
echo 2. Вернуться в Главное меню
echo ------------------------------------------------------------
set "LOOP_CHOICE="
set /p "LOOP_CHOICE= Ваш выбор: "
if "%LOOP_CHOICE%"=="1" goto :RunSingleParserLoop
goto :MainMenu

:BuildFinalTable
cd /d "%~dp0"
cls
echo ============================================================
echo СБОРКА ИТОГОВОЙ ТАБЛИЦЫ (ОБРАБОТКА ИИ)
echo ============================================================
echo.
.venv\Scripts\python.exe main.py --build-table
echo.
pause
goto :MainMenu

:EditAiPrompt
cd /d "%~dp0"
cls
echo ============================================================
echo РЕДАКТИРОВАНИЕ ПРОМПТА ИИ
echo ============================================================
echo.
echo Открываю файл промпта в Блокноте...
echo.
echo [!] ВАЖНО: После редактирования текста нажмите Ctrl+S (Сохранить)
echo     и просто закройте Блокнот, чтобы вернуться в меню.
echo ============================================================
echo.
:: Проверяем, существует ли файл. Если нет — создаем пустой, чтобы Блокнот не ругался
if not exist ai_prompt.txt echo. > ai_prompt.txt
:: /wait приостанавливает выполнение батника, пока Павел не закроет Блокнот
start /wait notepad.exe "ai_prompt.txt"
goto :MainMenu