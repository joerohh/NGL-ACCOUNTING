# Test Coverage Gaps

Current: 12 unit + 12 endpoint smoke tests in `agent/tests/`.

## Gaps to fill
- **Invoice sender SSE flow** — no tests around the streaming progress events, job lifecycle, or error recovery mid-send.
- **QBO API error paths** — 401 refresh, 429 rate-limit, 5xx retry behavior are untested.
- **Customer DB migrations** — JSON/JSONL → SQLite .bak path has no regression test.

## Why it matters before new work
The payment-reminder tool will layer another long-running SSE job on top of the same infrastructure. Regressions in the send pipeline would affect both tools.
