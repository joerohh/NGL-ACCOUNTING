# Spec: Workarounds Registry + TMS Duplicate Attachment Filter

**Date:** 2026-05-05
**Status:** Approved (brainstorming complete, ready for plan)
**Owner:** Joseph

## Background

The in-house TMS system has multiple bugs and gaps that the NGL Accounting agent has had to compensate for over time. These workarounds have accumulated as small one-off fixes scattered across the codebase, and there is currently no single place to see "what is the app currently working around?" The user has lost track of what tricks have been added and why.

A new TMS bug surfaced: when any new document is uploaded to a TMS work order, TMS re-uploads ALL prior documents to the linked QBO invoice, producing exact-duplicate `Attachable` records. Because the invoice sender attaches every QBO `Attachable` to outgoing customer emails, customers receive multiple identical PDFs in a single email (the screenshot at `docs/screenshots/image (12).png` shows the same 13 KB file repeated 5 times).

This spec covers two related deliverables, shipped together because the new fix is the trigger for finally creating the registry:

1. A **Workarounds Registry** — one markdown document listing every external-system workaround the app currently does.
2. A **Duplicate Attachment Filter** — a send-time dedup that drops duplicate attachments before emailing/uploading.

## Goals

- Give the user a single, scan-in-30-seconds view of all workarounds and which tools they affect.
- Stop sending duplicate PDFs to customers without touching the QBO record itself.
- Keep both deliverables additive — nothing destructive, nothing that requires reconfiguration if/when TMS is fixed.

## Non-goals

- No deletion or modification of existing QBO `Attachable` records.
- No change to TMS upload behaviour (the cascade upload path already pre-checks existence).
- No new in-app UI for browsing the registry. The registry is a markdown file, period. (May become a Settings tab later — out of scope here.)
- No new admin/manual override for choosing which duplicate to keep. The tie-breaker is automatic.

## Deliverable 1 — Workarounds Registry

### Location and format

- **Path:** `docs/tms-workarounds.md` (kept at the top level of `docs/` for visibility; not buried under `superpowers/`).
- **Format:** A single markdown document with three regions:
  1. Header explaining what the file is.
  2. **Quick lookup table** at the top, sorted newest-first, columns: `ID | Title | System | Tools affected | Status`.
  3. **Detailed entries**, grouped by external system (TMS, QBO, Gmail). Within each group, sorted newest-first.

### Entry format

Each entry is a level-3 heading with five fields:

```markdown
### TMS-008 — Duplicate attachment filter
- **Date added:** 2026-05-05
- **Tools affected:** Invoice Sender
- **Symptom:** What the user/customer sees when the bug fires
- **Root cause:** The actual TMS/QBO/Gmail misbehaviour
- **Workaround:** What the app does to compensate (one to two sentences)
- **Files:** Path(s) to the implementation, with line refs where useful
- **Status:** Active | Resolved YYYY-MM-DD | Superseded by TMS-NNN
```

### IDs and conventions

- IDs are stable: `<SYSTEM>-NNN`, monotonically incrementing within each system. Once assigned, an ID never changes, even if the entry is later marked Resolved.
- When TMS fixes a bug, the corresponding entry's Status flips to `Resolved YYYY-MM-DD` and the entry stays in the file for historical context. The registry is append-only; nothing is deleted.
- Code that implements a workaround should include a tag comment at the entry point so the doc and code link both directions: `# WORKAROUND(TMS-008): see docs/tms-workarounds.md`.

### Seeded entries

The registry ships seeded with the workarounds already in the codebase (renumbered consecutively for cleanliness; the dedup feature in this spec becomes TMS-008):

