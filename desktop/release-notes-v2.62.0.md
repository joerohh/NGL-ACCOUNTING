## Invoice Sender — major UX overhaul (Fix 1 + Fix 2)

The two bugs Lorena hit on her 60-invoice batch are finally fixed.

### Fix 1 — Clearer errors + volume-friendly UI

- Failed rows now show **specific badges**: ⚠ POD Missing, ⚠ BOL Missing, ⚡ QBO Error — no more generic "No Attachments"
- After-send view splits into **tabs**: ⚠ Needs Attention (default) · ✓ Sent · All Invoices — find the 3 broken rows out of 120 in one click
- **Alert banner** at the top of the failures: "3 invoices need your attention. The other 117 sent successfully."
- **Click a failed row** → side panel slides in from the right with a plain-English explanation, a "What we checked" checklist (✓/✕ per step), and what-to-do guidance
- **Pre-send**: validation banner highlights blockers (unknown customer codes, missing emails) above the table. Send button shows live ready count: "Send 117 Ready Invoices."

### Fix 2 — In-app fix + retry (no more leaving the app)

- Each failed row gets an **Action button**: `↻ Retry` for transient errors, `📎 Attach & Retry` for missing docs
- **Drop zone per missing doc** in the side panel — accepts PDF, JPG, PNG, HEIC
- **Smart verification**: filename match first (instant — no API call when the filename clearly says "pod"), Claude AI fallback only when the filename is ambiguous
- **Wrong-doc detection**: Claude flags mismatches (e.g. you dropped a BOL in the POD slot) with a "Use anyway" override
- **Background QBO upload**: file goes into your retry email instantly, then a fire-and-forget save to QuickBooks so it persists for future reference — no added wait on the retry click
- **Auto-advance**: after a successful retry, the next failure opens automatically (1.5s delay)
- **Bulk retry**: "↻ Retry All Fixed (N)" button on the alert banner retries every row whose files are attached, in sequence
- **Skip button**: silently marks a row skipped and moves on (for problems you'll handle outside the app)

### Under the hood

- New backend endpoints: `POST /jobs/verify-file` (Claude classification per slot) and `POST /jobs/retry-invoice` (multipart retry with files)
- Claude classifier now accepts JPG/PNG/HEIC via the Anthropic vision API — phone photos of PODs work
- Retry path is fully independent of the standard send path — no risk to existing send behavior
- The retry endpoint resolves QBO invoice IDs from invoice numbers when needed, so it works even if the original send never reached QBO

No backend behavior changes for the standard send flow — this is purely the recovery path for when something goes wrong.
