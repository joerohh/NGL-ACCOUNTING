"""PyInstaller runtime hook — set up environment for bundled agent."""

import os
import sys

# When running as a PyInstaller bundle, sys._MEIPASS points to the data directory
# (_internal/ in PyInstaller 6.x onedir mode).
# Bundled read-only files (.env, selectors, playwright) live there.
# Writable directories (data/, downloads/, debug/) go next to the exe.
if getattr(sys, "frozen", False):
    # The exe is in resources/agent/ngl-agent/ngl-agent.exe
    exe_dir = os.path.dirname(sys.executable)
    bundle_dir = sys._MEIPASS  # _internal/ — where PyInstaller extracts data files

    # NGL_AGENT_DIR = writable location next to the exe (for data/, downloads/, etc.)
    os.environ["NGL_AGENT_DIR"] = exe_dir

    # NGL_BUNDLE_DIR = read-only bundled files (selectors.json, etc.)
    os.environ["NGL_BUNDLE_DIR"] = bundle_dir

    # Web app is at resources/webapp/ (two levels up from exe_dir)
    resources_dir = os.path.dirname(os.path.dirname(exe_dir))
    app_dir = os.path.join(resources_dir, "webapp")
    if os.path.isdir(app_dir):
        os.environ["NGL_APP_DIR"] = app_dir

    # Playwright needs to know where browsers are (bundled in _internal/)
    pw_browsers = os.path.join(bundle_dir, "ms-playwright")
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = pw_browsers

    # Load the baked-in .env from the bundle (_internal/.env)
    # PyInstaller spec copies build-env/ contents to "." which lands in _internal/
    env_file = os.path.join(bundle_dir, ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    # Don't overwrite existing env vars (user overrides take priority)
                    if key.strip() not in os.environ:
                        os.environ[key.strip()] = val.strip()
