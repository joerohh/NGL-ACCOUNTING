# TMS Data Layer — Design

**Date:** 2026-04-28
**Status:** Design — pending implementation plan
**Author:** Joseph + Claude

---

## Summary

Standardize how every tool fetches container/work-order data and documents by routing all calls through a new shared module called the **TMS Data Layer**. The data layer runs a layered cascade — **QBO API first, TMS API second, TMS browser as an opt-in fallback** — and exposes a uniform interface so all three batch tools (Invoice Sender, Container Fetch, Chassis Finder) ask the same questions and get back the same shapes.

The browser fallback never runs automatically. When a row fails on the TMS API, it appears in a **Failed Rows** box with two buttons — **Retry (API)** and **Retry (Browser)** — and the user explicitly chooses how to recover. This makes the program faster (most calls become sub-second HTTP), more predictable (no surprise mode switches), and simpler for non-technical co-workers (one row failed → click Retry).

---

## Goals

1. **Speed.** Replace Playwright browser calls with TMS REST API calls wherever the API can satisfy the request. Most TMS lookups go from 3–8 seconds to sub-second.
2. **Uniformity.** Every tool that needs WO/container data calls the same module with the same shape. Future changes happen in one place.
3. **User control over the fallback path.** No automatic browser switching — failed rows surface in a Failed Rows box with explicit Retry buttons.
4. **Path to remove the browser.** Counting how often "Retry (Browser)" gets clicked tells us when it's safe to remove Playwright entirely (separate future project).

## Non-Goals

- **Removing the TMS browser in this work.** It stays as an opt-in fallback. Removal is a separate project once telemetry shows it's safe.
- **Touching the Merge Tool or Customer Manager.** Those tools don't talk to QBO or TMS. Out of scope.
- **Changing the QBO API client.** It already works (commit `0686078` migration). We use it as-is.
- **New tool features.** This is a refactor + speed improvement. No new buttons, no new workflows beyond the Failed Rows box.
- **Per-doc-type configuration UI.** All TMS API doc types (POD, BL, DO, POL, IT, ITE, etc. — whatever the API returns) are supported uniformly.

---

## Current State

### What works today
- **QBO REST API** (`agent/services/qbo_api/`) — fully wired into Invoice Sender, Container Fetch, Chassis Finder. Source of truth for invoice data, attachments, payment links, customer info.
- **TMS REST API** (`agent/services/tms_api.py`) — exists, works, but only used by the `/tms/api-test/{wo_no}` smoke-test endpoint. Not wired into any production workflow.
- **TMS Browser** (`agent/services/tms_browser/`, ~2,080 lines) — handles every TMS interaction in production today. Used by Invoice Sender's direct-URL doc fetch, OEC's D/O sender lookup, and the grid fallback.

### Where the gaps are

| Tool | Has TMS fallback today? | Source for fallback |
|---|---|---|
| Invoice Sender | Yes | Browser (Playwright) — slow, requires TMS login |
| Container Fetch | **No** | If POD not in QBO → permanent miss |
| Chassis Finder | **No** | If chassis empty in QBO → permanent miss |

