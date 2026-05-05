# Invoice Sender UX Overhaul — Spec & Handoff

**Status:** Mockup phase — Fix 1 and Fix 2 approved by Joseph. Fix 3 and Fix 4 not yet mocked.
**Created:** 2026-05-05
**Continued from chat session.** When picking up, start by reading this doc top to bottom, then opening the two existing mockup files in the browser.

---

## Why this exists

On 2026-05-04, Joseph's co-worker Lorena ran a 60-invoice batch via the packaged Electron app (post v2.39.0). Five invoices ended with a red **"NO ATTACHMENTS"** badge in the UI and were never sent — she had to email them manually through QBO web. Joseph asked for an audit.

### What the audit revealed

The five containers and their actual statuses (pulled from Supabase `audit_log`):

| Container | Customer | Backend status | Missing |
|---|---|---|---|
| TRHU4593053 | APEXMA01 | `missing_docs` | **pod** |
| SUDU8953501 | APEXMA01 | `missing_docs` | **invoice** |
| OOLU6909529 | TOPTRA02 | `missing_docs` | **invoice** |
| OOLU9916100 | TOPTRA02 | `missing_docs` | **invoice** |
| UETU5693907 | TOPTRA02 | `missing_docs` | **invoice** |

**Three intertwined bugs surfaced:**

