# Warehouse Invoice Sender — Design Spec

**Date:** 2026-06-01
**Author:** Joseph + Claude
**Status:** Approved for implementation planning
**Mockups:**
- `app/mockups/warehouse-invoice-sender.html` — Invoice Sender screen with mixed batch + detail panel
- `app/mockups/warehouse-customer-profile.html` — Customer profile (rejected; kept for reference)

## Problem

Co-workers need to send warehouse invoices through the Invoice Sender. Warehouse invoices differ from regular (drayage) invoices in three ways:

1. **No container number.** Routing letter `W` at position 2 of the INV# identifies warehouse rows (e.g. `LW260515P01`). The container column has nothing to show.
2. **Documents are QBO-only.** TMS doesn't carry warehouse paperwork. Supporting docs (usually an Excel spreadsheet + supporting PDFs) live exclusively as QBO invoice attachments.
3. **Mixed attachment types.** A typical warehouse invoice has at least one `.xlsx` plus zero or more PDFs. The customer expects to receive the `.xlsx` as `.xlsx`, not converted to PDF.

The merge tool already handles warehouse routing (agent-side: `_is_warehouse_row()`, `_handle_warehouse_attachments()`). The Invoice Sender does not — today, warehouse rows can be loaded but they have no special handling, no UI indication, and no enforcement that the required attachments are present.

## Goals

1. Send warehouse invoices from the same Invoice Sender screen as regular invoices — no separate tool or workflow.
2. Detect warehouse rows automatically; no manual classification.
3. Send all QBO attachments as-is. Excel stays Excel.
4. Block sending and flag the row when QBO has no supporting documents attached.
5. Use a separate subject template for warehouse rows so users aren't fighting the `{container_number}` token.
6. Keep the customer profile UI unchanged.

## Non-goals

- No per-customer warehouse settings (recipients, subject, body, attachment rules). All warehouse customers use the same global warehouse subject and the same recipient list as their regular profile.
- No xlsx → PDF conversion. The merge tool does that for its own purposes; the Invoice Sender intentionally does not.
- No TMS fetch for warehouse rows.
- No manual override of routing. If the INV# pattern misclassifies a row, the user edits the INV# in their source data, not in the app.
- No changes to the existing POD email / OEC flow / portal upload paths.
- No new customer profile field, toggle, or panel.

## Architectural Decisions

### Routing detection (shared logic, single source of truth)

Warehouse rows are detected by the existing `parseInvType()` helper at `app/assets/js/shared/utils.js:126`. The same helper already powers the merge tool. The Invoice Sender will call it during CSV parsing to tag each row with a `routingType` field (`'import' | 'export' | 'warehouse' | null`).

Rationale: the merge tool and Invoice Sender share `CSV_ALIASES` and `parseInvType()` already. Reusing the same detection guarantees the two tools never disagree about what a warehouse invoice is.

### Agent-side fetch and send

The agent already has a working warehouse fetch path (`agent/services/job_manager/fetch_job.py`, lines 22–349). The send path needs the equivalent. We will:

1. Add a warehouse branch to the send-dispatch logic. When a row is warehouse-routed, the send pipeline calls a new `send_warehouse_invoice()` flow that:
   - Fetches the QBO invoice PDF (same as today for regular rows).
   - Fetches every QBO attachment on the invoice.
   - Counts non-invoice attachments. If zero, emits a `warehouse_no_docs` failure and stops.
   - Calls `EmailSender.send_invoice_email()` with the invoice PDF + every other attachment, each preserved with its original filename and extension. `email_sender.py:183-197` already picks the correct MIME from the extension, so `.xlsx` files render with the Excel icon in Outlook and Gmail.

2. The "invoice PDF itself" is the file the agent fetched separately via the QBO invoice-PDF endpoint. Any QBO-attached file other than that one counts as a supporting document for the empty-docs check.

### Web-app changes

**CSV parsing (`invoice-sender.js`):**
- On row parse, call `parseInvType(row.invoiceNumber)` and store `routingType` on the row object.
- A row with `routingType === 'warehouse'` and no container number is valid; do not gate it on having a container.

