"""Tests for ClaudeClassifier image support (JPG/PNG/HEIC via vision API)."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Add agent/ to path so imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.claude_classifier import (
    ClaudeClassifier,
    ClassificationResult,
    _validate_image,
    _validate_pdf,
)


# Minimal valid file headers for each format
_MINIMAL_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00" + b"\x08" * 64
    + b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    + b"\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    + b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xd2\xcf \xff\xd9"
)

_MINIMAL_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01\x5b\x9c\x02\xc7"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)

_MINIMAL_PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\nxref\n0 1\n0000000000 65535 f \ntrailer\n<<>>\n%%EOF"


@pytest.fixture
def jpg_file(tmp_path):
    p = tmp_path / "scan_001.jpg"
    p.write_bytes(_MINIMAL_JPEG)
    return p


@pytest.fixture
def png_file(tmp_path):
    p = tmp_path / "scan_002.png"
    p.write_bytes(_MINIMAL_PNG)
    return p


@pytest.fixture
def pdf_file(tmp_path):
    # Need a real, openable PDF for _validate_pdf (uses fitz).
    # Use fitz to build a minimal one-page PDF.
    import fitz
    p = tmp_path / "scan_003.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(p))
    doc.close()
    return p


def _make_classifier():
    """Construct a ClaudeClassifier with a dummy API key (no network)."""
    return ClaudeClassifier(api_key="test-key-not-used")


def test_jpg_passes_validation(jpg_file):
    assert _validate_image(jpg_file) is True


def test_png_passes_validation(png_file):
    assert _validate_image(png_file) is True


def test_pdf_still_passes_validation(pdf_file):
    assert _validate_pdf(pdf_file) is True


@pytest.mark.asyncio
async def test_classify_routes_jpg_to_vision_api(jpg_file):
    """A .jpg file with a neutral name should be routed to _call_claude_vision."""
    clf = _make_classifier()

    fake_result = ClassificationResult(
        doc_type="pod",
        confidence=0.92,
        container_hint="ABCD1234567",
        needs_review=False,
    )

    with patch.object(
        clf, "_call_claude_vision", new=AsyncMock(return_value=fake_result)
    ) as mock_vision:
        result = await clf.classify(jpg_file)

    mock_vision.assert_awaited_once()
    assert isinstance(result, ClassificationResult)
    assert result.doc_type == "pod"
    assert result.confidence == 0.92
    assert result.container_hint == "ABCD1234567"
