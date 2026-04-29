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


async def run_enrich(invoice_data: dict, tms_api) -> Tuple[EnrichedInvoice, Optional[str]]:
    """Build an EnrichedInvoice from QBO data, filling blanks via TMS API.

    Returns (enriched, error). error is non-None only when the TMS API call
    raised (DNS, network, 5xx). 404 is treated as no-data, not an error.
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

    # Step 2: short-circuit if QBO already has the QBO-fillable fields
    # (container_no / do_sender_email are TMS-only, so we don't gate on them —
    # otherwise we'd always call TMS when wo_no is present).
    needs_tms = (
        wo_no  # can only call TMS if we know the WO#
        and (not chassis or not cnee)
    )
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
