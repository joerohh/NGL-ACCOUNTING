@echo off
cd /d "%~dp0.."

echo ================================================
echo   Pull Latest Updates from GitHub
echo ================================================
echo.

:: Check for uncommitted local changes that could conflict
git diff --quiet
if %errorlevel% neq 0 (
    echo  WARNING: You have unsaved local changes!
    echo.
    echo  Run "Push Updates to All Devices.bat" first to save your work,
    echo  or your changes may conflict with the incoming updates.
    echo.
    choice /C YN /M "Pull anyway? (Y/N)"
    if errorlevel 2 (
        echo.
        echo Cancelled. Push your changes first, then try again.
        echo.
        pause
        exit /b 1
    )
)

echo Checking for updates...
git pull --no-rebase

if %errorlevel% == 0 (
    echo.
    echo  Done! This device is now up to date.
) else (
    echo.
    echo  Pull failed!
    echo.
    :: Check if it's a merge conflict
    git diff --name-only --diff-filter=U >nul 2>&1
    if %errorlevel% == 0 (
        echo  Merge conflict detected. Resolving by keeping the remote version...
        echo.
        git merge --abort
        git stash
        git pull --no-rebase
        echo.
        echo  Pulled successfully. Your local changes were stashed.
        echo  If you need them back, run: git stash pop
    ) else (
        echo  Check your internet connection and try again.
    )
)

echo.
pause
