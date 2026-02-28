@echo off
echo ============================================
echo   NGL Agent - One-Click Setup
echo ============================================
echo.

cd /d "%~dp0"

REM Find Python — try py launcher first, then python
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
    echo [ERROR] Python is not installed!
    echo Download from: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [OK] Found: %PYTHON%
%PYTHON% --version
echo.

REM Create virtual environment
echo Creating virtual environment...
%PYTHON% -m venv venv
if errorlevel 1 (
    echo [ERROR] Failed to create virtual environment
    pause
    exit /b 1
)
echo [OK] Virtual environment created
echo.

REM Activate and install
call venv\Scripts\activate.bat

echo Installing Python packages...
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install packages
    pause
    exit /b 1
)
echo [OK] Packages installed
echo.

REM Install Playwright browsers
echo Installing Chromium browser for automation...
python -m playwright install chromium
if errorlevel 1 (
    echo [ERROR] Failed to install Playwright browsers
    pause
    exit /b 1
)
echo [OK] Chromium installed
echo.

REM Check .env
if not exist .env (
    if exist .env.example (
        copy .env.example .env >nul
        echo [ACTION REQUIRED] Edit .env and fill in your real API keys!
    ) else (
        echo [WARNING] No .env file found. Create one with your API keys.
    )
) else (
    echo [OK] .env file exists
)

echo.
echo ============================================
echo   Setup complete!
echo.
echo   To start the agent, run:
echo     Start Agent.bat  (in the project root)
echo.
echo   Make sure your API keys are set in agent\.env
echo ============================================
pause
