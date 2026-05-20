cd /d "%~dp0"
call node "%~dp0check-js.js"
if errorlevel 1 exit /b 1
echo ===CHECK_JS_OK===
REM Run the agent test suite before packaging. Skips test_endpoints.py
REM (needs a live HTTP server) and the integration suite (needs live QBO).
REM Catches regressions in the agent->browser contract, e.g. the SSE event
REM payload bug that broke every merge in v2.72 (fixed in v2.74).
cd /d "%~dp0..\agent"
python -m pytest tests/ --ignore=tests/integration --ignore=tests/test_endpoints.py -q
if errorlevel 1 (
  echo ===PYTEST_FAILED===
  exit /b 1
)
echo ===PYTEST_OK===
cd /d "%~dp0"
call "%~dp0build-agent.bat"
if errorlevel 1 exit /b 1
cd /d "%~dp0"
echo ===AGENT_BUILD_OK===
call node "%~dp0bump-version.js"
cd /d "%~dp0"
call "%~dp0node_modules\.bin\electron-builder.cmd" --win
echo ===ELECTRON_BUILD_DONE===
