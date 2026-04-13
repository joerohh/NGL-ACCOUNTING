@echo off
title NGL Accounting — Build + Publish Release
echo.
echo   ============================================
echo     NGL Accounting — Build + Publish Release
echo   ============================================
echo.

cd /d "%~dp0"

REM ── Read GITHUB_PAT from agent/.env ─────────────────────────────
set GH_TOKEN=
for /f "usebackq tokens=1,* delims==" %%a in ("..\agent\.env") do (
    if "%%a"=="GITHUB_PAT" set GH_TOKEN=%%b
)
if "%GH_TOKEN%"=="" (
    echo   [ERROR] GITHUB_PAT not found in agent\.env
    echo   Add a line like: GITHUB_PAT=ghp_your_token_here
    echo   The token needs "repo" scope from github.com/settings/tokens
    pause
    exit /b 1
)
echo   [OK] GitHub token loaded

REM ── Check Node.js ───────────────────────────────────────────────
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo   [ERROR] Node.js is not installed!
    echo   Download from https://nodejs.org/en/download
    pause
    exit /b 1
)

REM ── Step 1: Build the Python agent ──────────────────────────────
echo   [1/3] Building Python agent...
call build-agent.bat
if %errorlevel% neq 0 (
    echo   [ERROR] Agent build failed!
    pause
    exit /b 1
)

REM ── Step 2: Bump version ────────────────────────────────────────
set /p BUILD_VER=<VERSION
echo   [INFO] Building + publishing version: v%BUILD_VER%
node bump-version.js

REM ── Step 3: Install deps if needed ──────────────────────────────
echo   [2/3] Installing Electron dependencies...
if not exist "node_modules" (
    npm install
)

REM ── Step 4: Build + publish to GitHub Releases ──────────────────
echo   [3/3] Building installer + uploading to GitHub Releases...
call npx electron-builder --win --publish always

REM ── Verify and bump for next build ──────────────────────────────
if exist "dist\NGL_ACCOUNTING_INSTALLER_v%BUILD_VER%.0.exe" (
    node bump-version.js --bump

    set /p NEXT_VER=<VERSION
    echo.
    echo   ============================================
    echo     Published!  v%BUILD_VER%
    echo     Installer uploaded to GitHub Releases
    echo     Users will see the update automatically
    echo     Next build will be v!NEXT_VER!
    echo   ============================================
) else (
    echo.
    echo   [ERROR] Build failed — installer not found!
)

echo.
pause
