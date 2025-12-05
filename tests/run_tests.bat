@echo off
REM Скрипт для запуска тестов RAG системы (Windows)

setlocal enabledelayedexpansion

REM Имена файлов (переменные для избежания дублирования)
set TEST_FILE=test_rag_quality.py
set TEST_DIR=%~dp0

REM Параметры по умолчанию
set KB_NAME=Test KB
set KB_ID=

REM Парсинг аргументов
:parse_args
if "%~1"=="" goto :run_tests
if "%~1"=="--kb-name" (
    set KB_NAME=%~2
    shift
    shift
    goto :parse_args
)
if "%~1"=="--kb-id" (
    set KB_ID=%~2
    shift
    shift
    goto :parse_args
)
if "%~1"=="--help" (
    echo Использование: %0 [OPTIONS]
    echo.
    echo Опции:
    echo   --kb-name NAME    Имя базы знаний для тестирования (по умолчанию: "Test KB")
    echo   --kb-id ID        ID базы знаний (альтернатива --kb-name)
    echo   --help            Показать эту справку
    echo.
    echo Примеры:
    echo   %0 --kb-name "My KB"
    echo   %0 --kb-id 1
    exit /b 0
)
shift
goto :parse_args

:run_tests
echo 🧪 Запуск тестов RAG системы
echo.

REM Проверка наличия Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python не найден
    exit /b 1
)

REM Проверка наличия тестового файла
if not exist "%TEST_DIR%%TEST_FILE%" (
    echo ❌ Файл %TEST_DIR%%TEST_FILE% не найден
    exit /b 1
)

REM Формирование команды
set CMD=python "%TEST_DIR%%TEST_FILE%"

if not "!KB_ID!"=="" (
    set CMD=!CMD! --kb-id !KB_ID!
    echo 📚 Используется база знаний с ID: !KB_ID!
) else (
    set CMD=!CMD! --kb-name "!KB_NAME!"
    echo 📚 Используется база знаний: !KB_NAME!
)

echo.
echo ▶ Запуск тестов...
echo.

REM Запуск тестов
!CMD!

set EXIT_CODE=%ERRORLEVEL%

echo.
if %EXIT_CODE%==0 (
    echo ✅ Тесты завершены успешно
) else (
    echo ❌ Тесты завершились с ошибками (код: %EXIT_CODE%)
)

exit /b %EXIT_CODE%

