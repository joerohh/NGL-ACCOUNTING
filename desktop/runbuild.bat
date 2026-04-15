cd /d "%~dp0"
call "%~dp0build-agent.bat"
if errorlevel 1 exit /b 1
cd /d "%~dp0"
echo ===AGENT_BUILD_OK===
call node "%~dp0bump-version.js"
cd /d "%~dp0"
call "%~dp0node_modules\.bin\electron-builder.cmd" --win
echo ===ELECTRON_BUILD_DONE===
