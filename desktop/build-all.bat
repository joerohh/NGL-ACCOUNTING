@echo off
title NGL Accounting — Full Build
echo.
echo   ============================================
echo     NGL Accounting — Full Desktop Build
echo   ============================================
echo.

cd /d "%~dp0"

REM Check Node.js
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo   [ERROR] Node.js is not installed!
    echo   Download from https://nodejs.org/en/download
    pause
    exit /b 1
)

REM Step 1: Build the Python agent
echo   [1/3] Building Python agent...
call build-agent.bat
if %errorlevel% neq 0 (
    echo   [ERROR] Agent build failed!
    pause
    exit /b 1
)

REM Step 2: Install npm dependencies (if needed)
echo   [2/3] Installing Electron dependencies...
if not exist "node_modules" (
    npm install
)

REM Step 3: Build the Electron installer
echo   [3/3] Building Electron installer...
npm run build

if %errorlevel% neq 0 (
    echo.
    echo   [ERROR] Electron build failed!
    pause
    exit /b 1
)

echo.
echo   ============================================
echo     Build complete!
echo     Installer: desktop\dist\NGL Accounting Setup *.exe
echo   ============================================
echo.
pause