**Subject fields (`index.html` + `invoice-sender.js`):**
- Keep the existing Subject field. Label it "Subject" (unchanged).
- Add a sibling field directly below it: "Subject — Warehouse Invoices". It is `display:none` by default. Whenever the parsed CSV contains ≥1 warehouse row, this field becomes visible and shows a small "N detected" pill in its label.
- Default value: `Warehouse Invoice {invoice_number} - {customer_name}`.
- Help text under each field shows a live preview of what the subject will look like when rendered — no `{token}` syntax exposed to the user.

**Row rendering (`invoice-sender-results.js` and the table view):**
- The container column for a warehouse row shows the literal text `Warehouse` styled in NGL orange (`#c2410c`), not the row's empty container value.
- The INV# cell wraps the position-2 letter (`M`, `E`, `X`, or `W`) in `<span class="inv-letter">` for consistent highlighting. The existing `.inv-letter` CSS rule at `styles.css:1775` is reused (orange `#ea580c`, weight 800). The merge tool already uses this — both tools share the rule. The rule needs to be removed from its current merge-tool-scoped selector and made global so it applies in the Invoice Sender too.

**Needs Attention flag:**
- A warehouse row whose fetch returns zero non-invoice attachments becomes a Needs Attention row.
- Reason text (plain English, no jargon): "No documents attached in QuickBooks. Add at least one Excel or PDF backup to the QBO invoice, then resend."
- Resolve action: opens the row's detail panel with a single primary button — "Open in QuickBooks" — that deep-links to the QBO invoice if the API exposes one, otherwise just opens QBO's main invoices page.

### Customer profile

No changes. Same customer record, same recipients (To/CC/BCC), same send method. Warehouse rows for a customer use the customer's existing email list.

## User-Visible Behaviors

### Loading a mixed CSV

User drops one Excel/CSV that contains both regular and warehouse rows.

1. Status log shows total row count + breakdown (e.g. "21 rows loaded · 17 regular · 4 warehouse").
2. Table renders all rows together. Warehouse rows have:
   - The position-2 `W` in NGL orange inside the INV# cell.
   - The literal text `Warehouse` (also NGL orange) in the container column.
   - All other columns behave normally (customer, status, action buttons).
3. The Warehouse Subject field becomes visible. Pill reads "4 detected".

### Sending

User clicks Send. Per row, the agent:

1. Resolves the customer from the row's customer name (existing behavior).
2. For regular rows: existing fetch/send pipeline.
3. For warehouse rows:
   - Fetches the QBO invoice PDF.
   - Fetches all QBO attachments on the invoice.
   - If non-invoice attachment count == 0 → emit `warehouse_no_docs`, mark row failed, do not send email.
   - Otherwise: build the email using the Warehouse Subject template (per-row substitution), attach the invoice PDF + every QBO attachment with original filenames, send via Gmail SMTP.

### Post-send

- Successfully sent warehouse rows appear in the "Sent" tab alongside regular sent rows.
- Failed warehouse rows (no docs in QBO) appear in "Needs Attention" with the plain-English reason and the Open in QuickBooks button.
- Audit log records the row's routing type so reports can break out warehouse vs regular.

## Error Handling

| Condition | Behavior |
|---|---|
| Warehouse row, zero non-invoice attachments in QBO | Block send. Needs Attention row with "No documents attached in QuickBooks." Resolve = open invoice in QBO. |
| Warehouse row, QBO API failure during attachment fetch | Block send. Needs Attention row with the same generic "QuickBooks didn't respond — try again in a minute" pattern used today for regular rows. |
| Warehouse row, attachment download fails partway through | Block send for that row. Needs Attention. Include the failing filename in the detail panel. |
| Warehouse row, Excel COM unavailable | Not applicable — Invoice Sender does not convert Excel. |
| Warehouse row, customer not found in customer DB | Same as regular rows today. Customer-DB error. |
| Mixed CSV, all warehouse | Subject field for Regular stays visible (so the user can still see the default) but only the Warehouse Subject is actually used. |
| Mixed CSV, all regular | Warehouse Subject field stays hidden. |

