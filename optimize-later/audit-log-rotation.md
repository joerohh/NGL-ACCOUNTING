# Audit Log Rotation — Verify in Production

`rotate_audit_log()` is wired into `main.py` lifespan, but confirm it's actually firing on the installed build.

## Check
- On the installed Electron app, inspect the audit log file size next to the exe.
- If it's growing unbounded, rotation isn't running — likely a `BASE_DIR` vs `BUNDLE_DIR` path mismatch in the packaged build.

## Files
- `agent/utils.py` — `rotate_audit_log()`
- `agent/main.py` — lifespan hook
- `agent/config.py` — `BASE_DIR` (writable, next to exe)
