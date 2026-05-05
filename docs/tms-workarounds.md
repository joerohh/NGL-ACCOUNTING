# Workarounds Registry

A living index of every external-system bug or gap that the NGL Accounting agent
currently compensates for. The goal is to give a 30-second answer to "what is
the app currently working around, and where is the fix?"

**Conventions:**
- Entries are append-only. When an upstream bug is fixed, flip the entry's
  Status to `Resolved YYYY-MM-DD` — never delete.
- IDs are stable: `<SYSTEM>-NNN`, monotonically incrementing within each system.
- Code that implements a workaround should include a tag comment at its entry
  point: `# WORKAROUND(TMS-008): see docs/tms-workarounds.md`. This links the
  registry and the code in both directions.

---

## Quick lookup

Sorted newest-first.

| ID      | Title                                                | System | Tools affected             | Status      |
|---------|------------------------------------------------------|--------|----------------------------|-------------|
| TMS-008 | Duplicate attachment filter on send                  | TMS    | Invoice Sender             | Active      |
| TMS-007 | Manual TMS login prerequisite                        | TMS    | Invoice Sender             | Active      |
| TMS-006 | TMS REST API surface gaps                            | TMS    | Invoice Sender             | Active      |
| TMS-005 | SPA navigation via direct `page.goto("/main/imp")`   | TMS    | Invoice Sender             | Active      |
| TMS-004 | Document tab network-response intercept              | TMS    | Invoice Sender, Merge Tool | Active      |
| TMS-003 | Container verification via input `value`             | TMS    | Invoice Sender             | Active      |
| TMS-002 | False-positive detail-page markers removed           | TMS    | Invoice Sender             | Active      |
| TMS-001 | Direct-URL nav (AG Grid row-click handlers bypassed) | TMS    | Invoice Sender             | Active      |

---

## TMS workarounds

### TMS-008 — Duplicate attachment filter on send

- **Date added:** 2026-05-05
- **Tools affected:** Invoice Sender
- **Symptom:** Customers receive the same PDF (e.g. POD) attached multiple times
  in a single invoice email. Real-world case: `mm2603020032_ite_1775833088165.pdf`
  (13 KB) appeared 5× on one QBO invoice.
- **Root cause:** When any new document is uploaded to a TMS work order, TMS
  re-uploads ALL prior documents to the linked QBO invoice, producing exact-
  duplicate `Attachable` records on the QBO side.
- **Workaround:** Send-time dedup. A pure helper drops duplicates by
  `(filename.lower().strip(), size)` before the agent emails or uploads any
  attachment list. Tie-breaker: keep the highest QBO Attachable Id (newest
  upload). The QBO record itself is untouched. Skipped count is logged at
  INFO and surfaced to the Invoice Sender UI via the `attachments_deduped`
  SSE event so the user can see when TMS is misbehaving.
- **Files:**
  - Helper: `agent/services/qbo_api/dedup.py` (`dedupe_attachments`)
  - Call sites:
    - `agent/services/job_manager/send_qbo_api.py` (`_dedup_and_emit` + two `check_attachments` call points; the customer-visible bug)
    - `agent/services/job_manager/send_oec.py` (POD pick — newest by id)
    - `agent/services/job_manager/send_portal.py` (POD pick — newest by id)
    - `agent/services/job_manager/fetch_job.py` (POD pick — newest by id)
  - UI: `app/assets/js/tools/invoice-sender/invoice-sender.js` (`attachments_deduped` handler + `dedupNote` row field)
  - Tests: `agent/tests/test_qbo_api_dedup.py`, `agent/tests/test_job_manager/test_send_qbo_api_tms_data.py`
- **Status:** Active.

### TMS-007 — Manual TMS login prerequisite

- **Date added:** 2026-03-XX (pre-registry, retroactively logged 2026-05-05)
- **Tools affected:** Invoice Sender
- **Symptom:** Browser-fallback TMS automation fails with login redirect when
  the agent boots a fresh browser session.
- **Root cause:** TMS does not persist authentication across agent runs (no
  token-based auth on the browser path; session cookies expire/clear).
- **Workaround:** TMS browser automation surfaces a "TMS not logged in"
  warning in the agent panel and requires the user to log in manually before
  any TMS browser operation runs. Separate from QBO login.
- **Files:** `agent/services/tms_browser/login.py`.
- **Status:** Active — partially mitigated by TMS-006 (REST API path needs no
  manual login), but browser fallback still requires it.

### TMS-006 — TMS REST API surface gaps

- **Date added:** 2026-04-XX (pre-registry, retroactively logged 2026-05-05)
- **Tools affected:** Invoice Sender
- **Symptom:** Several Invoice Sender lookups (search by INV#, Billing-tab
  data, free-text search) cannot be served by the new TMS REST API.
