"""Explicit TMS browser path — invoked only when user clicks 'Retry (Browser)'.

This module mirrors the shape of cascade.py so the data layer can swap between
them based on the `source` parameter passed by callers.

Note: the methods called on the browser (`fetch_detail_info`, `fetch_doc_by_wo`,
`bc_detail_type_segment`) already exist or are thin shims around existing methods.
"""

import logging
from pathlib import Path
from typing import Optional, Tuple

from services.tms_data.enriched_invoice import EnrichedInvoice
from services.tms_data.extractors import (
    extract_chassis_from_qbo,
    extract_cnee_from_qbo,
    extract_wo_from_qbo,
)

logger = logging.getLogger("ngl.tms_data.browser_path")


async def run_enrich_browser(
    invoice_data: dict,
    tms_browser,
) -> Tuple[EnrichedInvoice, Optional[str]]:
    """Browser-driven enrichment. Used only when the user explicitly opts in."""
    wo_no = extract_wo_from_qbo(invoice_data)
    chassis = extract_chassis_from_qbo(invoice_data)
    cnee = extract_cnee_from_qbo(invoice_data)
    container_no = None
    do_sender_email = None

    sources = {
        "wo_no": "qbo" if wo_no else "missing",
        "container_no": "missing",
        "chassis": "qbo" if chassis else "missing",
        "cnee": "qbo" if cnee else "missing",
        "do_sender_email": "missing",
    }

    if not wo_no:
        return EnrichedInvoice(
            wo_no=wo_no, container_no=container_no, chassis=chassis,
            cnee=cnee, do_sender_email=do_sender_email, sources=sources,
        ), "Cannot fetch from TMS browser: no WO# on QBO invoice"

    try:
        details = await tms_browser.fetch_detail_info(wo_no)
    except Exception as e:
        logger.warning("TMS browser fetch_detail_info failed for %s: %s", wo_no, e)
        return EnrichedInvoice(
            wo_no=wo_no, container_no=container_no, chassis=chassis,
            cnee=cnee, do_sender_email=do_sender_email, sources=sources,
        ), str(e)

    if not isinstance(details, dict):
        return EnrichedInvoice(
            wo_no=wo_no, container_no=container_no, chassis=chassis,
            cnee=cnee, do_sender_email=do_sender_email, sources=sources,
        ), None

    if not chassis and details.get("chassis"):
        chassis = details["chassis"]
        sources["chassis"] = "tms_browser"
    if not cnee and details.get("cnee"):
        cnee = details["cnee"]
        sources["cnee"] = "tms_browser"
    if details.get("container_no"):
        container_no = details["container_no"]
        sources["container_no"] = "tms_browser"
    if details.get("do_sender_email"):
        do_sender_email = details["do_sender_email"]
        sources["do_sender_email"] = "tms_browser"

    return EnrichedInvoice(
        wo_no=wo_no, container_no=container_no, chassis=chassis,
        cnee=cnee, do_sender_email=do_sender_email, sources=sources,
    ), None


async def run_document_browser(
    invoice_data: dict,
    doc_type: str,
    dest_dir: Path,
    tms_browser,
    invoice_number: str,
) -> Tuple[Optional[Path], Optional[str]]:
    """Browser-driven document fetch using the existing direct-URL fetcher."""
    wo_no = extract_wo_from_qbo(invoice_data)
    if not wo_no:
        return None, "Cannot fetch from TMS browser: no WO# on QBO invoice"

    try:
        detail_type = tms_browser.bc_detail_type_segment(invoice_number)
    except Exception as e:
        logger.warning("Failed to derive detail_type for %s: %s", invoice_number, e)
        detail_type = None

    if not detail_type:
        return None, "Cannot derive TMS detail-type segment from invoice number"

    try:
        path = await tms_browser.fetch_doc_by_wo(
            wo_no, detail_type, doc_type, "", invoice_number, dest_dir,
        )
    except Exception as e:
        logger.warning("TMS browser fetch_doc_by_wo failed for %s/%s: %s",
                       wo_no, doc_type, e)
        return None, str(e)

    return path, None