## Data Model

No persistent schema changes. The customer DB is untouched. The CSV row in memory gains:

```js
{
  // ... existing fields ...
  routingType: 'import' | 'export' | 'warehouse' | null,
}
```

The agent's send-result payload for a warehouse row gains:

```python
{
  "routing_type": "warehouse",
  "warehouse_attachments": [ { filename, size_bytes } ],  # non-invoice files actually sent
  "warehouse_failures": [ { filename, error } ],          # files that failed to fetch
  "warehouse_no_docs": bool,                              # true if zero non-invoice attachments
}
```

These fields mirror the merge tool's existing payload so the two tools stay aligned.

## Testing

### Unit tests (agent)

- `parseInvType('LW260515P01') == 'warehouse'` — already covered by merge tool tests; verify the same helper is used.
- Send pipeline: warehouse row with 1 xlsx + 2 PDFs → email sent with 4 attachments (invoice PDF + 3 supporting).
- Send pipeline: warehouse row with only the invoice PDF in QBO → `warehouse_no_docs`, no email, failure recorded.
- Send pipeline: warehouse row with xlsx attachment → attachment is sent with its original `.xlsx` filename, MIME type `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` (not `application/pdf`).
- Send pipeline: regular row with INV# `LM26050100F` → existing send path runs, no warehouse handling triggered.

### Integration test (agent)

- End-to-end warehouse send via mocked QBO + real `EmailSender` in test mode (`EMAIL_TEST_REDIRECT` env var routes the email to a sink address). Verify subject, body, and attachment list match expectations.

### Web-app smoke test (manual)

- Load a CSV with 2 regular + 2 warehouse rows. Confirm:
  - Container column shows `Warehouse` (orange) on warehouse rows.
  - INV# `W` is orange on warehouse rows.
  - Warehouse Subject field is visible with "2 detected" pill.
  - Preview lines update when the user types in either Subject field.
- Send the batch (with agent in test mode). Confirm:
  - Regular rows go through the existing path unchanged.
  - Warehouse rows produce emails with xlsx attachments preserved.
  - A deliberately empty (no-attachment) warehouse row appears in Needs Attention.

## Open Questions

None at design-lock. All major decisions resolved during brainstorming:
- Workflow shape: same CSV, auto-route (Option A).
- Customer matching: same DB, no special handling.
- Attachments: xlsx stays xlsx (Option A).
- Subject template: two fields, warehouse field appears only when warehouse rows are detected (Option B).
- Empty-docs definition: non-invoice attachments only (interpretation B).
- Profile UI: unchanged.

## Implementation Notes for the Plan

Files most likely to change:

- `app/assets/js/shared/utils.js` — confirm `parseInvType` exports as expected; no change needed if already exported.
- `app/assets/js/tools/invoice-sender/invoice-sender.js` — CSV parsing, row tagging, conditional Warehouse Subject field visibility, mixed-batch summary.
- `app/assets/js/tools/invoice-sender/invoice-sender-results.js` — row rendering, container cell, INV# letter wrapping, Needs Attention row.
- `app/assets/js/tools/invoice-sender/invoice-sender-hud.js` — preview lines under subject fields.
- `app/index.html` — add Warehouse Subject DOM block under the existing Subject field.
- `app/assets/css/styles.css` — promote `.inv-letter` rule out of `#mergeToolViewV2` scope; add `.container-cell.warehouse` and `.wh-subject-block` styles.
- `agent/services/job_manager/` — new `send_warehouse.py` (or a method on the existing send mixin) that runs the warehouse send flow.
- `agent/services/job_manager/__init__.py` — dispatch warehouse routing to the new flow.
- `agent/tests/test_send_warehouse.py` — new test file mirroring `test_fetch_job_warehouse.py`.

No version-bump or build-pipeline changes are spec-level decisions — those follow the standard "Rebuild Pipeline — MANDATORY" rule from CLAUDE.md.
