"""HTML email template for invoice emails — matches QBO invoice email style."""

import logging
from pathlib import Path

logger = logging.getLogger("ngl.email_template")

# Path to logo file (attached inline as CID by email sender)
LOGO_PATH = Path(__file__).resolve().parent.parent.parent / "docs" / "screenshots" / "ngl trans logo.jpg"
if not LOGO_PATH.exists():
    logger.warning("NGL logo not found at %s", LOGO_PATH)


def build_invoice_email_html(
    invoice_number: str,
    container: str,
    customer_name: str,
    amount: str,
    due_date: str = "",
    ngl_ref: str = "",
    customer_ref: str = "",
) -> str:
    """Build an HTML email body that matches the QBO invoice email style."""

    # Build reference line — container number only
    ref_line = f"&gt;&gt;&gt; {container}"

    # Format amount
    try:
        amount_display = f"${float(amount):,.2f}"
    except (ValueError, TypeError):
        amount_display = f"${amount}" if amount else ""

    # Due date display
    due_display = ""
    if due_date:
        # Format YYYY-MM-DD → MM/DD/YYYY
        try:
            parts = due_date.split("-")
            if len(parts) == 3:
                due_display = f"DUE {parts[1]}/{parts[2]}/{parts[0]}"
            else:
                due_display = f"DUE {due_date}"
        except Exception:
            due_display = f"DUE {due_date}"

    # Use CID reference — the email sender attaches the logo inline
    if LOGO_PATH.exists():
        logo_img = '<img src="cid:ngl_logo" alt="NGL Transportation" style="max-width:220px; height:auto;" />'
    else:
        logo_img = '<span style="font-size:24px; font-weight:bold; color:#1a2744;">NGL TRANSPORTATION</span>'

    # "Print or save" button — links to the attached invoice PDF via CID
    print_btn = '<a href="cid:invoice_pdf" target="_blank" style="display:inline-block; background:#2e7d32; color:#ffffff; padding:10px 32px; border-radius:4px; font-size:14px; font-weight:600; text-decoration:none; letter-spacing:0.3px;">Print or save</a>'

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8" /></head>
<body style="margin:0; padding:0; background:#ffffff; font-family: Arial, Helvetica, sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff; padding:30px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff; border-radius:0; overflow:hidden;">

  <!-- Header: Invoice number -->
  <tr>
    <td style="padding:28px 40px 12px; text-align:center;">
      <span style="font-size:12px; color:#888888; letter-spacing:1.5px; text-transform:uppercase;">INVOICE {invoice_number}</span>
    </td>
  </tr>

  <!-- Logo -->
  <tr>
    <td style="padding:8px 40px 8px; text-align:center;">
      {logo_img}
    </td>
  </tr>

  <!-- Company name -->
  <tr>
    <td style="padding:4px 40px 20px; text-align:center;">
      <span style="font-size:15px; color:#ea580c; font-weight:600;">NGL Transportation, Inc.</span>
    </td>
  </tr>

  <!-- Amount box — peach/tan background matching QBO style -->
  <tr>
    <td style="padding:0 30px 24px;">
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#fde8cd; border-radius:6px;">
        <tr>
          <td style="padding:28px 20px; text-align:center;">
            {f'<div style="font-size:13px; color:#6b7280; margin-bottom:10px; font-weight:500;">{due_display}</div>' if due_display else ''}
            <div style="font-size:36px; font-weight:700; color:#1a1a1a; letter-spacing:-0.5px;">{amount_display}</div>
            <div style="margin-top:16px;">
              {print_btn}
            </div>
            <div style="font-size:11px; color:#9ca3af; margin-top:10px;">Powered by QuickBooks</div>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- Message body -->
  <tr>
    <td style="padding:0 40px 20px;">
      <div style="font-size:14px; color:#333333; line-height:1.8;">
        <p style="margin:0 0 14px;">Dear {customer_name},</p>
        <p style="margin:0 0 14px;">Attached is the invoice for the load or container referenced in the subject line.</p>
        <p style="margin:0 0 14px; font-weight:600; color:#111111;">{ref_line}</p>
        <p style="margin:0 0 14px;">Please confirm receipt and contact us if you have any questions.<br/>Have a great day.</p>
      </div>
    </td>
  </tr>

  <!-- Payment info -->
  <tr>
    <td style="padding:0 40px 28px;">
      <div style="font-size:13px; color:#444444; line-height:1.8;">
        <p style="margin:0 0 10px; font-weight:600;">* Payment by Mail :</p>
        <p style="margin:0 0 16px; padding-left:8px;">
          TAB Bank on Account of NGL Transportation<br/>
          P.O. Box 150433<br/>
          Ogden, UT 84415-0451
        </p>
        <p style="margin:0 0 10px; font-weight:600;">* Wire / ACH payment should be directed to :</p>
        <p style="margin:0 0 0; padding-left:8px;">
          Account # : 300301609<br/>
          Routing # : 124384657<br/>
          REFERENCE : NGL TRANSPORTATION<br/>
          TAB Bank<br/>
          Address : 4185 Harrison Blvd., Ogden, UT 84403<br/>
          Phone : 801-824-5000
        </p>
      </div>
    </td>
  </tr>

  <!-- Divider -->
  <tr>
    <td style="padding:0 40px;">
      <hr style="border:none; border-top:1px solid #e0e0e0; margin:0;" />
    </td>
  </tr>

  <!-- Footer -->
  <tr>
    <td style="padding:20px 40px 8px; text-align:center;">
      <span style="font-size:13px; font-weight:600; color:#111111;">NGL Transportation, Inc.</span>
    </td>
  </tr>
  <tr>
    <td style="padding:0 40px 6px; text-align:center;">
      <span style="font-size:12px; color:#777777;">6802 W Grant St Phoenix, AZ 85043-4400</span>
    </td>
  </tr>
  <tr>
    <td style="padding:0 40px 20px; text-align:center;">
      <a href="mailto:ar@ngltrans.net" style="font-size:12px; color:#2563eb; text-decoration:none;">ar@ngltrans.net</a>
      <span style="color:#cccccc; margin:0 8px;">|</span>
      <a href="https://ngltrans.com/" style="font-size:12px; color:#2563eb; text-decoration:none;">https://ngltrans.com/</a>
    </td>
  </tr>

  <!-- Fraud warning -->
  <tr>
    <td style="padding:0 40px 24px; text-align:center;">
      <span style="font-size:11px; color:#999999; font-style:italic;">
        If you receive an email that seems fraudulent, please check with the business owner before paying.
      </span>
    </td>
  </tr>

</table>
</td></tr>
</table>
</body>
</html>"""
