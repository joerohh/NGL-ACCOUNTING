# QBO Token Refresh Hardening

The payment-reminder tool will poll QBO far more often than today's flows (overdue check per customer, balance checks, etc.). Token refresh needs to be bulletproof before that load hits it.

## Verify
- `.qbo_tokens.json` refresh handles expiry gracefully — no user-facing "please reconnect" mid-job.
- Refresh is **single-flight**: concurrent API calls during expiry don't trigger multiple refresh requests (Intuit invalidates the old refresh token on use).
- Refresh failures surface clearly in the UI, not just the log.

## Files
- `agent/services/qbo_api/` — OAuth + refresh logic
- `agent/.qbo_tokens.json` — token store (shared via Supabase per recent commit `b918fc7`)

## Risk
Supabase-shared tokens mean multiple installs could race on refresh. Confirm there's a lock or last-writer-wins strategy that doesn't orphan other installs.
