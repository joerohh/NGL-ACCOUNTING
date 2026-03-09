@echo off
setlocal enabledelayedexpansion
title NGL — Publish Web Update
echo.
echo  ========================================
echo   NGL Accounting — Publish Web Update
echo  ========================================
echo.

:: ── Load .env for GITHUB_PAT ──
set "ENV_FILE=%~dp0..\agent\.env"
if exist "%ENV_FILE%" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
        set "line=%%A"
        if not "!line:~0,1!"=="#" (
            if "%%A"=="GITHUB_PAT" set "GITHUB_PAT=%%B"
        )
    )
)

if "%GITHUB_PAT%"=="" (
    echo [ERROR] GITHUB_PAT not found in agent\.env
    echo Add this line to agent\.env:
    echo   GITHUB_PAT=ghp_your_token_here
    echo.
    echo You can create a token at: https://github.com/settings/tokens
    echo Required scope: repo
    pause
    exit /b 1
)

:: ── Read current version ──
set "VERSION_FILE=%~dp0..\app\version.json"
if not exist "%VERSION_FILE%" (
    echo [ERROR] app\version.json not found
    pause
    exit /b 1
)

:: Parse version number from JSON
for /f "tokens=2 delims=:}" %%V in ('type "%VERSION_FILE%"') do (
    set "CURRENT_VERSION=%%V"
)
set "CURRENT_VERSION=%CURRENT_VERSION: =%"
echo Current version: %CURRENT_VERSION%

:: ── Bump version ──
set /a NEW_VERSION=%CURRENT_VERSION%+1
echo New version:     %NEW_VERSION%
echo.

:: Update version.json
echo {"version": %NEW_VERSION%}> "%VERSION_FILE%"
echo [OK] Bumped version.json to v%NEW_VERSION%

:: ── Create webapp.zip ──
set "ZIP_FILE=%~dp0..\webapp.zip"
if exist "%ZIP_FILE%" del "%ZIP_FILE%"

echo [..] Creating webapp.zip from app\ folder...
powershell -NoProfile -Command "Compress-Archive -Path '%~dp0..\app\*' -DestinationPath '%ZIP_FILE%' -Force"
if errorlevel 1 (
    echo [ERROR] Failed to create zip
    pause
    exit /b 1
)
echo [OK] webapp.zip created

:: ── Create GitHub Release ──
set "REPO=joerohh/NGL-ACCOUNTING"
set "TAG=web-v%NEW_VERSION%"
set "RELEASE_NAME=Web UI v%NEW_VERSION%"

echo.
echo [..] Creating GitHub release: %TAG%...

:: Create release via GitHub API
powershell -NoProfile -Command ^
  "$headers = @{ 'Authorization' = 'Bearer %GITHUB_PAT%'; 'Accept' = 'application/vnd.github+json'; 'User-Agent' = 'NGL-Publisher' }; " ^
  "$body = @{ tag_name = '%TAG%'; name = '%RELEASE_NAME%'; body = 'Web UI update v%NEW_VERSION%'; draft = $false; prerelease = $false } | ConvertTo-Json; " ^
  "$release = Invoke-RestMethod -Uri 'https://api.github.com/repos/%REPO%/releases' -Method Post -Headers $headers -Body $body -ContentType 'application/json'; " ^
  "$release.upload_url -replace '\{.*\}','' | Out-File -NoNewline '%TEMP%\ngl_upload_url.txt'; " ^
  "Write-Host '[OK] Release created: ' $release.html_url"

if errorlevel 1 (
    echo [ERROR] Failed to create release
    echo Make sure your GITHUB_PAT has 'repo' scope
    pause
    exit /b 1
)

:: ── Upload webapp.zip as release asset ──
set /p UPLOAD_URL=<"%TEMP%\ngl_upload_url.txt"
del "%TEMP%\ngl_upload_url.txt" 2>nul

echo [..] Uploading webapp.zip to release...
powershell -NoProfile -Command ^
  "$headers = @{ 'Authorization' = 'Bearer %GITHUB_PAT%'; 'Content-Type' = 'application/zip'; 'User-Agent' = 'NGL-Publisher' }; " ^
  "$bytes = [System.IO.File]::ReadAllBytes('%ZIP_FILE%'); " ^
  "$response = Invoke-RestMethod -Uri '%UPLOAD_URL%?name=webapp.zip' -Method Post -Headers $headers -Body $bytes; " ^
  "Write-Host '[OK] webapp.zip uploaded (' $response.size 'bytes)'"

if errorlevel 1 (
    echo [ERROR] Failed to upload webapp.zip
    pause
    exit /b 1
)

:: Cleanup
del "%ZIP_FILE%" 2>nul

echo.
echo  ========================================
echo   Published Web UI v%NEW_VERSION%
echo  ========================================
echo.
echo Co-workers will get this update next time
echo they restart the app.
echo.
pause
