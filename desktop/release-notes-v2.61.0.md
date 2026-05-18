## Bug fix — second half of the v2.60 fix

v2.60 split each row's "where are my files" pointer into separate invoice and document folders, but only the bulk-retry path (`Retry all errors` button) actually used the new fields. The **sidebar per-row "Retry API call" button** went through a different code path that bypassed the update — so on a per-row retry, the row's document pointer kept pointing at the original (POD-less) folder. The merge engine 404'd looking for the new POD, silently dropped it, and you got an invoice with no document behind it — which appears in Combined PDF as two consecutive invoice pages.

## What changed

`v2RetryRow` (the sidebar's one-shot retry handler) now repoints `docJobId` and `fetchJobId` at the retry job's folder when `pod_found` lands. Mirrors what `patchRow` already does on the bulk path.

Single-line addition; no other surface area changed.
