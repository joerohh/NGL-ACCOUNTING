@echo off
cd /d "%~dp0"
set "PATH=%~dp0;%PATH%"
call "%~dp0publish-release.bat"
