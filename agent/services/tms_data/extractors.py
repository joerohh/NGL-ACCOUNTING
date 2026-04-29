"""Pure field extractors for QBO invoices and TMS WO records.

QBO extractors delegate to existing helpers in the codebase to avoid duplication.
TMS WO extractors live here because the TMS API client only exposes a subset.
"""

from typing import Optional

from services.job_manager.util import extract_wo_from_invoice
from services.qbo_api.invoices import QBOInvoicesMixin


# Single shared instance — the chassis/CNEE methods are pure (no I/O, no state).
_qbo_helper = QBOInvoicesMixin()


# ── QBO invoice extractors ──────────────────────────────────────────

def extract_wo_from_qbo(invoice_data) -> Optional[str]:
    """Pull the WO# from a QBO invoice's NGL REF# custom field."""
    return extract_wo_from_invoice(invoice_data)


def extract_chassis_from_qbo(invoice_data) -> Optional[str]:
    """Pull chassis number from a QBO invoice (custom field)."""
    if not isinstance(invoice_data, dict):
        return None
    try:
        return _qbo_helper._extract_chassis(invoice_data)
    except Exception:
        return None


def extract_cnee_from_qbo(invoice_data) -> Optional[str]:
    """Pull CNEE from QBO invoice CustomerMemo (arrow-chain routing)."""
    if not isinstance(invoice_data, dict):
        return None
    try:
        return _qbo_helper._extract_cnee(invoice_data)
    except Exception:
        return None


# ── TMS WO record extractors ────────────────────────────────────────

def extract_chassis_from_tms_wo(wo) -> Optional[str]:
    """Pull chassis from a TMS WO record. Tries multiple field-name candidates."""
    if not isinstance(wo, dict):
        return None
    for key in ("chassis_no", "chassis", "chassis_number"):
        v = wo.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def extract_cnee_from_tms_wo(wo) -> Optional[str]:
    """Pull consignee/billto from a TMS WO record."""
    if not isinstance(wo, dict):
        return None
    for key in ("billto", "bill_to", "consignee", "cnee"):
        v = wo.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def extract_container_from_tms_wo(wo) -> Optional[str]:
    """Pull container number from a TMS WO record."""
    if not isinstance(wo, dict):
        return None
    v = wo.get("container_no")
    return v.strip() if isinstance(v, str) and v.strip() else None


def extract_do_sender_from_tms_wo(wo) -> Optional[str]:
    """Pull D/O sender email from a TMS WO record (first email in do_sender array)."""
    if not isinstance(wo, dict):
        return None
    senders = wo.get("do_sender") or []
    if not isinstance(senders, list):
        return None
    for s in senders:
        if isinstance(s, str) and "@" in s:
            return s.strip()
    return None


def extract_document_url_from_tms_wo(wo, doc_type: str) -> Optional[str]:
    """Pull the file_url for a given doc type from a TMS WO record (case-insensitive)."""
    if not isinstance(wo, dict) or not doc_type:
        return None
    target = doc_type.upper()
    for doc in wo.get("documents") or []:
        if not isinstance(doc, dict):
            continue
        t = (doc.get("type_") or "").upper()
        if t == target and doc.get("file_url"):
            return doc["file_url"]
    return None
