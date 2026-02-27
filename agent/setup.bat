@echo off
echo ============================================
echo   NGL Agent - One-Click Setup
echo ============================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed!
    echo Download from: https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [OK] Python found

REM Install dependencies
echo.
echo Installing Python packages...
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install packages
    pause
    exit /b 1
)
echo [OK] Packages installed

REM Install Playwright browsers
echo.
echo Installing Chromium browser for automation...
python -m playwright install chromium
if errorlevel 1 (
    echo [ERROR] Failed to install Playwright browsers
    pause
    exit /b 1
)
echo [OK] Chromium installed

REM Check .env
echo.
if not exist .env (
    echo [WARNING] No .env file found. Creating template...
    echo ANTHROPIC_API_KEY=your-claude-api-key-here > .env
    echo NGL_AGENT_TOKEN=ngl-local-dev-token >> .env
    echo.
    echo [ACTION REQUIRED] Edit .env and add your Claude API key!
) else (
    echo [OK] .env file exists
)

echo.
echo ============================================
echo   Setup complete!
echo.
echo   To start the agent:
echo     python main.py
echo.
echo   Make sure to set your Claude API key in .env
echo ============================================
pause
