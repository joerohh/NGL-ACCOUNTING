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

REM Read and apply version
set /p BUILD_VER=<VERSION
echo   [INFO] Building version: v%BUILD_VER%
node bump-version.js

REM Step 2: Install npm dependencies (if needed)
echo   [2/3] Installing Electron dependencies...
if not exist "node_modules" (
    npm install
)

REM Step 3: Build the Electron installer
echo   [3/3] Building Electron installer...
call npx electron-builder --win

REM Check if the installer file was actually created
if exist "dist\NGL_ACCOUNTING_INSTALLER_v%BUILD_VER%.0.exe" (
    REM Bump version for next build
    node bump-version.js --bump

    set /p NEXT_VER=<VERSION
    echo.
    echo   ============================================
    echo     Build complete!  v%BUILD_VER%
    echo     Installer: desktop\dist\NGL_ACCOUNTING_INSTALLER_v%BUILD_VER%.0.exe
    echo     Next build will be v!NEXT_VER!
    echo   ============================================
) else (
    echo.
    echo   [ERROR] Electron build failed — installer not found!
)

echo.
pause
