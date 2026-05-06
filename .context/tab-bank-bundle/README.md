# Tab Bank Bundle — Reference Material

Source material for the **Tab Bank Bundle** tool: a daily invoice + POD packet builder that produces merged PDFs (invoice + proof-of-delivery) for upload to Tab Bank (invoice factoring partner).

**Implementation plan:** `C:\Users\Joseph\.claude\plans\TAB-BUNDLE.md`

## Contents

| File | What it is |
|---|---|
| `workflow-spec.md` | Original workflow spec authored by Jaehyeon Park. Step-by-step description of the existing automation his team uses today (QBO → attachment selection → TMS fallback → merge → save to dated folder). Converted from TXT to Markdown, wording preserved verbatim. |
| `warehouse-xlsx-gap.png` | Screenshot of Jaehyeon's Slack note flagging the gap his script does not handle: **warehouse invoices** (second character of the invoice number = `W`) sometimes include an Excel spreadsheet attachment that needs to be converted to PDF before merging. Deferred to v2 pending a real sample. |

## Why this folder exists

`.context/` is this project's established home for feature-level reference material (see `.context/tms-integration-plan.md` for the precedent). Keeping the Tab Bank reference here — instead of at the repo root — makes it clear these are specs, not code, and pairs the folder with the other planning docs the team maintains.
