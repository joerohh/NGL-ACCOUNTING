"""Warehouse invoice send mixin — QBO-only, xlsx-as-is, no TMS, no doc rules."""

import logging
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger("ngl.job_manager")


class SendWarehouseMixin:
    """Send invoices whose INV# position-2 letter is 'W'."""

    async def _send_warehouse_invoice(self, job, invoice, customer, result, i):
        """Fetch invoice PDF + every QBO attachment; send via Gmail SMTP.

        Blocks the send if QBO has zero non-invoice attachments.
        """
        raise NotImplementedError("filled in by Task 4")
