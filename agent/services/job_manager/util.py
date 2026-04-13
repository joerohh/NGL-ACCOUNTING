"""Utility mixin — D/O sender cache, customer loading, audit log, event streaming."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from config import DO_SENDER_CACHE_FILE
import services.database as db

logger = logging.getLogger("ngl.job_manager")


def normalize_email_list(emails: list) -> list[str]:
    """Split any comma-separated email strings into individual addresses.

    Fixes data like ["a@x.com, b@x.com"] → ["a@x.com", "b@x.com"].
    """
    result = []
    for entry in emails:
        if isinstance(entry, str) and "," in entry:
            result.extend(e.strip() for e in entry.split(",") if e.strip())
        elif isinstance(entry, str) and entry.strip():
            result.append(entry.strip())
    return result


class JobManagerUtilMixin:
    """Shared utility methods used across fetch and send jobs."""

    # ------------------------------------------------------------------
    # D/O Sender Cache
    # ------------------------------------------------------------------
    def _load_do_sender_cache(self) -> dict:
        """Load the D/O sender cache from disk."""
        try:
            if DO_SENDER_CACHE_FILE.exists():
                with open(DO_SENDER_CACHE_FILE, "r") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning("[DO_CACHE] Failed to load cache: %s", e)
        return {}

    def _save_do_sender_cache(self, container: str, email: str, source: str,
                               strategy: str = "") -> None:
        """Write/update a D/O sender cache entry."""
        try:
            cache = self._load_do_sender_cache()
            existing = cache.get(container, {})
            cache[container] = {
                "email": email,
                "source": source,
                "strategy": strategy,
                "updated": datetime.now(timezone.utc).isoformat(),
                "success_count": existing.get("success_count", 0) + 1,
            }
            with open(DO_SENDER_CACHE_FILE, "w") as f:
                json.dump(cache, f, indent=2)
            logger.info("[DO_CACHE] Cached D/O sender for %s: %s (strategy=%s, count=%d)",
                        container, email, strategy or "N/A",
                        cache[container]["success_count"])
        except Exception as e:
            logger.warning("[DO_CACHE] Failed to save cache: %s", e)

    def _get_cached_do_sender(self, container: str) -> Optional[str]:
        """Return cached D/O sender email for a container, or None."""
        cache = self._load_do_sender_cache()
        entry = cache.get(container)
        if entry and entry.get("email"):
            logger.info("[DO_CACHE] Cache hit for %s: %s (cached %s, count=%d)",
                        container, entry["email"], entry.get("updated", "?"),
                        entry.get("success_count", 0))
            return entry["email"]
        return None

    # ------------------------------------------------------------------
    # Customer loading
    # ------------------------------------------------------------------
    @staticmethod
    def _load_customers() -> dict:
        """Load customer profiles from SQLite."""
        return db.get_all_customers_dict()

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------
    @staticmethod
    def _write_audit_log(entry: dict) -> None:
        """Write a single audit log entry to SQLite."""
        db.write_audit_entry(entry)

    # ------------------------------------------------------------------
    # SSE event stream
    # ------------------------------------------------------------------
    async def event_stream(self, job_id: str):
        """Async generator yielding SSE events for a job."""
        job = self._jobs.get(job_id)
        if not job:
            return

        while True:
            try:
                event = await asyncio.wait_for(job.events.get(), timeout=30)
                yield {
                    "event": event["type"],
                    "data": json.dumps(event),
                }
                if event["type"] in ("job_complete", "send_job_complete", "login_required", "job_paused"):
                    break
            except asyncio.TimeoutError:
                # Send keepalive
                yield {"event": "keepalive", "data": "{}"}
