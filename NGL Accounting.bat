@echo off
title NGL Accounting
echo.
echo   ============================================
echo     NGL Accounting - Starting everything...
echo   ============================================
echo.

cd /d "c:\Users\Joseph\Desktop\NGL ACCOUNTING SERVICE\agent"

REM Find Python
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
    pause
    exit /b 1
)

REM Create venv if needed
if not exist "venv\Scripts\activate.bat" (
    echo   [INFO] First-time setup — creating virtual environment...
    %PYTHON% -m venv venv
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
    python -m playwright install chromium
    echo   [OK] Setup complete!
    echo.
) else (
    call venv\Scripts\activate.bat
)

REM Check .env
if not exist .env (
    echo   [ERROR] No .env file found in agent folder!
    echo   Create agent\.env with your API keys.
    pause
    exit /b 1
)

REM Start agent server fully hidden (no window at all)
echo   [OK] Starting agent server...
wscript "%~dp0agent\start-hidden.vbs"

REM Wait for server to be ready
echo   [..] Waiting for server...
timeout /t 3 /nobreak >nul

REM Open web app (served by the agent at localhost:8787)
echo   [OK] Opening web app...
start "" "http://localhost:8787"

echo.
echo   ============================================
echo     Everything is running!
echo     - Agent server (minimized window)
echo     - Web app (in your browser)
echo   ============================================
echo.
echo   You can close this window.
timeout /t 4 /nobreak >nul