### The TMS API surface (constraint)
- `GET /api/v1/work-orders/{wo_no}` — returns the Detail Info tab fields and the documents array
- File downloads via `GET {documents[].file_url}`
- **Cannot search by container # or invoice #** — only by WO#
- **Cannot read Billing Info data** (no INV#, no amounts, no payment data)

This means: **every workflow still starts with QBO** to get the WO# (from the `NGL REF#` custom field), then uses TMS API for the WO data and documents. There's no way around the QBO-first dependency.

---

## Target Architecture

```
┌─────────────────────────────────────────────────────┐
│  Tools                                              │
│  • Invoice Sender   • Container Fetch   • Chassis   │
└────────────────────┬────────────────────────────────┘
                     │ asks for: chassis, POD, D/O sender, etc.
                     ▼
┌─────────────────────────────────────────────────────┐
│  TMSDataLayer  (new — agent/services/tms_data/)     │
│  • Runs QBO → TMS API cascade                       │
│  • Records failures into per-job Failed Rows list   │
│  • Routes user-driven retries to API or Browser     │
│  • Emits SSE events                                 │
└────────┬────────────────┬───────────────┬───────────┘
         ▼                ▼               ▼
   QBOApiClient      TMSApiClient    TMSBrowser
   (qbo_api/)        (tms_api.py)    (tms_browser/)
                                     [opt-in fallback only]
```

### Module layout

```
agent/services/tms_data/
├── __init__.py            # public TMSDataLayer class
├── cascade.py             # the QBO → TMS API routing logic
├── failed_rows.py         # per-job failed-rows tracker
├── enriched_invoice.py    # the EnrichedInvoice / Source dataclasses
├── extractors.py          # field extractors (chassis, CNEE, etc.) per source
└── browser_path.py        # explicit "Retry (Browser)" path that bypasses API
```

**Why a package, not one file:** the cascade, the retry tracker, and the per-source extractors are conceptually distinct. Splitting up front makes the eventual "remove the browser fallback" cleanup a single-file delete.

---

## The Public Interface

### Class: `TMSDataLayer`

```python
class TMSDataLayer:
    def __init__(self, qbo_api: QBOApiClient,
                       tms_api: TMSApiClient,
                       tms_browser: TMSBrowser):
        ...

    # ── data access (called by tools, per-row) ─────────────────
    async def enrich_invoice(self, job_id: str,
                              invoice_data: dict,
                              source: Literal["api", "browser"] = "api"
                              ) -> EnrichedInvoice

    async def get_document(self, job_id: str,
                            invoice_data: dict,
                            doc_type: str,
                            dest_dir: Path,
                            source: Literal["api", "browser"] = "api"
                            ) -> Optional[Path]

    async def get_documents(self, job_id: str,
                             invoice_data: dict,
                             doc_types: list[str],
                             dest_dir: Path,
                             source: Literal["api", "browser"] = "api"
                             ) -> dict[str, Path]

    # ── failed-rows control (called by routers + UI) ───────────
    def get_failed_rows(self, job_id: str) -> list[FailedRow]
    async def retry_failed_row(self, job_id: str,
                                row_id: str,
                                source: Literal["api", "browser"]) -> bool
    async def retry_all_failed(self, job_id: str,
                                source: Literal["api", "browser"]) -> dict
    def reset_for_new_job(self, job_id: str) -> None
```

### Return shapes

```python
@dataclass
class EnrichedInvoice:
    wo_no: Optional[str]
    container_no: Optional[str]
    chassis: Optional[str]
    cnee: Optional[str]
    do_sender_email: Optional[str]
    sources: dict[str, Literal["qbo", "tms_api", "tms_browser", "missing"]]
    # ^ sources["chassis"] = "qbo", sources["do_sender_email"] = "tms_api", etc.

@dataclass
class FailedRow:
    row_id: str                       # opaque ID assigned by the data layer
    invoice_number: str
    container_number: Optional[str]
    operation: str                    # "enrich_invoice" | "get_document"
    doc_type: Optional[str]           # populated only for get_document failures
    error_message: str                # one-line preview for the UI
    failed_at_source: Literal["tms_api", "tms_browser"]
    timestamp: float
```

### Behavior in plain English

**`enrich_invoice(invoice_data, source="api")`** — fills in the missing pieces of a QBO invoice:
1. Read what's already on the QBO invoice (chassis, CNEE, WO# from NGL REF#) — no extra network call.
2. If anything is still missing AND we have a WO#:
   - If `source == "api"`: call TMS API `get_work_order(wo_no)` once, fill in any blanks.
   - If `source == "browser"`: drive the TMS browser to the Detail Info tab, scrape the same fields.
3. On API failure (network, 404, 500) → the row is added to the Failed Rows list, the partially-filled `EnrichedInvoice` is returned, the batch keeps going.
4. The browser is **never** auto-invoked. If the user explicitly chose `source="browser"`, we use the browser path; otherwise, never.

**`get_document(invoice_data, doc_type, dest_dir, source="api")`** — fetches a single doc to disk:
1. Resolve WO# from `invoice_data` (NGL REF# field).
2. If `source == "api"`:
   - TMS API: `get_work_order(wo_no)` → find the matching `documents[].file_url` → `download_document(url)` → write to `dest_dir`.
   - On failure → record in Failed Rows, return None.
3. If `source == "browser"`:
   - `tms_browser.fetch_doc_by_wo(wo_no, detail_type, doc_type)` — existing direct-URL path.
   - On failure → record in Failed Rows, return None.

**`get_documents`** — convenience method that calls `get_document` for each requested type. Each individual doc-type failure is recorded as its own Failed Row.

**`retry_failed_row(job_id, row_id, source)`** — user clicked Retry. Re-runs the original operation for that row using the chosen source. Success removes it from `failed_rows`; new failure replaces the old entry with the latest error.

**`retry_all_failed(job_id, source)`** — convenience batch retry over the contents of the Failed Rows box. Returns a dict of how many succeeded and how many remain failed.

**`reset_for_new_job(job_id)`** — clears the failed-rows list for that job ID. Called when a job ends.

---

## The Failed Rows Box (UX)

### Where it lives
- Inside each of the three affected tools (Invoice Sender, Container Fetch, Chassis Finder).
- Top of the right-hand status panel, above the Status Log.
- Hidden when there are zero failed rows; appears the moment the first failure happens.

### What it shows
- **Header:** count badge — *"3 rows had errors"*
- **Each failed row** as one line:
  - Invoice number (and container number if available)
  - What was being fetched: *"POD"*, *"chassis"*, *"WO data"*, etc.
  - One-line error preview (truncated)
  - Two small buttons: **Retry (API)** and **Retry (Browser)**
- **Footer:** two batch buttons:
  - **Retry (API)** — applies to every row currently in the box
  - **Retry (Browser)** — applies to every row currently in the box

### What happens when the user clicks
- **Retry (API)** for one row → `retry_failed_row(job_id, row_id, "api")`. Success removes the row from the box. Failure updates the row's error text in place.
- **Retry (Browser)** for one row → `retry_failed_row(job_id, row_id, "browser")`. Same outcomes. First click in a session may trigger the existing TMS login flow if not already logged in.
- **Retry (API)** at the footer → `retry_all_failed(job_id, "api")`. UI refreshes when each row finishes; rows that succeed disappear, rows that fail remain.
- **Retry (Browser)** at the footer → same as above with the browser path.

### What does **not** happen
- The browser is never invoked unless the user clicks a Browser button.
- The original batch is never paused waiting for clicks. Failures accumulate in the box; the batch processes the full input list.
- No threshold logic, no automatic mode switching, no "browser mode active" badge.

---

## Per-Tool Changes

### 1. Invoice Sender (biggest change)

**File:** `agent/services/job_manager/send_qbo_api.py` and `send_oec.py`.

**Today:** calls `self._tms.fetch_doc_by_wo()` (browser) for direct-URL doc fetch. Calls `self._tms.fetch_pod_and_do_sender()` (browser) when WO# is unavailable. OEC flow does its own browser-driven D/O sender lookup.

**After:**
- `_tms_fetch_and_upload_missing_docs` is rewritten to call `self._tms_data.get_documents(invoice_data, missing_docs, temp_dir)`. Each successfully-returned doc is uploaded to QBO via existing `api.upload_attachment(...)`.
- The OEC POD email step in `_send_oec_pod_email` calls `self._tms_data.get_document(invoice_data, "POD", temp_dir)` and `self._tms_data.enrich_invoice(invoice_data)` for D/O sender.
- Direct browser usage is removed from these files. The data layer is the only TMS gateway.

**User-visible result:** sends are dramatically faster (sub-second per doc instead of 3–8 seconds), TMS login is no longer prompted at the start of every batch, failed lookups appear in the Failed Rows box with both Retry buttons.

### 2. Container Fetch

**File:** `agent/services/job_manager/fetch_job.py`.

**Today:** if QBO doesn't have a POD attached to the invoice, mark `pod_missing = True` and continue. No fallback.

**After:** when QBO comes up empty for a POD, call `self._tms_data.get_document(invoice_data, "POD", job.download_dir)`. If a path is returned, mark `result.pod_file = ...` and emit `pod_found_via_tms`. If None, leave `pod_missing = True` (existing behavior preserved).

**User-visible result:** fewer "POD missing" entries in the merge prep job. PODs that exist in TMS but not QBO are now recovered automatically.

### 3. Chassis Finder

**File:** `agent/services/job_manager/chassis_job.py`.

**Today:** queries QBO by invoice#, extracts chassis + CNEE from QBO custom fields/CustomerMemo. If QBO doesn't have a chassis, the row is marked "no chassis."

**After:** after QBO extraction in `_lookup_one_chassis`, call `self._tms_data.enrich_invoice(invoice_data)`. Use the enriched record's chassis/CNEE values to fill in any blanks. Emit `chassis_found_via_tms` if the chassis came from TMS.

**User-visible result:** more populated cells in the chassis output Excel; fewer "no chassis" rows.

### 4. Merge Tool & Customer Manager
No change. Out of scope.

---

## Hard Invariants the Redesign MUST Preserve

These are existing behaviors the redesign cannot regress. Each must be verified during milestone 2 implementation.

### OEC flow (most stringent)
1. **Two emails per invoice, in order:** POD email goes out FIRST, invoice email SECOND.
2. **POD email** — TO: `customer.podEmailTo`; CC: `customer.podEmailCc` + **D/O sender email**; attachment: POD PDF only (no invoice PDF, no extras).
3. **Invoice email** — TO: `customer.emails`; CC: `ar@ngltrans.net` + `customer.ccEmails` + **D/O sender email**; attachment: invoice PDF only (no POD, no extras — hard-guarded).
4. **D/O sender CC'd on BOTH emails.** The data layer must populate `invoice.do_sender_email` so the existing CC logic in both `_send_oec_pod_email` and `_send_qbo_api` continues to append it.
5. **`result.pod_status` tracked separately from `result.status`.** Final invoice status reconciles to `sent` vs. `sent_no_pod` based on POD email outcome.
6. **Invoice email goes out even if POD email failed** (current `pod_status = "failed" / "skipped"` path).

### All-customers flow
7. **`ar@ngltrans.net` is always CC'd** on the invoice email.
8. **Customer-required-docs check** — the existing `att_check.allPresent` gate stays in place. If required docs are missing after the data layer has tried to fetch them, the invoice is marked `missing_docs` and not sent (existing behavior).
9. **Test mode approval gate** — when `job.test_mode` is on, the per-row approval prompt still fires before any email goes out.
10. **QBO is the single source of truth for invoice data** — invoice PDF, payment portal link, amounts, due date, customer name. The data layer never overrides QBO on these fields.

### Telemetry / cache
11. **D/O sender cache** ([send_oec.py:124-146](agent/services/job_manager/send_oec.py#L124-L146)) — local cache of container-to-D/O-sender mappings is preserved. The data layer reads from cache as a final fallback before declaring "not found."
12. **Audit log** — every send result still writes to the audit log JSONL with the same field shape (status, error, attachments, recipients, timestamp).

---

## Rollout Sequence

Five shippable milestones, each followed by a full version bump + GitHub release per the project's standing rule.

| # | Milestone | Why this order | Risk |
|---|---|---|---|
| 1 | Build the `tms_data/` module — cascade, failed-rows tracker, dataclasses, unit tests. **No tool migration yet.** | Pure backend addition. Lets us test the cascade in isolation before any user-visible change. | Very low |
| 2 | Migrate Invoice Sender to the data layer. Add the Failed Rows box UI to Invoice Sender. | Most-used tool — proves the pattern. Biggest visible speed win. | Medium |
| 3 | Add TMS API fallback to Container Fetch via the data layer. Add the Failed Rows box UI to Container Fetch. | Additive — no regressions possible. Recovers PODs that previously failed. | Low |
| 4 | Add TMS API fallback to Chassis Finder via the data layer. Add the Failed Rows box UI to Chassis Finder. | Same shape as #3. Fewer empty cells in chassis output. | Low |
| 5 | Telemetry: count "Retry (Browser)" clicks across all three tools, surface in Settings. | Future signal for "is it safe to remove the browser?" | Very low |

After milestone 5, we'll have real-world data on whether the browser fallback is ever needed in normal operation. If browser clicks stay near zero for several weeks, browser removal becomes a separate, well-justified project.

---

## Testing Approach

### Unit tests (per milestone)

- `tms_data/cascade.py` — feed mock QBO/TMS API responses, assert the right source is used and the right `EnrichedInvoice.sources[...]` tag is set.
- `tms_data/failed_rows.py` — assert failures accumulate, retries clear them, `reset_for_new_job` empties the list.
- Per-tool changes — assert that with TMS API mocked to fail, the row appears in `get_failed_rows()` with `failed_at_source="tms_api"` and the batch continues.

### Integration tests

- One end-to-end Invoice Sender run against real QBO + real TMS API in test mode (no actual email send) using a known-good invoice number.
- One end-to-end Chassis Finder run against a small (5-row) Excel.
- Manual smoke test of the Failed Rows box: artificially break TMS API (wrong base URL) and confirm rows appear with both Retry buttons, both batch buttons work, retry success removes the row.

### What does **not** need testing

- The QBO API client (already battle-tested, no changes).
- The TMS API client (already has a smoke test endpoint).
- The TMS browser path (no behavior changes — only its invocation point moves).

---

## Open Questions for Future Decisions

These are explicitly **not** decided in this design. Flagging them here so we don't forget.

1. **What counts as "doc type"?** TMS API returns whatever `type_` strings the back-end stores. We pass them through verbatim (POD, BL, DO, POL, IT, ITE…). If new types appear, no code change needed. Confirm this assumption holds — i.e., the customer-required-docs list in Customer Manager uses the same strings.

2. **Should "enrich_invoice" also be called proactively at the start of a batch** (to populate per-row data ahead of time, e.g., for verification), or only when a tool actually needs a missing field? Current design says "only when needed."

3. **Telemetry storage.** Where do we record "Retry (Browser)" clicks for the future browser-removal decision? SQLite audit log is a natural fit but the schema needs a new column or new table. Defer to milestone 5.

4. **What if QBO itself fails** (DNS error, 401, etc.)? Today QBO failures show as `chassis_error` / `container_error` in the existing per-tool error events. Those rows should also appear in the Failed Rows box, but a "Retry (Browser)" button is meaningless for a QBO failure. The UI may show only "Retry (API)" or "Retry (QBO)" for QBO-source failures. Worth deciding during milestone 1 implementation.

---

## What Implementation Will Look Like

After this design is approved, the next step is the **implementation plan** (via `superpowers:writing-plans`). That plan will break each of the five milestones into ordered steps with file paths, test commands, and review checkpoints. Each milestone results in: code committed → version bumped → installer built → GitHub release published → auto-update reaches all co-workers.

The standing rules from `CLAUDE.md` apply throughout: every rebuild bumps the version, commits, pushes, and publishes a GitHub release with the installer + `latest.yml`. No half-finished implementations between milestones.
