# TMS Integration — Project Plan

## Overview

When the QBO send agent encounters an invoice with **missing attachments** (POD, BOL, etc.), it currently skips the invoice and logs it. This feature will add a new automated step: the agent will **access the company TMS portal via browser automation**, search for the missing documents by container number, **download them**, then **upload them to the corresponding QBO invoice** — making the invoice ready to send.

---

## How It Fits Into the Current Flow

### Current Send Flow (job_manager.py)
```
For each invoice:
  1. Look up customer → get requiredDocs (e.g., ["invoice", "pod"])
  2. Search QBO for invoice by number
  3. Verify invoice details (container #, amount)
  4. Check attachments on QBO invoice page
  5. If zero attachments → SKIP (skipped_no_attachments)        ← NEW HOOK
  6. If required docs missing → SKIP (missing_docs)             ← NEW HOOK
  7. Click "Review and Send" → fill form → send email
```

### New Flow (with TMS integration)
```
For each invoice:
  1. Look up customer → get requiredDocs
  2. Search QBO for invoice by number
  3. Verify invoice details
  4. Check attachments on QBO invoice page
  5. If attachments missing:
     a. ── NEW ── Query TMS portal for missing docs (by container #)
     b. ── NEW ── Download found PDFs from TMS to temp folder
     c. ── NEW ── Navigate back to QBO invoice
     d. ── NEW ── Upload downloaded PDFs as attachments to QBO invoice
     e. ── NEW ── Re-check attachments (verify upload worked)
     f. If still missing → SKIP with detailed report
  6. Click "Review and Send" → fill form → send email
```

---

## Architecture

### New Files
| File | Purpose |
|------|---------|
| `agent/services/tms_browser.py` | TMS portal browser automation (login, search, download) |
| `agent/routers/tms.py` | REST endpoints for TMS status, manual triggers |

### Modified Files
| File | Changes |
|------|---------|
| `agent/services/qbo_browser.py` | Add `upload_attachment_to_invoice()` method |
| `agent/services/job_manager.py` | Insert TMS recovery step between attachment check and skip |
| `agent/config.py` | Add TMS URL, credentials config |
| `agent/main.py` | Register TMS router |
| `app/index.html` | New SSE events display, TMS status indicators |

---

## Feature Breakdown

### Feature 1: TMS Browser Service (`tms_browser.py`)

Mirrors `qbo_browser.py` pattern — Playwright-based browser automation with persistent Chrome profile.

**Methods:**
- `init()` — Launch browser with TMS profile (separate from QBO profile so both can run)
- `is_logged_in()` — Check if TMS session is active
- `open_login_page()` → user manually logs in (same UX as QBO login)
- `wait_for_login()` — Poll for successful login
- `search_container(container_number)` → navigate to container's document page
- `list_available_docs()` → scrape the page for downloadable document links, classify by type (POD, BOL, PL, DO, etc.)
- `download_document(doc_link, download_dir)` → click download link, save PDF to temp folder
- `close()` — Shut down browser

**Key design decisions:**
- Uses a **separate** Playwright persistent context (own `user_data_dir`) so QBO and TMS browsers can be open simultaneously
- Selector-driven (like QBO) with a `tms_selectors.json` config file — easy to update when TMS UI changes
- Debug screenshots saved to `agent/debug/tms/` for troubleshooting
- Document classification reuses the same filename patterns from `qbo_browser.py`

### Feature 2: QBO Attachment Upload (`qbo_browser.py`)

New method: `upload_attachment_to_invoice(file_path: Path) -> bool`

**Flow:**
1. Verify we're on the QBO invoice detail page
2. Scroll to the Attachments section
3. Find the "Attach file" / upload button
4. Use Playwright's `set_input_files()` to upload the PDF via the file input
5. Wait for upload to complete (watch for the new attachment filename to appear)
6. Return True if attachment now visible on page

**Why this is feasible:**
- QBO's attachment upload is a standard HTML file input — Playwright handles this natively
- No API needed — just browser automation like everything else
- The agent is already on the invoice page when it detects missing docs

### Feature 3: TMS Recovery Step (`job_manager.py`)

Insert between the current attachment check (line 649) and the skip logic (line 653).

**Logic:**
```python
# After check_attachments_on_page returns missing docs:
if missing_types and self._tms:  # TMS service is available
    await self._emit_send(job, "tms_fetching", {
        "invoiceNumber": invoice.invoice_number,
        "containerNumber": invoice.container_number,
        "missingDocs": missing_types,
    })

    # Search TMS for this container
    tms_docs = await self._tms.search_container(invoice.container_number)

    # Download matching doc types
    downloaded = []
    for doc_type in missing_types:
        matching = [d for d in tms_docs if d["type"] == doc_type]
        if matching:
            path = await self._tms.download_document(matching[0], temp_dir)
            if path:
                downloaded.append({"type": doc_type, "path": path})

    # Upload to QBO
    for doc in downloaded:
        await self._qbo.upload_attachment_to_invoice(doc["path"])
        await self._emit_send(job, "tms_uploaded", {
            "invoiceNumber": invoice.invoice_number,
            "docType": doc["type"],
            "fileName": doc["path"].name,
        })

    # Re-check attachments after upload
    if downloaded:
        att_check = await self._qbo.check_attachments_on_page(required_docs)
        # Update result with new attachment info
```

