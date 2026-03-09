"""Web app auto-updater.

On agent startup, checks GitHub Releases for a newer version of the web UI.
If found, downloads webapp.zip and extracts it to a local cache folder.
The agent then serves from the cache instead of the bundled webapp.

Setup:
  1. Set WEB_UPDATE_URL in .env to your GitHub repo:
     WEB_UPDATE_URL=github:joerohh/NGL-ACCOUNTING
  2. Set GITHUB_PAT in .env (needed for private repos):
     GITHUB_PAT=ghp_xxxxx
  3. Run publish-update.bat to create a release with updated web files.
  4. Co-workers restart the app — the agent auto-downloads the update.
"""

import json
import logging
import os
import shutil
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

logger = logging.getLogger("ngl.web_updater")

TIMEOUT_S = 15


def _get_local_version(app_dir: Path) -> int:
    """Read version from the local webapp's version.json."""
    vf = app_dir / "version.json"
    if vf.exists():
        try:
            return json.loads(vf.read_text())["version"]
        except (json.JSONDecodeError, KeyError):
            pass
    return 0


def _github_api(endpoint: str, pat: str) -> dict:
    """Call GitHub API with auth."""
    url = f"https://api.github.com{endpoint}"
    headers = {
        "User-Agent": "NGL-Agent/1.0",
        "Accept": "application/vnd.github+json",
    }
    if pat:
        headers["Authorization"] = f"Bearer {pat}"
    req = Request(url, headers=headers)
    resp = urlopen(req, timeout=TIMEOUT_S)
    return json.loads(resp.read())


def _download_bytes(url: str, pat: str) -> bytes:
    """Download a file (GitHub release asset)."""
    headers = {
        "User-Agent": "NGL-Agent/1.0",
        "Accept": "application/octet-stream",
    }
    if pat:
        headers["Authorization"] = f"Bearer {pat}"
    req = Request(url, headers=headers)
    resp = urlopen(req, timeout=120)
    return resp.read()


def _extract_zip(zip_bytes: bytes, dest: Path) -> None:
    """Extract a zip to dest, handling top-level folder detection."""
    tmp = Path(tempfile.mkdtemp(prefix="ngl-webapp-update-"))
    try:
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            zf.extractall(tmp)

        # If zip has a single top-level folder, use its contents
        entries = list(tmp.iterdir())
        source = entries[0] if len(entries) == 1 and entries[0].is_dir() else tmp

        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest)
        logger.info("Update extracted to %s", dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _check_github_releases(owner_repo: str, pat: str, app_dir: Path, cache_dir: Path) -> Path:
    """Check GitHub releases for a newer webapp.zip."""
    local_version = _get_local_version(cache_dir if cache_dir.exists() else app_dir)

    # Get latest release
    release = _github_api(f"/repos/{owner_repo}/releases/latest", pat)
    tag = release.get("tag_name", "")
    logger.info("Latest GitHub release: %s", tag)

    # Extract version from tag (e.g. "web-v2" → 2, "v3" → 3)
    import re
    match = re.search(r"(\d+)$", tag)
    if not match:
        logger.warning("Could not parse version from tag '%s'", tag)
        return cache_dir if cache_dir.exists() and (cache_dir / "version.json").exists() else app_dir

    remote_version = int(match.group(1))
    logger.info("Web UI version: local=%d, remote=%d", local_version, remote_version)

    if remote_version <= local_version:
        logger.info("Web UI is up to date (v%d)", local_version)
        return cache_dir if cache_dir.exists() and (cache_dir / "version.json").exists() else app_dir

    # Find webapp.zip in release assets
    assets = release.get("assets", [])
    zip_asset = next((a for a in assets if a["name"] == "webapp.zip"), None)
    if not zip_asset:
        logger.warning("Release %s has no webapp.zip asset", tag)
        return cache_dir if cache_dir.exists() and (cache_dir / "version.json").exists() else app_dir

    # Download and extract
    logger.info("Downloading web UI update (v%d → v%d)...", local_version, remote_version)
    zip_data = _download_bytes(zip_asset["url"], pat)
    _extract_zip(zip_data, cache_dir)
    logger.info("Web UI updated to v%d!", remote_version)
    return cache_dir


def _check_url(update_url: str, app_dir: Path, cache_dir: Path) -> Path:
    """Check a plain URL for version.json + webapp.zip."""
    local_version = _get_local_version(cache_dir if cache_dir.exists() else app_dir)

    url = f"{update_url.rstrip('/')}/version.json"
    req = Request(url, headers={"User-Agent": "NGL-Agent/1.0"})
    resp = urlopen(req, timeout=TIMEOUT_S)
    remote_version = json.loads(resp.read())["version"]
    logger.info("Web UI version: local=%d, remote=%d", local_version, remote_version)

    if remote_version <= local_version:
        logger.info("Web UI is up to date (v%d)", local_version)
        return cache_dir if cache_dir.exists() and (cache_dir / "version.json").exists() else app_dir

    logger.info("Downloading web UI update (v%d → v%d)...", local_version, remote_version)
    zip_url = f"{update_url.rstrip('/')}/webapp.zip"
    req = Request(zip_url, headers={"User-Agent": "NGL-Agent/1.0"})
    resp = urlopen(req, timeout=120)
    _extract_zip(resp.read(), cache_dir)
    logger.info("Web UI updated to v%d!", remote_version)
    return cache_dir


def check_for_updates(app_dir: Path, cache_dir: Path, update_url: str) -> Path:
    """Check for web UI updates and return the directory to serve from."""
    if not update_url:
        if cache_dir.exists() and (cache_dir / "version.json").exists():
            logger.info("Serving from cached webapp (v%d)", _get_local_version(cache_dir))
            return cache_dir
        return app_dir

    pat = os.getenv("GITHUB_PAT", "")

    try:
        if update_url.startswith("github:"):
            # GitHub releases mode: "github:owner/repo"
            owner_repo = update_url[7:]
            return _check_github_releases(owner_repo, pat, app_dir, cache_dir)
        else:
            # Plain URL mode
            return _check_url(update_url, app_dir, cache_dir)
    except URLError as e:
        logger.warning("Could not check for web updates: %s", e)
    except Exception as e:
        logger.error("Web update check failed: %s", e)

    # Fallback: use cache if available, otherwise bundled
    if cache_dir.exists() and (cache_dir / "version.json").exists():
        return cache_dir
    return app_dir
