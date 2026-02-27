@echo off
cd /d "%~dp0"

echo ================================================
echo   Push Updates to All Devices
echo ================================================
echo.

git add -A

git diff --cached --quiet
if %errorlevel% == 0 (
    echo No new changes to push. Everything is already up to date.
    echo.
    pause
    exit /b 0
)

for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set dt=%%I
set TIMESTAMP=%dt:~0,4%-%dt:~4,2%-%dt:~6,2% %dt:~8,2%:%dt:~10,2%

git commit -m "update: saved on %TIMESTAMP%"

echo.
echo Pushing to GitHub...
git push

echo.
if %errorlevel% == 0 (
    echo  Done! Other devices can now run "Pull Latest Updates.bat" to sync.
) else (
    echo  Push failed. Check your internet connection and try again.
)

echo.
pause
