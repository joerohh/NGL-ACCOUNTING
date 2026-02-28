@echo off
title NGL Agent Server
echo.
echo   ============================================
echo     NGL Agent Server - Starting...
echo   ============================================
echo.

cd /d "%~dp0agent"

REM Find Python — try py launcher first (most reliable on Windows), then python
set PYTHON=
where py >nul 2>&1
if %errorlevel% == 0 (
    set PYTHON=py -3
) else (
    where python >nul 2>&1
    if %errorlevel% == 0 (
        set PYTHON=python
    )
)

if "%PYTHON%"=="" (
    echo   [ERROR] Python is not installed!
    echo   Download from: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

echo   [OK] Using: %PYTHON%
%PYTHON% --version
echo.

REM Create venv if it doesn't exist
if not exist "venv\Scripts\activate.bat" (
    echo   [INFO] Creating virtual environment...
    %PYTHON% -m venv venv
    if errorlevel 1 (
        echo   [ERROR] Failed to create virtual environment
        pause
        exit /b 1
    )
    echo   [OK] Virtual environment created
    echo.

    echo   [INFO] Installing dependencies...
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
    if errorlevel 1 (
        echo   [ERROR] Failed to install dependencies
        pause
        exit /b 1
    )
    echo.
    echo   [INFO] Installing Chromium for browser automation...
    python -m playwright install chromium
    echo.
    echo   [OK] Setup complete!
    echo.
) else (
    call venv\Scripts\activate.bat
)

REM Check .env
if not exist .env (
    if exist .env.example (
        echo   [WARNING] No .env file found!
        echo   Run this to create it, then edit with your keys:
        echo     copy .env.example .env
    ) else (
        echo   [WARNING] No .env file found!
        echo   Create agent\.env with your API keys.
    )
    echo.
    pause
    exit /b 1
)

echo   Starting agent on http://localhost:8787 ...
echo   Press Ctrl+C to stop
echo.
python main.py
pause
