@echo off
title NGL Agent Server
echo.
echo   ============================================
echo     NGL Agent Server - Starting...
echo   ============================================
echo.

cd /d "%~dp0agent"

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] Python is not installed!
    echo   Download from: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

REM Check if dependencies are installed
python -c "import fastapi" >nul 2>&1
if errorlevel 1 (
    echo   [INFO] First run detected - installing dependencies...
    echo.
    pip install -r requirements.txt
    python -m playwright install chromium
    echo.
)

REM Check .env
if not exist .env (
    echo   [WARNING] No .env file found. Creating template...
    echo ANTHROPIC_API_KEY=your-claude-api-key-here > .env
    echo NGL_AGENT_TOKEN=ngl-local-dev-token >> .env
    echo.
    echo   [ACTION] Edit agent\.env and add your Claude API key
    echo.
)

echo   Starting agent on http://localhost:8787 ...
echo   Press Ctrl+C to stop
echo.
python main.py
pause
