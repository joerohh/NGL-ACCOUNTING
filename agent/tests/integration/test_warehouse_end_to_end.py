"""Warehouse end-to-end — requires live QBO + Excel.

Scripted version of scratch/warehouse_full_run.py. Exercises the full path:
routing decision → list_attachments → download_attachment (with retry) →
Excel COM conversion → output structure suitable for FetchResult.

Skipped by default — runs before each release."""

from pathlib import Path

import pytest

from services.excel_converter import _check_excel_available


WAREHOUSE_INV = "LW260515P01"      # real warehouse invoice from POC
WAREHOUSE_TXN_ID = "391101"        # real QBO txnId


@pytest.mark.requires_excel
@pytest.mark.requires_qbo
@pytest.mark.asyncio
async def test_warehouse_fetch_end_to_end(tmp_path: Path):
    """Verify the agent path that the merge tool will exercise for warehouse rows.

    Asserts the fetch result contains:
      - at least one attachment downloaded
      - xlsx attachments produce a non-zero-byte PDF after conversion
      - no failures for this known-good invoice
    """
    assert _check_excel_available(), "Excel COM not available — cannot run."

    # Inline imports so collecting this file doesn't fail in CI envs without QBO
    from services.qbo_api import QBOApiClient
    from services.excel_converter import ExcelSession

    api = QBOApiClient()

    invoice = await api.search_invoice(WAREHOUSE_INV)
    assert invoice is not None, f"Could not find {WAREHOUSE_INV} in QBO — is OAuth still valid?"
    invoice_id = invoice["Id"]

    attachments = await api.list_attachments(invoice_id)
    assert len(attachments) >= 1, "Expected at least one attachment on POC invoice"

    successes = []
    failures = []
    async with ExcelSession() as session:
        for att in attachments:
            fname = att["fileName"]
            lower = fname.lower()
            path = await api.download_attachment(
                att["id"], fname, tmp_path,
                temp_download_uri=att.get("tempDownloadUri"),
            )
            assert path is not None, f"Download failed for {fname}"

            if lower.endswith((".xlsx", ".xls", ".xlsm")):
                pdf = path.with_suffix(".pdf")
                result = await session.convert(path, pdf)
                if result.ok:
                    assert pdf.exists()
                    assert pdf.stat().st_size > 10_000
                    successes.append(pdf.name)
                else:
                    failures.append({"file": fname, "reason": result.error})
            elif lower.endswith(".pdf"):
                successes.append(path.name)
            else:
                failures.append({"file": fname, "reason": "unsupported"})

    assert len(successes) >= 1, "No attachments succeeded"
    assert len(failures) == 0, f"Unexpected failures: {failures}"
