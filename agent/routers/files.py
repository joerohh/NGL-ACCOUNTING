"""File serving endpoint — serves downloaded PDFs and saves merged output."""

import base64
import logging
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from fastapi.responses import FileResponse

from config import DOWNLOADS_DIR, OUTPUT_DIR
from utils import strip_motw

logger = logging.getLogger("ngl.files")

router = APIRouter(prefix="/files", tags=["files"])


@router.get("/{job_id}/{filename}")
async def serve_file(job_id: str, filename: str):
    """Serve a downloaded PDF so the web app can fetch it as a blob."""
    # Sanitize path components to prevent directory traversal
    safe_job_id = Path(job_id).name
    safe_filename = Path(filename).name

    file_path = DOWNLOADS_DIR / safe_job_id / safe_filename

    if not file_path.exists():
        raise HTTPException(404, f"File not found: {safe_filename}")

    if not file_path.is_file():
        raise HTTPException(400, "Not a file")

    # Verify the file is within the downloads directory
    try:
        file_path.resolve().relative_to(DOWNLOADS_DIR.resolve())
    except ValueError:
        raise HTTPException(403, "Access denied")

    return FileResponse(
        path=str(file_path),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{safe_filename}"'},
    )


@router.get("/{job_id}")
async def list_job_files(job_id: str):
    """List all downloaded files for a job."""
    safe_job_id = Path(job_id).name
    job_dir = DOWNLOADS_DIR / safe_job_id

    if not job_dir.exists():
        raise HTTPException(404, f"Job directory not found: {job_id}")

    files = []
    for f in job_dir.iterdir():
        if f.is_file():
            files.append({
                "name": f.name,
                "size": f.stat().st_size,
                "url": f"/files/{safe_job_id}/{f.name}",
            })

    return {"jobId": job_id, "files": files}


class SaveFileItem(BaseModel):
    filename: str
    data: str  # base64-encoded PDF bytes
    subfolder: str = ""  # may be multi-level: "Per Container/2026-05/2026-05-07"


class SaveOutputRequest(BaseModel):
    files: list[SaveFileItem]
    openFolder: bool = True
    baseLocation: str | None = None  # absolute path; falls back to OUTPUT_DIR if absent
    overwriteFolder: bool = False    # if True, clear the target subfolder before writing


def _safe_subfolder(subfolder: str, base: Path) -> Path:
    """Resolve a multi-level subfolder under base, rejecting path-traversal attempts."""
    if not subfolder:
        return base
    # Reject absolute paths and UNC paths early — these are caller bugs, not just safety hazards.
    if subfolder.startswith(("/", "\\")) or subfolder.startswith("\\\\"):
        raise HTTPException(400, "subfolder must be a relative path")
    # Normalize separators and split into parts
    parts = [p for p in subfolder.replace("\\", "/").split("/") if p not in ("", ".")]
    for part in parts:
        if part == ".." or part.startswith("/") or ":" in part:
            raise HTTPException(400, f"Invalid subfolder path component: {part}")
    target = base.joinpath(*parts) if parts else base
    # Final sanity check: target must be inside base
    try:
        target.resolve().relative_to(base.resolve())
    except ValueError:
        raise HTTPException(400, "Subfolder path escapes base location")
    return target


@router.post("/save-output")
async def save_output(req: SaveOutputRequest):
    """Save merged PDFs into [baseLocation]/[subfolder]/. Supports nested paths and overwrite."""
    if not req.files:
        raise HTTPException(400, "No files provided")

    # Resolve base location — user-chosen path or OUTPUT_DIR fallback
    if req.baseLocation:
        base = Path(req.baseLocation)
        if not base.is_absolute():
            raise HTTPException(400, "baseLocation must be an absolute path")
        base.mkdir(parents=True, exist_ok=True)
    else:
        base = OUTPUT_DIR

    saved = []
    open_dir = base

    # Group files by subfolder so we can apply overwriteFolder once per folder
    by_folder: dict[str, list[SaveFileItem]] = {}
    for item in req.files:
        by_folder.setdefault(item.subfolder, []).append(item)

    for subfolder, items in by_folder.items():
        target_dir = _safe_subfolder(subfolder, base)

        # Overwrite mode: clear the target folder first (only if it exists and is non-empty)
        if req.overwriteFolder and target_dir.exists() and target_dir.is_dir():
            for child in target_dir.iterdir():
                try:
                    if child.is_file():
                        child.unlink()
                    elif child.is_dir():
                        # Recursively remove subdirs (rare, but possible from prior runs)
                        shutil.rmtree(child)
                except Exception as e:
                    logger.warning("Failed to clear %s: %s", child, e)

        target_dir.mkdir(parents=True, exist_ok=True)
        open_dir = target_dir

        for item in items:
            safe_name = Path(item.filename).name
            if not safe_name:
                continue
            dest = target_dir / safe_name
            try:
                pdf_bytes = base64.b64decode(item.data)
                dest.write_bytes(pdf_bytes)
                strip_motw(dest)
                saved.append({"name": safe_name, "size": len(pdf_bytes), "path": str(dest)})
                logger.info("Saved merged file: %s (%d bytes) -> %s", safe_name, len(pdf_bytes), target_dir)
            except Exception as e:
                logger.error("Failed to save %s: %s", safe_name, e)
                saved.append({"name": safe_name, "error": str(e)})

    if req.openFolder and saved:
        try:
            subprocess.Popen(["explorer", str(open_dir)])
        except Exception:
            pass

    return {
        "status": "ok",
        "saved": len([s for s in saved if "error" not in s]),
        "total": len(req.files),
        "outputDir": str(open_dir),
        "files": saved,
    }
