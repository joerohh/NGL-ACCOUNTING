# v2.71.0 — Invoice Sender Combined Results HUD

## What's new

### One unified results panel (replaces three stacked surfaces)
The Invoice Sender now shows a **single combined HUD** for every stage of a batch — pre-send, during-send, and post-send. The old Send Complete summary card, the old filter+table view, and the bottom-of-page v2.62 tabbed results are gone. Everything lives in one place above the Status Log.

The HUD morphs through four states automatically:

- **Idle** — empty state, prompts you to upload an Excel.
- **Uploaded** — every row gets a status pill the moment your Excel is parsed. Ready rows show a sky-blue **"Ready to Send"** pill; anything with a problem (missing INV#, duplicate INV#, no customer match, no email, needs review) shows an amber warning pill.
- **Sending** — a progress strip appears at the top with a live counter, shimmer bar, animated "Sending invoices…" dots, and rolling Elapsed / ETA / Avg-per-invoice stats. Each row's pill ticks from `Queued` → `Sending…` → `Sent` in real time.
- **Complete** — a banner (green / amber / red depending on severity) summarizes the run. A toolbar with Report and Audit buttons appears next to the tab bar.

### Plain-English Resolve flow for every failure
Click any failed row (or the new **Resolve** button on it) and a side panel slides in with:

- **What's wrong** — 1-sentence plain-English explanation
- **What to do** — the action the user should take
- **Action button(s)** — primary action tailored to the pill type
- **Technical detail (for support)** — collapsible, with the raw error text for screenshots

### "Upload to TMS" deep link on POD-missing rows
When QuickBooks and TMS both come up empty for a required POD or BOL, the side panel now offers an **Upload to TMS →** primary action that opens the exact WO# document page in TMS (`nglinnovation.net/bc-detail/document/{type}/{woNo}`). Upload the file there, come back to the app, click Retry. If the WO# couldn't be resolved, the link falls back to the TMS container-search view.

### Cleaner upload card
The old "Step 1 — Invoice CSV" card and "Settings & Actions" card are now **one panel**. The drop zone fills the full card height on the left; **Subject** (with placeholders explained in one line), Test Mode, Send Invoices, and the secondary Audit / Resend / Clear buttons sit on the right.

### Tab bar with live counts
Three tabs at the top of the row list — **Needs Attention** · **Sent** · **All Invoices** — with live counts. Auto-switches to Needs Attention on upload if any row needs a fix, or to Sent after a clean batch. The Needs Attention tab pill is amber so it stands out at a glance.

## What's the same
- Send dispatcher routing (QBO API for `email` and `custom`, OEC for `qbo_invoice_only_then_pod_email`) — untouched.
- TMS auto-fetch cascade for POD/BOL/POL/IT/ITE — untouched.
- v2.62 retry-with-drop-zone flow — still works as a "for this send only" fallback inside the new Resolve panel.
- Customer Manager — untouched (last changed in v2.69).

## Coming next
- **TMS upload backend** — currently "Upload to TMS" deep-links to TMS so the user uploads manually. The agent already knows how to *download* from TMS; an `upload_document(wo_no, file, doc_type)` method would let the app do the whole thing in-place.
- **Fix 4 (CSV import validation)** — Pydantic-style validation on customer create/update, plus CSV import error UX.
- **Multi-user auth** — last remaining Phase 3 item.