| ID      | Title                                                       | Tools affected               |
|---------|-------------------------------------------------------------|------------------------------|
| TMS-001 | Direct-URL navigation (AG Grid row-click handlers reject synthetic events) | Invoice Sender |
| TMS-002 | False-positive detail-page markers ("PULL OUT", "WO #") removed | Invoice Sender |
| TMS-003 | Container verification via input `value` (CONT# is an input field on detail page) | Invoice Sender |
| TMS-004 | Document tab network-response intercept (no `<a>` links in div-based flex layout) | Invoice Sender, Merge Tool |
| TMS-005 | SPA navigation via direct `page.goto("/main/imp")` (first strategy in `_navigate_to_main_page`) | Invoice Sender |
| TMS-006 | TMS REST API gaps — only Detail Info + Documents available; browser fallback retained for INV#, Billing, search | Invoice Sender |
| TMS-007 | Manual TMS login prerequisite (sessions don't persist between agent runs) | Invoice Sender |
| TMS-008 | Duplicate attachment filter (see Deliverable 2 below) | Invoice Sender |

QBO and Gmail sections start empty with a `(none yet)` placeholder.

## Deliverable 2 — TMS-008: Duplicate attachment filter

### Helper module

A new file `agent/services/qbo_api/dedup.py` exporting one pure function:

```python
def dedupe_attachments(attachments: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (kept, skipped).

    Two attachments are duplicates if (filename.lower().strip(), size) match.
    Tie-breaker: keep the attachment with the highest QBO Id (newest upload).
    """
```

- **Match key:** `(filename.lower().strip(), size_int)`. Case- and whitespace-insensitive on filename; exact match on size in bytes.
- **Tie-breaker:** highest `int(att["id"])` survives. QBO IDs are monotonic, so highest = most recent upload.
- **Pure, no side effects.** No I/O, no logging inside the function — caller logs the outcome.
- **Stable order preserved** for `kept` (preserves the input ordering, just with dupes removed).

### Call sites

Four locations get one-line wire-up after the existing `list_attachments`/`check_attachments` call:

| File | Line | Today | After dedup |
|---|---|---|---|
| `agent/services/job_manager/send_qbo_api.py` | 178 | iterates and emails every attachment (the actual bug) | iterates `kept` only — customer gets one of each |
| `agent/services/job_manager/send_oec.py` | 53 | picks first POD found | picks newest POD via tie-breaker |
| `agent/services/job_manager/send_portal.py` | 79 | picks first POD found | picks newest POD via tie-breaker |
| `agent/services/job_manager/fetch_job.py` | 133 | picks first POD via `next(...)` | picks newest POD via tie-breaker |

The customer-visible bug is `send_qbo_api.py`. The other three are defensive — if TMS uploads a corrupted 0-byte duplicate, dedup ensures we don't accidentally pick that one as "the" POD.

### Visibility — logging and SSE

Per invoice, two surfaces:

- **Agent log line** (INFO level): `Deduped attachments for <invoiceNumber>: kept N of M (skipped K TMS duplicates)` — only logged when `K > 0`.
- **SSE event** named `attachments_deduped`, payload `{invoiceNumber, kept: int, skipped: int, skippedFiles: [...filenames]}`. Emitted only when `skipped > 0`.

The Invoice Sender UI surfaces this as a small inline note on the row in the send results, e.g. `4 duplicate attachments skipped`. No action required from the user — purely informational so they can spot when TMS is misbehaving more than usual.

### Testing

- **Unit test** at `agent/tests/test_qbo_api_dedup.py`:
  - Feed the exact pattern from the screenshot (`mm2603020032_ite_1775833088165.pdf` × 5 with identical size, distinct IDs); assert 1 kept, 4 skipped, kept = highest ID.
  - Edge: same filename, different sizes → both kept (real revision, not a duplicate).
  - Edge: empty list → empty kept, empty skipped.
  - Edge: filename casing/whitespace differences → still deduped.
- **Integration test** extending `agent/tests/test_job_manager/test_send_oec_tms_data.py` (or a new `test_send_qbo_api.py`): assert that when `list_attachments` returns dupes, the resulting `email_attachments` contains no duplicate filenames.

## Order of work

Two commits, in this order:

1. **Commit 1 — Workarounds Registry seed.** Creates `docs/tms-workarounds.md` with TMS-001 through TMS-007 fully filled in, plus a placeholder TMS-008 entry pointing to "in progress, see spec". No code changes.
2. **Commit 2 — TMS-008 implementation.** Adds `dedup.py`, wires the four call sites, adds the SSE event, adds tests, and updates the TMS-008 entry in `docs/tms-workarounds.md` from "in progress" to its final form.

This ordering means the registry exists before the next workaround is added — the pattern is established, future TMS fixes have a home immediately.

## Open questions

None. Ready to move to implementation plan.
