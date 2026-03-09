"""Gmail SMTP email sender — sends POD emails for the OEC flow."""

import asyncio
import logging
import smtplib
from typing import Optional
from email.mime.application import MIMEApplication
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

        # All recipients (To + CC)
        all_recipients = list(to) + list(cc)

        # Send via Gmail SMTP
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(self._address, self._password)
            server.sendmail(self._address, all_recipients, msg.as_string())

        logger.info(
            "POD email sent: to=%s, cc=%s, subject=%s, attachment=%s",
            to, cc, subject, pod_path.name,
        )
        return {"sent": True, "error": None}
