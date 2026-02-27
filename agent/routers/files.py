"""File serving endpoint — serves downloaded PDFs and saves merged output."""

import base64
import logging
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
    subfolder: str = ""  # subfolder within OUTPUT_DIR (e.g. "One to One Merge")


class SaveOutputRequest(BaseModel):
    files: list[SaveFileItem]
    openFolder: bool = True


@router.post("/save-output")
async def save_output(req: SaveOutputRequest):
    """Save merged PDFs directly to the output folder (bypasses browser download + MOTW)."""
    if not req.files:
        raise HTTPException(400, "No files provided")

    saved = []
    open_dir = OUTPUT_DIR  # track the target dir for Explorer

    for item in req.files:
        safe_name = Path(item.filename).name
        if not safe_name:
            continue

        # Resolve subfolder (sanitize to prevent traversal)
        if item.subfolder:
            safe_subfolder = Path(item.subfolder).name
            target_dir = OUTPUT_DIR / safe_subfolder
        else:
            target_dir = OUTPUT_DIR

        target_dir.mkdir(parents=True, exist_ok=True)
        open_dir = target_dir  # open the last subfolder used

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

    # Open the target subfolder in Windows Explorer
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
