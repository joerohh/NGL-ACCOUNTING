# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the NGL Agent server.

Bundles: FastAPI server + Playwright driver + Chromium browser + data files.
Output: desktop/agent-dist/ngl-agent.exe (onedir mode)

Run from the repo root:
    cd agent && ../agent/venv/Scripts/pyinstaller.exe ../desktop/ngl-agent.spec
Or use: desktop/build-agent.bat
"""

import os
import sys
from pathlib import Path

# Paths
AGENT_DIR = Path(os.environ.get("NGL_AGENT_DIR", os.path.join(SPECPATH, "..", "agent")))
VENV_SITE = AGENT_DIR / "venv" / "Lib" / "site-packages"
PLAYWRIGHT_DIR = VENV_SITE / "playwright"
PLAYWRIGHT_DRIVER = PLAYWRIGHT_DIR / "driver"

# Auto-detect Chromium version installed by Playwright (e.g. chromium-1148)
_pw_browsers_root = Path(os.path.expanduser("~")) / "AppData" / "Local" / "ms-playwright"
_chromium_dirs = sorted(_pw_browsers_root.glob("chromium-*"), reverse=True)
if not _chromium_dirs:
    raise FileNotFoundError(
        f"No chromium-* directory found in {_pw_browsers_root}. "
        "Run: python -m playwright install chromium"
    )
CHROMIUM_DIR = _chromium_dirs[0]  # Use the latest version
print(f"[spec] Using Chromium: {CHROMIUM_DIR.name}")

DIST_DIR = Path(SPECPATH) / "agent-dist"

a = Analysis(
    [str(AGENT_DIR / "main.py")],
    pathex=[str(AGENT_DIR)],
    binaries=[],
    datas=[
        # Agent data files
        (str(AGENT_DIR / "selectors.json"), "."),
        (str(AGENT_DIR / "tms_selectors.json"), "."),
        # Shared .env with baked-in secrets (created by build script)
        (str(Path(SPECPATH) / "build-env"), "."),
        # Playwright driver (node.exe + JS package)
        (str(PLAYWRIGHT_DRIVER), "playwright/driver"),
        # Chromium browser (version auto-detected)
        (str(CHROMIUM_DIR), f"ms-playwright/{CHROMIUM_DIR.name}"),
    ],
    hiddenimports=[
        # FastAPI / Starlette
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        # SSE
        "sse_starlette",
        # Our modules
        "config",
        "utils",
        "routers",
        "routers.jobs",
        "routers.files",
        "routers.qbo",
        "routers.customers",
        "routers.audit",
        "routers.tms",
        "routers.settings",
        "routers.auth",
        "services",
        "services.database",
        "services.qbo_browser",
        "services.tms_browser",
        "services.claude_classifier",
        "services.email_sender",
        "services.portal_uploader",
        "services.job_manager",
        "services.shared_browser",
        "services.health_check",
        "services.notifier",
        "services.supabase_client",
        "services.web_updater",
        # Packages (split into submodules)
        "services.qbo_browser.__init__",
        "services.qbo_browser.login",
        "services.qbo_browser.search",
        "services.qbo_browser.download",
        "services.qbo_browser.invoice",
        "services.qbo_browser.send",
        "services.tms_browser.__init__",
        "services.tms_browser.login",
        "services.tms_browser.search",
        "services.tms_browser.documents",
        "services.tms_browser.download",
        "services.job_manager.__init__",
        "services.job_manager.fetch_job",
        "services.job_manager.send_job",
        "services.job_manager.send_qbo",
        "services.job_manager.send_oec",
        "services.job_manager.send_portal",
        "services.job_manager.util",
        # httpx for Supabase
        "httpx",
        "httpcore",
        "h11",
        "anyio",
        "sniffio",
        "certifi",
        "idna",
        # PyMuPDF
        "fitz",
        # Anthropic
        "anthropic",
        # Pydantic
        "pydantic",
        # Auth
        "jwt",
        "bcrypt",
        # sqlite3
        "sqlite3",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(Path(SPECPATH) / "pyinstaller-hook.py")],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "scipy",
        "pandas",
        "PIL",
        "cv2",
        "test",
        "unittest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ngl-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # Keep console for logging (hidden by Electron)
    icon=str(Path(SPECPATH) / ".." / "app" / "assets" / "images" / "ngl-desktop.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ngl-agent",
)
