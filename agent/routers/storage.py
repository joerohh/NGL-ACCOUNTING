"""HTTP endpoints for the Settings · Storage card.

Endpoints:
  GET  /storage/info     → current size/count of output + downloads trees
  POST /storage/cleanup  → run the 7-day sweep immediately, return post-sweep info
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from config import DOWNLOADS_DIR, STORAGE_RETAIN_DAYS
from services.storage import (
    SweepResult,
    get_storage_info,
    sweep_old_downloads,
    sweep_old_outputs,
)

router = APIRouter(prefix="/storage", tags=["storage"])
logger = logging.getLogger("ngl.routers.storage")


# Process-level memo of the last cleanup result. Reset when the agent restarts.
_last_cleanup_ts: float = 0.0
_last_cleanup_freed_bytes: int = 0
_last_cleanup_files_removed: int = 0


def _resolve_output_root() -> Optional[Path]:
    """The user-chosen output location lives in the same JSON config the
    web app already writes to via /files endpoints. If unknown, return None
    and the endpoints report only the downloads-side info.

    The setting lives in APPDATA_DIR/data/settings.json under "output_location"
    when set; the frontend handles initial selection."""
    from config import APPDATA_DIR
    settings_file = APPDATA_DIR / "data" / "settings.json"
    if not settings_file.exists():
        return None
    try:
        import json
        with open(settings_file) as f:
            data = json.load(f)
        loc = data.get("output_location")
        return Path(loc) if loc else None
    except Exception as e:
        logger.warning("Could not read output_location from settings: %s", e)
        return None


@router.get("/info")
async def storage_info():
    """Return current storage totals for the Settings card."""
    output_root = _resolve_output_root() or DOWNLOADS_DIR.parent
    info = get_storage_info(
        output_root=output_root,
        downloads_root=DOWNLOADS_DIR,
        last_cleanup_ts=_last_cleanup_ts,
        last_cleanup_freed_bytes=_last_cleanup_freed_bytes,
        last_cleanup_files_removed=_last_cleanup_files_removed,
    )
    return {
        "output_root": str(output_root),
        "output_size_bytes": info.output_size_bytes,
        "output_file_count": info.output_file_count,
        "output_folder_count": info.output_folder_count,
        "downloads_size_bytes": info.downloads_size_bytes,
        "downloads_batch_count": info.downloads_batch_count,
        "last_cleanup_ts": info.last_cleanup_ts,
        "last_cleanup_freed_bytes": info.last_cleanup_freed_bytes,
        "last_cleanup_files_removed": info.last_cleanup_files_removed,
        "retain_days": STORAGE_RETAIN_DAYS,
    }


@router.post("/cleanup")
async def storage_cleanup():
    """Run the 7-day sweep on demand. Returns post-sweep info."""
    global _last_cleanup_ts, _last_cleanup_freed_bytes, _last_cleanup_files_removed

    output_root = _resolve_output_root()
    out_result = SweepResult()
    if output_root and output_root.exists():
        out_result = sweep_old_outputs(output_root, STORAGE_RETAIN_DAYS)

    dl_result = sweep_old_downloads(DOWNLOADS_DIR, STORAGE_RETAIN_DAYS)

    _last_cleanup_ts = time.time()
    _last_cleanup_freed_bytes = out_result.bytes_freed + dl_result.bytes_freed
    _last_cleanup_files_removed = out_result.files_removed + dl_result.files_removed

    return await storage_info()


def record_startup_sweep(out_result: SweepResult, dl_result: SweepResult) -> None:
    """Called by main.py lifespan after the startup sweep so /storage/info
    can report 'last cleanup ran at startup'."""
    global _last_cleanup_ts, _last_cleanup_freed_bytes, _last_cleanup_files_removed
    _last_cleanup_ts = time.time()
    _last_cleanup_freed_bytes = out_result.bytes_freed + dl_result.bytes_freed
    _last_cleanup_files_removed = out_result.files_removed + dl_result.files_removed
