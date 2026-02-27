"""Claude API document classifier — identifies invoices vs PODs.

Cost-saving measures:
  1. Filename-based bypass — if the filename clearly says "invoice" or "pod", skip the API call entirely
  2. Lower DPI (100 instead of 150) — smaller image = fewer tokens = cheaper
  3. Daily usage cap — prevents runaway costs
  4. Usage tracking — logs every API call with cost estimate to disk
"""

import base64
import json
import logging
import re
import time
from datetime import date
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
from anthropic import AsyncAnthropic

from config import (
    CLAUDE_API_KEY,
    CLAUDE_MODEL,
    CLASSIFICATION_CONFIDENCE_THRESHOLD,
    CLASSIFICATION_DPI,
    DAILY_API_CALL_LIMIT,
    API_USAGE_FILE,
)

logger = logging.getLogger("ngl.classifier")

# ── Filename patterns that let us skip the API call ────────────────────
_INVOICE_PATTERNS = re.compile(
    r"(invoice|inv[_\-\s]|billing|freight.?bill)", re.IGNORECASE
)
_POD_PATTERNS = re.compile(
    r"(pod|proof.?of.?delivery|delivery.?receipt|bol|bill.?of.?lading)", re.IGNORECASE
)


class ClassificationResult:
    """Result of classifying a single document."""

    def __init__(
        self,
        doc_type: str,
        confidence: float,
        container_hint: Optional[str] = None,
        needs_review: bool = False,
        skipped_api: bool = False,
    ) -> None:
        self.doc_type = doc_type          # "invoice", "pod", "bol", "other"
        self.confidence = confidence       # 0.0 – 1.0
        self.container_hint = container_hint  # container number if found in doc
        self.needs_review = needs_review   # True if confidence below threshold
        self.skipped_api = skipped_api     # True if classified by filename (no API call)

    def to_dict(self) -> dict:
        return {
            "doc_type": self.doc_type,
            "confidence": self.confidence,
            "container_hint": self.container_hint,
            "needs_review": self.needs_review,
            "skipped_api": self.skipped_api,
        }


def _classify_by_filename(filename: str) -> Optional[str]:
    """Try to classify a document purely from its filename. Returns None if unsure."""
    if _INVOICE_PATTERNS.search(filename):
        return "invoice"
    if _POD_PATTERNS.search(filename):
        return "pod"
    return None


def _pdf_first_page_to_png(pdf_path: Path) -> Optional[bytes]:
    """Convert the first page of a PDF to a PNG image (for Claude vision)."""
    try:
        doc = fitz.open(str(pdf_path))
        page = doc[0]
        pix = page.get_pixmap(dpi=CLASSIFICATION_DPI)
        png_bytes = pix.tobytes("png")
        doc.close()
        return png_bytes
    except Exception as e:
        logger.error("Failed to convert PDF to image: %s", e)
        return None


def _validate_pdf(pdf_path: Path) -> bool:
    """Check that a file is a valid PDF."""
    try:
        with open(pdf_path, "rb") as f:
            header = f.read(5)
        if header != b"%PDF-":
            return False
        doc = fitz.open(str(pdf_path))
        page_count = len(doc)
        doc.close()
        return page_count > 0
    except Exception:
        return False


