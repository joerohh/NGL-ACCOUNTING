@echo off
title NGL Accounting — Build Agent
echo.
echo   ============================================
echo     Building NGL Agent (PyInstaller)
echo   ============================================
echo.

cd /d "%~dp0..\agent"

REM Activate venv
if not exist "venv\Scripts\activate.bat" (
    echo   [ERROR] No venv found. Run setup.bat first.
    pause
    exit /b 1
)
call venv\Scripts\activate.bat

REM Install PyInstaller if needed
pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo   [INFO] Installing PyInstaller...
    pip install pyinstaller
)

REM Prepare shared secrets (Python script — more reliable than batch parsing)
echo   [INFO] Preparing shared secrets...
python "%~dp0prepare-env.py"
if %errorlevel% neq 0 (
    echo   [ERROR] Failed to prepare shared secrets!
    pause
    exit /b 1
)

REM Run PyInstaller
echo   [INFO] Running PyInstaller (this takes a few minutes)...
pyinstaller --distpath "%~dp0agent-dist" --workpath "%~dp0build-temp" "%~dp0ngl-agent.spec" --noconfirm

if %errorlevel% neq 0 (
    echo.
    echo   [ERROR] PyInstaller build failed!
    pause
    exit /b 1
)

echo.
echo   ============================================
echo     Agent build complete!
echo     Output: desktop\agent-dist\ngl-agent\
echo   ============================================
echo.
