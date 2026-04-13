"""Upload the latest installer to Google Drive after a build.

Usage: python upload-to-drive.py
  - Finds the newest .exe in desktop/dist/
  - Moves any previous installers in Drive to "Old Versions" subfolder
  - Uploads the new installer to the configured Google Drive folder
  - Authenticates via OAuth on first run (opens browser), then reuses saved token
"""

import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
AGENT_DIR = SCRIPT_DIR.parent / "agent"
DIST_DIR = SCRIPT_DIR / "dist"
CREDENTIALS_FILE = AGENT_DIR / "gdrive_credentials.json"
TOKEN_FILE = AGENT_DIR / ".gdrive_token.json"
FOLDER_ID = "1T5Erj_tCZnhSpUqLRN_MZjk8MzCqwc6j"
OLD_VERSIONS_FOLDER = "Old Versions"

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def get_drive_service():
    """Authenticate and return a Google Drive API service."""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())

    return build("drive", "v3", credentials=creds)


def find_latest_installer():
    """Find the newest .exe installer in dist/."""
    exes = sorted(DIST_DIR.glob("NGL_ACCOUNTING_INSTALLER_*.exe"), key=os.path.getmtime, reverse=True)
    if not exes:
        print("[ERROR] No installer found in desktop/dist/")
        sys.exit(1)
    return exes[0]


def get_or_create_old_versions_folder(service):
    """Find or create the 'Old Versions' subfolder."""
    results = service.files().list(
        q=f"name='{OLD_VERSIONS_FOLDER}' and '{FOLDER_ID}' in parents "
          f"and mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id)",
    ).execute()
    existing = results.get("files", [])

    if existing:
        return existing[0]["id"]

    metadata = {
        "name": OLD_VERSIONS_FOLDER,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [FOLDER_ID],
    }
    folder = service.files().create(body=metadata, fields="id").execute()
    print(f"[INFO] Created '{OLD_VERSIONS_FOLDER}' folder in Drive")
    return folder["id"]


def archive_old_installers(service, new_filename):
    """Move any existing installers in the root folder to Old Versions."""
    results = service.files().list(
        q=f"'{FOLDER_ID}' in parents and trashed=false and name contains 'NGL_ACCOUNTING_INSTALLER'",
        fields="files(id, name)",
    ).execute()
    old_files = [f for f in results.get("files", []) if f["name"] != new_filename]

    if not old_files:
        return

    old_folder_id = get_or_create_old_versions_folder(service)
    for f in old_files:
        service.files().update(
            fileId=f["id"],
            addParents=old_folder_id,
            removeParents=FOLDER_ID,
        ).execute()
        print(f"[INFO] Archived: {f['name']} -> Old Versions/")


def upload_file(service, file_path: Path):
    """Upload file to Google Drive."""
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(str(file_path), mimetype="application/octet-stream", resumable=True)
    metadata = {"name": file_path.name, "parents": [FOLDER_ID]}
    service.files().create(body=metadata, media_body=media, fields="id").execute()
    print(f"[OK] Uploaded: {file_path.name}")


def main():
    installer = find_latest_installer()
    print(f"[INFO] Uploading: {installer.name} ({installer.stat().st_size / 1024 / 1024:.1f} MB)")

    service = get_drive_service()
    archive_old_installers(service, installer.name)
    upload_file(service, installer)
    print("[DONE] Installer available in Google Drive")


if __name__ == "__main__":
    main()