- **Root cause:** `api.flow.ngltrans.net` exposes only Detail Info + Documents
  endpoints (`GET /api/v1/work-orders/{wo_no}` and document file URLs). No
  INV# lookup, no Billing endpoint, no search.
- **Workaround:** TMS Data Layer cascade — try REST API first; on miss, fall
  back to the browser automation path (which can scrape the missing surfaces).
- **Files:** `agent/services/tms_api.py`, `agent/services/tms_data/cascade.py`.
- **Status:** Active — depends on internal API team adding endpoints. See
  `reference_tms_api_surface.md` in memory for the full surface inventory.

### TMS-005 — SPA navigation via direct `page.goto("/main/imp")`

- **Date added:** 2026-03-XX (pre-registry, retroactively logged 2026-05-05)
- **Tools affected:** Invoice Sender
- **Symptom:** Sidebar-click navigation to the MAIN grid was unreliable
  (intermittent failures, slow, sensitive to DOM changes).
- **Root cause:** TMS is a React SPA; the sidebar click depends on framework-
  internal handlers that are not stable to drive synthetically.
- **Workaround:** `_navigate_to_main_page()` tries `page.goto(base + "/main/imp")`
  as Strategy 1 before falling back to sidebar click. Direct URL navigation
  works because React Router resolves the path on load.
- **Files:** `agent/services/tms_browser/search.py:153`.
- **Status:** Active.

### TMS-004 — Document tab network-response intercept

- **Date added:** 2026-03-XX (pre-registry, retroactively logged 2026-05-05)
- **Tools affected:** Invoice Sender, Merge Tool (POD pull)
- **Symptom:** Standard "find anchor, click to download" patterns return no
  results on the Document tab.
- **Root cause:** The Document tab uses a div-based flex layout — file rows
  are not `<a>` tags. Files are downloaded by a JS handler that POSTs and
  receives a PDF response in the network layer.
- **Workaround:** `download_document()` uses Playwright's response interception
  — register a response listener for PDFs, click the row, capture the PDF
  bytes from the matched response. Row identification uses the
  `input[type="search"][readonly]` pattern to locate document rows in the
  div grid.
- **Files:** `agent/services/tms_browser/documents.py` (download function +
  row-detection JS at `documents.py:774`).
- **Status:** Active.

### TMS-003 — Container verification via input `value`

- **Date added:** 2026-03-XX (pre-registry, retroactively logged 2026-05-05)
- **Tools affected:** Invoice Sender
- **Symptom:** After navigating to a WO detail page, container-number
  verification (does this page actually show CONT# X?) intermittently failed
  even when the right container was loaded.
- **Root cause:** On the detail page, the CONT# field is a read-only `<input>`
  element, not visible body text. `innerText` doesn't include input field
  values, so a text-only check missed it.
- **Workaround:** Verification checks both `innerText` AND the `value`
  attribute of the relevant `<input>` element.
- **Files:** `agent/services/tms_browser/documents.py` (verification flow).
- **Status:** Active.

### TMS-002 — False-positive detail-page markers removed

- **Date added:** 2026-03-XX (pre-registry, retroactively logged 2026-05-05)
- **Symptom:** `_has_detail_markers()` returned true on the MAIN grid page,
  causing the agent to think it had navigated to a detail page when it had
  not.
- **Root cause:** Earlier marker list included `"PULL OUT"` and `"WO #"`,
  which are AG Grid column group headers / column labels on the MAIN page —
  not unique to the detail view.
- **Workaround:** Marker list narrowed to `['DETAIL INFO', 'BILLING INFO']`,
  which are tab labels that only appear on the detail view.
- **Files:** `agent/services/tms_browser/search.py:479` (`_has_detail_markers`).
- **Status:** Active.
- **Tools affected:** Invoice Sender

### TMS-001 — Direct-URL nav (AG Grid row-click handlers bypassed)

- **Date added:** 2026-03-XX (pre-registry, retroactively logged 2026-05-05)
- **Tools affected:** Invoice Sender
- **Symptom:** Eight different row-click strategies (Playwright click,
  React fiber handler invocation, AG Grid API, dispatchEvent, etc.) failed
  to navigate from the MAIN grid to the WO detail page.
- **Root cause:** AG Grid's event handlers are framework wrappers that reject
  synthetic events. The app-level handler invoked by a real click (`lM()`) is
  a Zustand state setter, not a navigate function — so even successful
  handler invocation didn't produce navigation.
- **Workaround:** Skip the row-click entirely. After extracting the WO# and
  type from the grid, navigate directly to
  `/bc-detail/detail-info/{type}/{woNo}`. Verify arrival via marker check
  (TMS-002) plus container-input check (TMS-003).
- **Files:** `agent/services/tms_browser/search.py` (search/navigate flow at
  ~line 608), `agent/services/tms_browser/documents.py:595`.
- **Status:** Active.

---

## QBO workarounds

(none yet)

---

## Gmail workarounds

(none yet)
