## v2.69.0 — Customer Manager cleanup

This release simplifies how customers are configured and silently fixes two customers (APEXMA01, TOPTRA02) whose setup was broken-by-design since the QBO API migration.

### What's new

- **Send Method dropdown simplified to three options.** Standard QBO Email · QBO Invoice Only + POD Email · **Custom (new)**. Portal Upload is hidden from the dropdown — its backend code is preserved unchanged, so the option is a one-line restoration whenever you need it back.

- **Custom send method** replaces the old "Send All / Require Specific" toggle. Pick Custom, then tick exactly which docs go out (Invoice · POD · POL · BOL · PL · DO) plus the "either/or" linker. Tick only Invoice for an invoice-only sender. No more nested setting confusion — one dropdown decides everything.

- **The Invoice checkbox works again.** It used to fail silently — the gate looked for an `Invoice` attachment in QuickBooks, never found one (the invoice is always emailed by QBO directly, not stored as an attachment), and blocked the send forever. The new logic recognizes that the invoice PDF is always sent automatically, so ticking it is an inclusion signal rather than an unsatisfiable requirement. APEXMA01 and TOPTRA02 are unblocked the moment the agent starts.

- **Live duplicate customer-code warning.** Type a code that already exists and a red warning appears under the input within a second, with a "View existing →" button that closes the modal and re-opens it on that customer. Save is blocked while the warning is showing — no more silent overwrites.

- **One-time data migration.** On first launch after upgrading, the agent scans every customer who has `invoice` listed in their required-docs list, moves them onto the new Custom send method, and preserves their docs list. Idempotent — subsequent launches log nothing. APEXMA01 and TOPTRA02 will both come up correctly configured the first time you open them.

### Bug fixes caught during review

- **Custom send method now persists end-to-end** through the API layer. An earlier draft of the validator only whitelisted four send methods, so any save with `sendMethod=custom` was silently coerced back to `email`. The whitelist now includes `custom`.
- **Custom's doc-picker selections are saved.** An earlier draft of the save handler hardcoded an empty doc list for any non-Standard method. Custom mode's ticked boxes now actually persist to the database.
- **Legacy Portal Upload customers are preserved.** When you open a customer whose send method is Portal Upload (e.g. TRV), the dropdown now dynamically adds a "Portal Upload (legacy)" option so the setting round-trips cleanly. Without this, your first edit of any portal customer would have silently reverted them to email.

### Backend changes

- Required-docs gate filter at `agent/services/job_manager/send_qbo_api.py:192` strips `invoice` (case-insensitive) before the QuickBooks attachment check.
- New `migrate_invoice_to_custom()` runs once per agent startup, idempotent, wrapped in try/except so a Supabase outage on startup doesn't block the app.
- New send method `custom` is added to the API whitelist (`agent/routers/customers.py`).
- 21 new pytest tests cover the gate filter (7), migration (8), dispatcher contract (2), and router round-trip (4).

### What didn't change

- OEC two-email flow logic — untouched.
- Send dispatcher routing — `custom` falls through the existing else branch to `_send_qbo_api`, exactly like `email`. Same end-to-end path; only the gate filter differs.
- Portal Upload backend (`send_portal.py`, `PortalUploader`, the dispatcher branch) — preserved verbatim.

### Coming next

- **v2.70** — Pydantic validation on customer create/update + CSV import error UX (Fix 4 from the original Invoice Sender UX overhaul).
- **v2.71** — Combined Results HUD redesign for Invoice Sender — replaces the three stacked result surfaces with one unified HUD above the Status Log.