### Feature 4: Configuration (`config.py`)

```python
# TMS Settings
TMS_PORTAL_URL = os.getenv("TMS_PORTAL_URL", "")
TMS_PROFILE_DIR = DATA_DIR / "tms_profile"
TMS_DOWNLOADS_DIR = DATA_DIR / "tms_downloads"
TMS_ENABLED = bool(TMS_PORTAL_URL)
```

Plus `tms_selectors.json`:
```json
{
  "login": {
    "logged_in_indicator": "..."
  },
  "search": {
    "container_search_input": "...",
    "search_button": "...",
    "results_table": "..."
  },
  "documents": {
    "document_row": "...",
    "document_name": "...",
    "download_button": "..."
  }
}
```

### Feature 5: TMS Router (`tms.py`)

Endpoints:
- `GET /tms/status` — Check if TMS is configured and browser is logged in
- `POST /tms/login` — Open TMS login page for manual authentication
- `GET /tms/login/wait` — SSE stream waiting for user to complete login
- `POST /tms/search/{container}` — Manual search (for testing)

### Feature 6: Frontend Updates (`index.html`)

**New SSE events to handle:**
| Event | Display |
|-------|---------|
| `tms_fetching` | "Searching TMS for missing docs..." + container # |
| `tms_found` | "Found [POD, BOL] in TMS" |
| `tms_downloading` | "Downloading POD from TMS..." |
| `tms_uploaded` | "Uploaded POD to QBO invoice" |
| `tms_not_found` | "Document not available in TMS" |

**TMS connection status:**
- Add TMS status indicator next to existing QBO status in the agent panel
- "TMS: Connected" / "TMS: Not Connected" / "TMS: Not Configured"
- Login button (same pattern as QBO login flow)

**Send status enhancements:**
- New status: `recovered_from_tms` — invoice had missing docs but TMS provided them
- Status pill: "TMS Recovered" in blue (#2563eb)

---

## Implementation Order

### Phase 1: TMS Browser Service (Core)
1. Create `tms_selectors.json` with placeholders
2. Build `tms_browser.py` — init, login flow, search, download
3. Add TMS config to `config.py`
4. Create `tms.py` router with status/login endpoints
5. Register in `main.py`
6. Add TMS login UI to frontend (connection panel)
7. **Test:** Manual login + container search + document list

### Phase 2: QBO Upload Automation
1. Add `upload_attachment_to_invoice()` to `qbo_browser.py`
2. **Test:** Upload a test PDF to a QBO invoice via browser automation
3. Verify the attachment appears and can be selected for email

### Phase 3: Integration into Send Flow
1. Modify `job_manager.py` — add TMS recovery step
2. Add new SSE events (tms_fetching, tms_found, tms_uploaded, etc.)
3. Update frontend `invHandleSendEvent` to handle new events
4. Add "TMS Recovered" status pill
5. **Test:** Full send flow with an invoice that has missing docs in QBO but available in TMS

### Phase 4: Polish & Edge Cases
1. Handle TMS login expiration mid-job (re-auth prompt)
2. Retry logic for failed TMS downloads
3. Timeout handling (TMS portal slow to respond)
4. Audit logging for TMS actions
5. Summary stats: "X invoices recovered from TMS" in job complete event

---

## Selector Configuration Required

Before Phase 1 can begin, we need to map the TMS portal's DOM structure. I'll need you to:
1. **Show me the TMS portal login page** (screenshot or URL)
2. **Show the container search page** — where you type a container # to find docs
3. **Show the document listing** — where PODs/BOLs appear with download links

From these, I'll build the `tms_selectors.json` config (same approach as `selectors.json` for QBO).

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| TMS portal changes UI | Selector-driven design — update `tms_selectors.json` without code changes |
| TMS session expires mid-job | Detect expiry, pause job, emit `tms_login_required` event, wait for re-auth |
| QBO upload fails | Retry once, then skip invoice with `tms_upload_failed` status |
| Document not in TMS | Skip with `tms_not_found` — same as current `missing_docs` behavior |
| Two browsers competing for focus | Separate Playwright contexts with own profiles, no window focus conflicts |
| TMS rate limiting | Add configurable delay between TMS requests (like `QBO_ACTION_DELAY_S`) |

---

## Summary

This integration adds a **self-healing step** to the invoice send pipeline. Instead of skipping invoices with missing attachments, the agent will:

1. **Detect** what's missing (POD, BOL, etc.)
2. **Fetch** it from the TMS portal automatically
3. **Upload** it to the QBO invoice
4. **Continue** with the send — no manual intervention needed

The architecture mirrors the existing QBO automation pattern (Playwright + selectors + SSE events), keeping the codebase consistent and maintainable.
