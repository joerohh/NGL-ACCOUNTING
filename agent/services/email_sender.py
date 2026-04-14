"""Gmail SMTP email sender — sends emails with attachments."""

import asyncio
import logging
import smtplib
from typing import Optional
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger("ngl.email_sender")


class EmailSender:
    """Send emails via Gmail SMTP (App Password authentication)."""

    def __init__(self, gmail_address: str, app_password: str) -> None:
        self._address = gmail_address
        self._password = app_password

    async def send_pod_email(
        self,
        to: list[str],
        cc: list[str],
        subject: str,
        body: str,
        pod_path: Path,
        reply_to: Optional[str] = None,
    ) -> dict:
        """Send a POD PDF as an email attachment via Gmail SMTP.

        Returns: { sent: bool, error: str|None }
        """
        if not to:
            return {"sent": False, "error": "No recipients specified"}

        if not pod_path.exists():
            return {"sent": False, "error": f"POD file not found: {pod_path}"}

        try:
            result = await asyncio.to_thread(
                self._send_sync, to, cc, subject, body, pod_path, reply_to
            )
            return result
        except Exception as e:
            logger.error("Failed to send POD email: %s", e)
            return {"sent": False, "error": str(e)}

    async def send_invoice_email(
        self,
        to: list[str],
        cc: list[str] = None,
        bcc: list[str] = None,
        subject: str = "",
        body: str = "",
        attachments: list[dict] = None,
        reply_to: Optional[str] = None,
    ) -> dict:
        """Send an invoice email with multiple attachments via Gmail SMTP.

        attachments: list of {filename: str, data: bytes}
        Returns: { sent: bool, error: str|None }
        """
        if not to:
            return {"sent": False, "error": "No recipients specified"}

        try:
            result = await asyncio.to_thread(
                self._send_invoice_sync, to, cc or [], bcc or [],
                subject, body, attachments or [], reply_to
            )
            return result
        except Exception as e:
            logger.error("Failed to send invoice email: %s", e)
            return {"sent": False, "error": str(e)}

    def _send_sync(
        self,
        to: list[str],
        cc: list[str],
        subject: str,
        body: str,
        pod_path: Path,
        reply_to: Optional[str],
    ) -> dict:
        """Synchronous email send (runs in thread pool)."""
        msg = MIMEMultipart()
        msg["From"] = self._address
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = subject
        if reply_to:
            msg["Reply-To"] = reply_to

        # Body
        msg.attach(MIMEText(body, "plain"))

        # Attach POD PDF
        with open(pod_path, "rb") as f:
            pdf_part = MIMEApplication(f.read(), _subtype="pdf")
            pdf_part.add_header(
                "Content-Disposition", "attachment", filename=pod_path.name
            )
            msg.attach(pdf_part)

        # All recipients (To + CC), deduplicated to prevent double delivery
        all_recipients = list(dict.fromkeys(list(to) + list(cc)))

        # Send via Gmail SMTP
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(self._address, self._password)
            server.send_message(msg, self._address, all_recipients)

        logger.info(
            "POD email sent: to=%s, cc=%s, subject=%s, attachment=%s",
            to, cc, subject, pod_path.name,
        )
        return {"sent": True, "error": None}

    def _send_invoice_sync(
        self,
        to: list[str],
        cc: list[str],
        bcc: list[str],
        subject: str,
        body: str,
        attachments: list[dict],
        reply_to: Optional[str],
    ) -> dict:
        """Send invoice email with multiple attachments (runs in thread pool)."""
        msg = MIMEMultipart("mixed")
        msg["From"] = self._address
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = subject
        if reply_to:
            msg["Reply-To"] = reply_to

        # For HTML emails with inline images, use related + alternative structure
        is_html = body.lstrip().lower().startswith(("<!doctype", "<html"))
        if is_html:
            related = MIMEMultipart("related")
            related.attach(MIMEText(body, "html"))

            # Embed NGL logo as inline CID attachment
            from services.email_template import LOGO_PATH
            if LOGO_PATH.exists():
                with open(LOGO_PATH, "rb") as f:
                    logo = MIMEImage(f.read(), _subtype="jpeg")
                    logo.add_header("Content-ID", "<ngl_logo>")
                    logo.add_header("Content-Disposition", "inline", filename="ngl_logo.jpg")
                    related.attach(logo)

            msg.attach(related)
        else:
            msg.attach(MIMEText(body, "plain"))

        # Attach files (raw bytes)
        for att in attachments:
            filename = att["filename"]
            data = att["data"]
            subtype = "pdf" if filename.lower().endswith(".pdf") else "octet-stream"
            part = MIMEApplication(data, _subtype=subtype)
            part.add_header("Content-Disposition", "attachment", filename=filename)
            msg.attach(part)

        # All recipients (To + CC + BCC), deduplicated to prevent double delivery
        all_recipients = list(dict.fromkeys(list(to) + list(cc) + list(bcc)))

        # Send via Gmail SMTP
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(self._address, self._password)
            server.send_message(msg, self._address, all_recipients)

        logger.info(
            "Invoice email sent: to=%s, cc=%s, bcc=%s, subject=%s, attachments=%d",
            to, cc, bcc, subject, len(attachments),
        )
        return {"sent": True, "error": None}
