"""Prepare shared .env for distribution.

Copies all env vars EXCEPT per-user credentials (QBO/TMS login)
from agent/.env into desktop/build-env/.env.
"""

import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
AGENT_ENV = SCRIPT_DIR.parent / "agent" / ".env"
BUILD_ENV_DIR = SCRIPT_DIR / "build-env"
BUILD_ENV_FILE = BUILD_ENV_DIR / ".env"

# Per-user keys — these are NOT baked into the build
# Users enter them via the Settings page on first run
PER_USER_KEYS = {
    "QBO_EMAIL",
    "QBO_PASSWORD",
    "TMS_EMAIL",
    "TMS_PASSWORD",
    "NGL_ADMIN_USERNAME",
    "NGL_ADMIN_PASSWORD",
    "NGL_ADMIN_DISPLAY",
    # Note: GITHUB_PAT is intentionally NOT excluded — needed by electron-updater
    # to check for updates from the private GitHub repo.
}

def main():
    if not AGENT_ENV.exists():
        print(f"[ERROR] {AGENT_ENV} not found!")
        return

    BUILD_ENV_DIR.mkdir(exist_ok=True)

    lines_out = [
        "# Shared secrets — baked into the distributed build",
        "# Per-user credentials are entered via the Settings page",
        "",
    ]

    with open(AGENT_ENV) as f:
        for line in f:
            stripped = line.strip()
            # Keep comments and blank lines
            if not stripped or stripped.startswith("#"):
                lines_out.append(stripped)
                continue
            # Parse KEY=VALUE
            if "=" not in stripped:
                continue
            key = stripped.split("=", 1)[0].strip()
            if key in PER_USER_KEYS:
                lines_out.append(f"# {key}= (set per-user via Settings page)")
            else:
                lines_out.append(stripped)

    BUILD_ENV_FILE.write_text("\n".join(lines_out) + "\n")
    print(f"[OK] Shared secrets written to {BUILD_ENV_FILE}")
    print(f"     Excluded: {', '.join(sorted(PER_USER_KEYS))}")


if __name__ == "__main__":
    main()
