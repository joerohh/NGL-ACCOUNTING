"""TMS API cascade — run_enrich and run_document.

Each function returns (result, error_message_or_None). The data layer wraps
these calls and records failures into FailedRowsTracker on error.
"""

import logging
from pathlib import Path
from typing import Optional, Tuple

from services.tms_data.enriched_invoice import EnrichedInvoice
from services.tms_data.extractors import (
    extract_chassis_from_qbo,
    extract_chassis_from_tms_wo,
    extract_cnee_from_qbo,
    extract_cnee_from_tms_wo,
    extract_container_from_tms_wo,
    extract_do_sender_from_tms_wo,
    extract_document_url_from_tms_wo,
    extract_wo_from_qbo,
)

logger = logging.getLogger("ngl.tms_data.cascade")


async def run_enrich(
    invoice_data: dict,
    tms_api,
    force: bool = False,
) -> Tuple[EnrichedInvoice, Optional[str]]:
    """Build an EnrichedInvoice from QBO data, filling blanks via TMS API.

    Returns (enriched, error). error is non-None only when the TMS API call
    raised (DNS, network, 5xx). 404 is treated as no-data, not an error.

    When force=True, TMS is queried even if QBO already has chassis+CNEE.
    Used by the OEC flow to guarantee do_sender_email is populated, since
    that field lives only in TMS but the QBO-complete short-circuit would
    otherwise skip the call.
    """
    # Step 1: read what QBO already gave us
    wo_no = extract_wo_from_qbo(invoice_data)
    chassis = extract_chassis_from_qbo(invoice_data)
    cnee = extract_cnee_from_qbo(invoice_data)
    container_no = None  # QBO invoices don't carry container# directly
    do_sender_email = None  # QBO custom fields rarely carry this

    sources = {
        "wo_no": "qbo" if wo_no else "missing",
        "container_no": "missing",
        "chassis": "qbo" if chassis else "missing",
        "cnee": "qbo" if cnee else "missing",
        "do_sender_email": "missing",
    }

    # Step 2: short-circuit if QBO already has chassis+CNEE AND caller didn't force.
    # do_sender_email is TMS-only, so non-force callers accept it stays None.
    # OEC callers pass force=True to guarantee do_sender_email is fetched.
    needs_tms = bool(wo_no) and (force or not chassis or not cnee)
    if not needs_tms:
        return EnrichedInvoice(
            wo_no=wo_no, container_no=container_no, chassis=chassis,
            cnee=cnee, do_sender_email=do_sender_email, sources=sources,
        ), None

    # Step 3: call TMS API
    try:
        wo = await tms_api.get_work_order(wo_no)
    except Exception as e:
        logger.warning("TMS API enrich failed for WO %s: %s", wo_no, e)
        return EnrichedInvoice(
            wo_no=wo_no, container_no=container_no, chassis=chassis,
            cnee=cnee, do_sender_email=do_sender_email, sources=sources,
        ), str(e)

    if not wo:
        # 404 / not found is not a failure — just no data to fill in.
        return EnrichedInvoice(
            wo_no=wo_no, container_no=container_no, chassis=chassis,
            cnee=cnee, do_sender_email=do_sender_email, sources=sources,
        ), None

    # Step 4: fill in any blanks from the TMS WO record
    if not chassis:
        v = extract_chassis_from_tms_wo(wo)
        if v:
            chassis = v
            sources["chassis"] = "tms_api"

    if not cnee:
        v = extract_cnee_from_tms_wo(wo)
        if v:
            cnee = v
            sources["cnee"] = "tms_api"

    if not container_no:
        v = extract_container_from_tms_wo(wo)
        if v:
            container_no = v
            sources["container_no"] = "tms_api"

    if not do_sender_email:
        v = extract_do_sender_from_tms_wo(wo)
        if v:
            do_sender_email = v
            sources["do_sender_email"] = "tms_api"

    return EnrichedInvoice(
        wo_no=wo_no, container_no=container_no, chassis=chassis,
        cnee=cnee, do_sender_email=do_sender_email, sources=sources,
    ), None


async def run_document(
    invoice_data: dict,
    doc_type: str,
    dest_dir: Path,
    tms_api,
) -> Tuple[Optional[Path], Optional[str]]:
    """Download one document via TMS API. Returns (path, error).

    error is None when the doc isn't present on the WO (not a failure).
    error is non-None when the TMS API call raised or download failed.
    """
    wo_no = extract_wo_from_qbo(invoice_data)
    if not wo_no:
        return None, "Cannot fetch from TMS API: no WO# on QBO invoice"

    try:
        wo = await tms_api.get_work_order(wo_no)
    except Exception as e:
        logger.warning("TMS API get_work_order failed for %s: %s", wo_no, e)
        return None, str(e)

    if not wo:
        # 404 — WO doesn't exist or API doesn't have it.
        return None, None

    url = extract_document_url_from_tms_wo(wo, doc_type)
    if not url:
        # WO exists but doesn't have this doc type.
        return None, None

    try:
        data = await tms_api.download_document(url)
    except Exception as e:
        logger.warning("TMS API download_document failed for %s: %s", url, e)
        return None, str(e)

    if not data:
        return None, f"Document download returned no data for {doc_type}"

    dest = dest_dir / f"{wo_no}_{doc_type}.pdf"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest, None