1. **UI mislabeling.** [invoice-sender.js:960](app/assets/js/tools/invoice-sender/invoice-sender.js#L960) takes the `invoice_missing_docs` SSE event and stamps the row `skipped_no_attachments`, which renders as the badge "No Attachments." The agent never sent that status — the JS made it up. The actual error string ("Missing required docs: pod") was on `event.missing` but the badge ignores it.

2. **"Invoice" as a required-doc is broken-by-design.** Four of the five rows were missing the "invoice" doc. But the QBO invoice PDF is **always** sent automatically via `download_invoice_pdf` ([send_qbo_api.py:299](agent/services/job_manager/send_qbo_api.py#L299)). It's never an `Attachable`, so the requiredDocs gate at [send_qbo_api.py:233](agent/services/job_manager/send_qbo_api.py#L233) can never see it. The cascade also explicitly skips `"invoice"` ([send_qbo_api.py:61](agent/services/job_manager/send_qbo_api.py#L61) — `skip_types.add("invoice")`). Customers APEXMA01 and TOPTRA02 are the only two in the 214-customer Supabase base set up this way.

3. **No in-app recovery.** When something fails, Lorena's only option is to leave the app, manually upload to QBO web, send manually, and then deal with the duplicate-send guard if she tries to re-run.

### Other inconsistencies caught during audit

- **Local SQLite is out of sync with Supabase.** Local has 191 customers; Supabase has 214. APEXMA01/TOPTRA02 differ between the two stores. Today this is harmless (runtime always uses Supabase) but it's a hidden time-bomb if Supabase ever fails.
- **53 active customers (28% of base) have no emails configured.** They fail with `skipped: no_emails` if invoiced.
- **Server has no validation on `sendMethod` or `requiredDocs`.** CSV import accepts any junk values.
- **Dead code:** [customers.js:84,197](app/assets/js/tools/customers/customers.js#L84) checks `sendMethod === 'qbo_standard'` but the dropdown never produces that value.

---

## The four fixes (planned shipping order)

Joseph approved this version sequence:

| Version | Includes | Status |
|---|---|---|
| **v2.40.0** | Fix 1 + Fix 3 | Mockups: Fix 1 ✅ done, Fix 3 ⏳ pending |
| **v2.41.0** | Fix 2 | Mockup ✅ done |
| **v2.42.0** | Fix 4 | Mockup ⏳ pending |

### Fix 1 — Clearer error messages + volume-friendly UI

**Problem:** Lorena saw "NO ATTACHMENTS" with no actionable info on 5 of 120 rows. Scrolling 120 rows to find 3 problems is brutal.

**Mockups (both built and approved):**
- [app/mockups/fix-1-clear-error-messages.html](app/mockups/fix-1-clear-error-messages.html) — post-send view, full app shell, side panel diagnostic
- [app/mockups/fix-1-before-send.html](app/mockups/fix-1-before-send.html) — three states: Empty / Ready / Sending Live

**What changed in the design:**
- Real `missing_docs` UI status with **specific badge text**: "POD Missing", "BOL Missing", "QBO Error" (orange = retriable, red = needs file)
- Side detail panel slides in from the right with: plain-English explanation, "What we checked" checklist, "What to do" guidance, technical details (collapsed)
- **Filter tabs** at top: ⚠ Needs Attention (auto-default after batch) · ✓ Sent · All Invoices
- **Sticky orange alert banner**: *"3 invoices need your attention. The other 117 sent successfully."*
- **Quick-jump chips** in "All" tab — clickable invoice numbers that scroll-to-row + open panel
- **Prev/Next navigation** in the side panel — fly through failures sequentially
- **Setup panel collapses to a one-line bar** after CSV upload (with "⚙ Settings" expand button)
- **Three validation cards** before send: Ready to Send · Customer Not Found · Missing Email *(NOT D/O Sender — TMS API supplies that automatically)*
- **Auto-revalidation toast**: when Lorena fixes a customer in another tab, the CSV revalidates automatically — no re-upload required. Toast shows "Customer added — re-checked your CSV. 1 invoice moved to Ready."
- **Live elapsed timer + ETA + done-by-clock-time** during sending
- **Send button shows live count**: "Send 117 Ready Invoices" — disabled when blocking errors exist

### Fix 2 — Drop zones + retry buttons (with smart-tier verification)

**Problem:** Today, when a row fails, Lorena has to fix it outside the app entirely. Need an in-app recovery path.

**Mockup (approved):**
- [app/mockups/fix-2-fix-and-retry.html](app/mockups/fix-2-fix-and-retry.html) — fully interactive demo

**What's in the design:**
- **Inline action buttons** on each failed row:
  - Transient errors → one-click "↻ Retry"
  - Missing-doc errors → "📎 Attach & Retry" (opens panel)
- **Side-panel "Fix It" section** with drop zones (one per missing doc, **stacked vertically** — side-by-side looked cramped when AI warning shows)
- **Smart-tier verification** (this is the key design decision Joseph asked about):
  - **Tier 1 — instant filename match.** If filename obviously matches expected type (e.g. `pod_TRHU.pdf` in POD slot → matches `_pod` regex), skip Claude entirely. Status: *"✓ POD verified by filename · ready to retry"*
  - **Tier 2 — Claude AI fallback.** If filename is ambiguous (`IMG_4521.jpg`, `Document.pdf`, `scan_001.pdf`), run Claude on the file. Status: *"✓ POD verified by AI · ready to retry"*. ~1–2s wait, ~$0.0008 per call.
  - **Tier 3 — wrong-doc detection.** When Claude classifies the file as a different type than the slot, show orange warning with two buttons: "↻ Replace File" or "Use anyway" (manual override). Retry button stays disabled until resolved. Override note: *"✓ Manually confirmed · ready to retry"*
- **File formats:** accept JPG/PNG/HEIC directly (Claude vision handles them); convert to PDF only on the way to QBO. Today's classifier code rejects non-PDFs at `_validate_pdf`, needs ~1hr extension.
- **Retry progress view** — panel shows step-by-step: "Uploading attachments → Verifying → Sending email" with check/spinner indicators
- **Auto-advance to next failure** on success ("Next failure →" button) — workflow is fix-confirm-next, like inbox zero
- **"Retry All Fixed (N)" bulk button** at the top of the alert banner — count is dynamic, updates as files attach
- **"Skip" button** for handling outside the app (marks resolved without retry)
- **Existing Claude classifier** ([agent/services/claude_classifier.py](agent/services/claude_classifier.py)) gets reused. Currently only wired into Container Fetch ([fetch_job.py:110,149](agent/services/job_manager/fetch_job.py#L110)). Wiring into Invoice Sender drop zone is net new (~2-3hrs).

**Cost expectations per batch:** typical 5-doc recovery → 0–2 Claude calls (most files have obvious names). Daily cap in [config.py](agent/config.py) `DAILY_API_CALL_LIMIT` already protects against runaway costs.

### Fix 3 — Customer Manager fixes *(no mockup yet)*

**Problem:** "Invoice" checkbox in customer profile creates a gate that can never be reliably satisfied. Two customers (APEXMA01, TOPTRA02) are misconfigured this way.

**Three layers of fix needed:**

1. **Runtime backstop.** [send_qbo_api.py:170](agent/services/job_manager/send_qbo_api.py#L170) — filter `"invoice"` out of `required_docs` before the gate runs. One line:
   ```python
   required_docs = [] if is_oec else [d for d in customer.get("requiredDocs", []) if d.lower() != "invoice"]
   ```
2. **UI removal.** Drop the "Invoice" checkbox from [index.html:650](app/index.html#L650). Replace with a static label: *"The invoice PDF is always sent automatically — you don't need to set this."*
3. **Data migration.** One-time script: strip `"invoice"` from any saved `requiredDocs` in Supabase (and SQLite). APEXMA01 → `["pod"]`, TOPTRA02 → `["pod"]`.

**Mockup needs to show:** the customer-edit modal with "Invoice" removed, replaced by the static helper text.

### Fix 4 — Validation gate + duplicate-code detection *(no mockup yet)*

**Problem:** No server-side validation on customer data. CSV import accepts any junk. Duplicate customer codes silently overwrite or fail with generic error.

**Design:**

1. **Pydantic model** for customer create/update. Allowlists:
   - `sendMethod`: `email | qbo_invoice_only_then_pod_email | portal_upload`
   - `requiredDocs`: list of strings drawn from `pod | pol | bol | pl | do` (note: NO "invoice" — Fix 3 removes it). Allow `"a/b"` OR-group syntax with the same allowlist.
2. **Duplicate-code detection.** When user clicks Save and the code already exists, show a friendly red message under the Customer Code field:
   > ⚠ **Customer code "APEXMA01" is already in use.** Want to edit that customer instead? *(View existing →)*

   Click the link → jumps to the existing customer's edit screen.
3. **CSV import validation.** Reject imports with bad sendMethod values, show clear error per row.

**Mockup needs to show:** customer modal with duplicate-code warning live under the input field; CSV import error state.

---

## Implementation conventions Joseph cares about

Pulled from `MEMORY.md` and conversation:

- **User prefers packaged Electron app** — every shipped fix needs full rebuild + push + GitHub release with installer + `latest.yml` (per `feedback_always_push_and_release.md`).
- **Always bump `desktop/VERSION` first** before building.
- **No fake-choice menus.** When one option is clearly best, recommend it directly.
- **Use Opus for heavy-duty subagents** (invariant-heavy refactors, multi-file changes), Sonnet only for trivial wiring/HTML/CSS.
- **NGL theme:** white/light, orange accents (#ea580c).
- **User is not a developer** — explain technical terms simply.
- **TMS data layer is shipped through v2.39.0.** Milestone 3 (Container Fetch TMS fallback) was approved 2026-04-29 but not started yet — see `project_tms_data_layer.md`. **This Invoice Sender UX work is a different track and was kicked off mid-stream because of Lorena's batch failure.**

## Files that exist now

```
app/mockups/
  fix-1-clear-error-messages.html    ✅ approved
  fix-1-before-send.html             ✅ approved (3 states: empty/ready/sending)
  fix-2-fix-and-retry.html           ✅ approved (smart-tier verification)
docs/superpowers/specs/
  2026-05-05-invoice-sender-ux-overhaul.md   ← this file
```

## Where to pick up

1. **Read this doc top to bottom.**
2. **Open the three mockup files** in a browser to refresh the visual context.
3. **Build Fix 3 mockup next.** Customer-edit modal: Invoice checkbox removed + duplicate-code detection. File: `app/mockups/fix-3-customer-manager.html`. Same app shell as the others.
4. **Then Fix 4 mockup.** Validation gate + CSV import error state. File: `app/mockups/fix-4-validation.html`.
5. **After all four mockups are approved, brainstorm + plan + execute v2.40.0** (Fix 1 + Fix 3 together). Use `superpowers:brainstorming` first per Joseph's process.
6. Ship v2.40.0 → smoke test → v2.41.0 → v2.42.0.

## Key open questions Joseph hasn't answered yet

- For Fix 2's "Skip" button: should it ask for a reason ("why are you skipping?") or just silently mark and move on?
- For Fix 1's auto-advance after retry: auto-advance immediately, or wait for "Next failure →" click? (Currently waits for click — Joseph said "auto advance is good" but the actual implementation still has a click — re-confirm before shipping.)
- For Fix 4's duplicate-code "View existing" link: should it open the existing customer in the same modal (replacing current draft), or open in a new modal stacked on top, or scroll the customer list to that row?

## Hard rules (must not regress)

- **OEC two-email flow** invariants verified in TMS Data Layer Task 13 — see `project_tms_data_layer.md`. None of these UX changes touch `send_oec.py`. Don't let the `requiredDocs` filter accidentally affect OEC behavior — gate is `is_oec ? [] : filtered_docs` already.
- **`ar@ngltrans.net` always CC'd** on invoice emails ([send_qbo_api.py:249](agent/services/job_manager/send_qbo_api.py#L249)).
- **Duplicate-send guard** (6-hour) at [send_job.py:271](agent/services/job_manager/send_job.py#L271) — Fix 2's per-row retry endpoint must bypass it cleanly (the prior attempt was a failure, not a sent).