class UsageTracker:
    """Tracks daily Claude API usage to enforce cost caps."""

    def __init__(self, usage_file: Path = API_USAGE_FILE) -> None:
        self._file = usage_file
        self._data = self._load()

    def _load(self) -> dict:
        if self._file.exists():
            try:
                with open(self._file) as f:
                    return json.load(f)
            except Exception:
                return {"date": str(date.today()), "calls": 0, "estimated_cost_usd": 0.0}
        return {"date": str(date.today()), "calls": 0, "estimated_cost_usd": 0.0}

    def _save(self) -> None:
        with open(self._file, "w") as f:
            json.dump(self._data, f, indent=2)

    def _reset_if_new_day(self) -> None:
        today = str(date.today())
        if self._data.get("date") != today:
            self._data = {"date": today, "calls": 0, "estimated_cost_usd": 0.0}

    def can_make_call(self) -> bool:
        self._reset_if_new_day()
        return self._data["calls"] < DAILY_API_CALL_LIMIT

    def record_call(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self._reset_if_new_day()
        self._data["calls"] += 1
        # Haiku pricing: $0.80/M input, $4.00/M output (approximate)
        cost = (input_tokens * 0.80 / 1_000_000) + (output_tokens * 4.00 / 1_000_000)
        self._data["estimated_cost_usd"] = round(self._data["estimated_cost_usd"] + cost, 6)
        self._save()
        logger.info(
            "API usage today: %d/%d calls, ~$%.4f",
            self._data["calls"], DAILY_API_CALL_LIMIT, self._data["estimated_cost_usd"],
        )

    @property
    def calls_today(self) -> int:
        self._reset_if_new_day()
        return self._data["calls"]

    @property
    def cost_today(self) -> float:
        self._reset_if_new_day()
        return self._data["estimated_cost_usd"]


class ClaudeClassifier:
    """Classifies logistics documents using Claude's vision capabilities."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        key = api_key or CLAUDE_API_KEY
        if not key:
            raise ValueError("ANTHROPIC_API_KEY not set — cannot classify documents")
        self._client = AsyncAnthropic(api_key=key)
        self._usage = UsageTracker()

    @property
    def usage(self) -> UsageTracker:
        return self._usage

    async def classify(self, pdf_path: Path) -> ClassificationResult:
        """
        Classify a PDF document as invoice, POD, or other.

        Cost-saving order:
          1. Try filename-based classification (FREE)
          2. Check daily API cap
          3. Send first page to Claude as an image (PAID)
        """
        # ── Step 1: Try filename bypass (FREE) ──
        filename_guess = _classify_by_filename(pdf_path.name)
        if filename_guess:
            logger.info(
                "Classified %s → %s by filename (FREE, skipped API call)",
                pdf_path.name, filename_guess,
            )
            return ClassificationResult(
                doc_type=filename_guess,
                confidence=0.90,
                skipped_api=True,
            )

        # ── Step 2: Check daily cap ──
        if not self._usage.can_make_call():
            logger.warning(
                "Daily API call limit reached (%d/%d). Skipping classification for %s",
                self._usage.calls_today, DAILY_API_CALL_LIMIT, pdf_path.name,
            )
            return ClassificationResult(
                doc_type="other",
                confidence=0.0,
                needs_review=True,
                skipped_api=True,
            )

        # ── Step 3: Validate PDF ──
        if not _validate_pdf(pdf_path):
            logger.warning("Invalid or corrupted PDF: %s", pdf_path.name)
            return ClassificationResult(
                doc_type="other",
                confidence=0.0,
                needs_review=True,
            )

        # ── Step 4: Convert first page to image (at lower DPI to save tokens) ──
        png_bytes = _pdf_first_page_to_png(pdf_path)
        if not png_bytes:
            return ClassificationResult(
                doc_type="other",
                confidence=0.0,
                needs_review=True,
            )

        b64_image = base64.standard_b64encode(png_bytes).decode("utf-8")

        try:
            response = await self._client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=200,  # reduced from 300 — response is tiny JSON
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": b64_image,
                                },
                            },
                            {
                                "type": "text",
                                "text": (
                                    "You are a logistics document classifier. Analyze this document image and respond with ONLY a JSON object (no other text):\n\n"
                                    '{"type": "invoice" | "pod" | "bol" | "other", "confidence": 0.0-1.0, "container_number": "extracted number or null"}\n\n'
                                    "Rules:\n"
                                    '- "invoice" = billing document, freight invoice, carrier invoice\n'
                                    '- "pod" = proof of delivery, delivery receipt, signed delivery confirmation\n'
                                    '- "bol" = bill of lading (treat as POD for our purposes)\n'
                                    '- "other" = anything else\n'
                                    "- Extract any container number visible (format like ABCD1234567)\n"
                                    "- Confidence should reflect how certain you are about the classification"
                                ),
                            },
                        ],
                    }
                ],
            )

            # Track usage
            input_tokens = getattr(response.usage, "input_tokens", 0)
            output_tokens = getattr(response.usage, "output_tokens", 0)
            self._usage.record_call(input_tokens, output_tokens)

            raw = response.content[0].text.strip()

            # Handle potential markdown code blocks
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            data = json.loads(raw)
            doc_type = data.get("type", "other").lower()
            confidence = float(data.get("confidence", 0.0))
            container_hint = data.get("container_number")

            # BOL counts as POD
            if doc_type == "bol":
                doc_type = "pod"

            needs_review = confidence < CLASSIFICATION_CONFIDENCE_THRESHOLD

            logger.info(
                "Classified %s → %s (%.0f%% confidence, container hint: %s)",
                pdf_path.name, doc_type, confidence * 100, container_hint,
            )

            return ClassificationResult(
                doc_type=doc_type,
                confidence=confidence,
                container_hint=container_hint,
                needs_review=needs_review,
            )

        except Exception as e:
            logger.error("Claude classification failed for %s: %s", pdf_path.name, e)
            return ClassificationResult(
                doc_type="other",
                confidence=0.0,
                needs_review=True,
            )
