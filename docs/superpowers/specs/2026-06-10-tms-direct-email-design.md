# TMS-Direct Email for Standard Invoice Sends

**Status:** Design approved 2026-06-10
**Triggering incident:** PROMAR01 invoice `LM26060242F` (container `TEMU7671557`) sent 2026-06-08 21:44:23 UTC without a POD, despite TMS having the POD on file since 2026-06-06.

## 1. Problem

The standard email send path (`_send_qbo_api`) does this today:

1. List existing QBO attachments and classify them by filename regex.
2. Cascade: download every supporting doc from TMS → upload missing types to QBO.
3. Re-list QBO attachments.
4. Download invoice PDF + all attachments from QBO.
5. Email everything via Gmail SMTP.

That design has four compounding weaknesses, all of which fired together on 6/8:

- **No retry on `upload_attachment`** (`agent/services/qbo_api/attachments.py:191`). One transient QBO 5xx or `ReadTimeout` and the doc is gone.
- **Silent failure surface.** The cascade emits a `doc_upload_failed` SSE event to the live UI but writes nothing to the audit log. Once the live UI closes, there is no record.
- **Filename classifier is inconsistent.** `DOC_PATTERNS["pod"]` matches `_pod` anywhere; `DOC_PATTERNS["do"]` and `["invoice"]` require a literal period (`_do\.`, `_it\.`). TMS-generated filenames look like `lm<wo>_<type>_<ms-timestamp>.pdf` — the period rule never fires. Result: a DO sitting in QBO classifies as `"other"`, so the skip-list doesn't recognize it, so the cascade tries to upload a duplicate. The duplicate upload only didn't happen on 6/8 because the upload was *also* failing silently — the two bugs masked each other.
- **`required_docs` gate only fires when the array is non-empty.** PROMAR01 had `required_docs: []`. The missing-POD gate at `send_qbo_api.py:264` was a no-op. The audit row read `status: sent` with `attachments_missing: []`.

Audit log review confirms this wasn't isolated to PROMAR01 — five other unrelated customers had the same `POD missing` pattern in the 21:43–21:47 UTC window, then it self-corrected. Transient infrastructure blip, but the code gave it no resistance and left no trace.

## 2. Decision

For the **standard email send path only**, stop mirroring TMS docs into QBO. Attach TMS docs directly to the Gmail message instead.

- QBO becomes the source of truth for the invoice PDF (unchanged).
- TMS becomes the only source for supporting docs (POD, DO, BL, IT, ITE, etc.) at email time.
- The upload-to-QBO step disappears, along with its silent failure mode.
- Custom one-off docs continue to flow through the existing sidebar workflow — outside this code path.

The OEC, warehouse, and portal-upload send paths are unchanged.

## 3. New flow (standard email path)

```
1. Search QBO for the invoice (unchanged) → invoice_id, customer info, ref fields
2. Verify invoice details (unchanged) → container#, amount
3. Fetch every TMS doc with a file_url for this WO  ← new
4. If customer requires POD and TMS returned no POD → hold send, status=pod_missing  ← new
5. If TMS unreachable after retries → hold send, status=tms_unreachable  ← new
6. Download QBO invoice PDF
7. Email = invoice PDF + every TMS doc
8. Send via Gmail SMTP
9. Audit row records attachments_emailed = [doc types that went out]
```

Steps removed from the old flow: list QBO attachments, dedup, cascade-upload, re-list, download QBO attachments.

## 4. Behavior details

### 4.1 TMS retry policy

Wrap both `tms_api.get_work_order` and `tms_api.download_document` in the same retry policy already used for downloads: **3 attempts, 1s and 3s backoff between them**, 30s per-attempt timeout for `get_work_order`, 60s for `download_document`. Transient errors only (`ConnectError`, `ConnectTimeout`, `ReadTimeout`, `RemoteProtocolError`). 4xx/5xx are permanent — no retry.

If all 3 attempts fail:

- Send is **held** (no email goes out).
- Audit row: `status = tms_unreachable`, `error = "TMS unreachable after 3 attempts: <last error>"`.
- Row appears in the results UI under a "Needs retry" group with a one-click retry button.

### 4.2 POD-required gate

Use the existing `customer.required_docs` array as the gate. Behavior:

- If `'pod' in customer.required_docs` AND no doc with `type_ == "POD"` and a usable `file_url` came back from TMS → **hold send**.
- Audit row: `status = pod_missing`, `error = "POD not yet available on TMS for WO <wo_no>"`.
- Row appears in the "Needs retry" group. User retries when TMS catches up (typically after a checkpoint/customs clearance).

If `'pod'` is not in `required_docs`, the send proceeds without a POD. Other doc types in `required_docs` (BOL, IT, etc.) are **advisory only** under the new model — they do not gate the send. Rationale: only the POD has a real customer-facing impact that's worth holding an invoice over; everything else is informational, and the per-customer array was over-engineered for a problem that has one real answer.

No schema migration. The `required_docs` array stays in the customer record exactly as it is.

### 4.3 Audit log

- Rename `attachments_found` → `attachments_emailed`. The values are now doc types lowercase (`pod`, `do`, `bl`, `it`, `ite`), pulled from the TMS `documents[].type_` field directly — no filename regex. Supabase column rename + FastAPI response key change + UI key update all ship in the same release. Single-user packaged app — no transition aliasing needed.
- The standard-email path writes `attachments_missing = []` (empty list, never populated). Its job is now done by the dedicated `pod_missing` status. The column stays in the schema for the warehouse/OEC paths, which still populate it.
- New possible statuses on a standard-email row: `tms_unreachable`, `pod_missing` (in addition to the existing `sent`, `error`, `skipped`, `mismatch`).

### 4.4 OEC, warehouse, portal — unchanged

- **OEC** (`qbo_invoice_only_then_pod_email`) already pulls POD from TMS directly into a separate D/O email. The standard-email change brings the rest of the customers up to OEC's existing pattern. No code change to `send_oec.py`.
- **Warehouse** (any invoice with `W` at position 2 of the INV#) routes to `send_warehouse.py`, which pulls everything from QBO because warehouse customers have no TMS WO. Unchanged.
- **Portal** (`sendMethod ∈ {portal_upload, portal}`) routes to `send_portal.py`. Unchanged.

### 4.5 Re-sending a historical invoice

The new flow pulls fresh from TMS every time. For an invoice from May or earlier, this means:

- If TMS still has the docs (verified the May 28 WO still has all its docs intact on 2026-06-10) → the customer gets the current TMS docs, which is the right answer.
- If TMS purged a doc → the customer gets whatever TMS currently has + the invoice PDF. The old QBO copy is not used.

Existing QBO attachments sitting on historical invoices are not deleted. They become a paper trail. New sends don't add to the pile.

## 5. Code scope

### 5.1 Changes

- `agent/services/job_manager/send_qbo_api.py` — rewrite `_send_qbo_api` to follow the new 9-step flow. Delete the call to `_tms_fetch_and_upload_missing_docs`. Delete the calls to `api.check_attachments`, `api.list_attachments`, and `_dedup_and_emit` from this path.
- `agent/services/tms_api.py` — extend the retry policy to wrap `get_work_order` (download already has it).
- `agent/services/database.py` and Supabase `audit_log` table — rename column `attachments_found` → `attachments_emailed`. Single-release rename (Supabase + FastAPI + UI all updated together).
- `agent/services/job_manager/send_result.py` (or wherever `SendResult.status` is defined) — add `tms_unreachable` and `pod_missing` to the allowed status set.
- Frontend results UI (`app/assets/js/tools/invoice-sender/`) — add the "Needs retry" group and per-row retry button. Render the new statuses with plain-English copy per the existing UX feedback ("TMS unreachable — retry when connection returns", "POD not yet on TMS — retry after checkpoint clears").

### 5.2 Preserved (intentional dead code)

These stay defined and tested but are no longer called from the standard email path:

- `_tms_fetch_and_upload_missing_docs` in `send_qbo_api.py`
- `classify_attachment` and `DOC_PATTERNS` in `agent/services/qbo_api/attachments.py`
- `dedupe_attachments` and the TMS-008 workaround in `agent/services/qbo_api/dedup.py`
- `upload_attachment` in `attachments.py` (still used by warehouse + portal paths and the sidebar custom-doc upload — keep working as-is)

Each preserved function gets a header comment block:

```
# DISABLED from standard email send (2026-06-10).
# See docs/superpowers/specs/2026-06-10-tms-direct-email-design.md
#
# To re-enable: (1) fix DOC_PATTERNS so DO/IT/ITE recognize TMS-style
# `_<type>_<ms-timestamp>.pdf` filenames; (2) add retry to upload_attachment;
# (3) persist cascade failures to the audit log row.
```

The TMS-008 dedup workaround stays in place for the warehouse path, which still reads QBO attachments.

### 5.3 Customer-data migration

None. `required_docs` keeps its existing meaning for `pod`. Other values in the array become advisory only.

## 6. Testing

- **Unit:** mock TMS returning `{POD, DO, IT, ITE}` with file_urls; verify email payload includes invoice PDF + all four. Mock TMS returning no POD with `pod` in `required_docs`; verify status is `pod_missing` and no email is sent. Mock TMS connect errors; verify retry sequence and final `tms_unreachable` status.
- **Integration against live TMS:** re-send PROMAR01 `LM26060242F` after the fix lands (this is the original failing invoice; TMS currently has all 4 docs for WO `LM2605280007`). Verify the email payload contains DO + POD + IT + ITE plus the invoice PDF. Verify QBO is not modified — no new Attachable records created.
- **Regression:** OEC, warehouse, and portal paths run unchanged end-to-end. No new statuses leak into their audit rows.
- **Audit log shape:** confirm Supabase column rename landed, FastAPI returns `attachments_emailed`, and the UI reads it correctly.

## 7. Out of scope

- Fixing `DOC_PATTERNS` and the silent `upload_attachment` failure mode. The code stays preserved and disabled; revival is a future project.
- Deleting historical QBO attachments. They sit as a paper trail.
- Cloud DB migration for customer data (separate long-term goal in the backlog).
- Any change to the merge tool's POD fallback chain (different code path, different problem).
