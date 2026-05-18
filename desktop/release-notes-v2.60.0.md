## Bug fix

Combined PDF (and the other single-output modes) was silently dropping invoice pages for rows that went through a POD-only retry. The folder pointer on each row was getting overwritten when the retry succeeded, so the merge engine looked in the new (POD-only) folder for the invoice and got a 404 — invoice silently skipped, output looked "out of order."

## What changed

Each row now tracks the invoice's folder and the document's folder separately. A POD-only retry only updates the document pointer; the invoice pointer stays pointed at the original folder where the invoice file still lives. No extra QBO calls — retries stay POD-only at full speed.

- Updates `patchRow` to set `invoiceJobId` / `docJobId` per the current job's doc types
- Updates the merge engine's `preloadRowFiles` to read each from its own field
- Falls back to legacy `fetchJobId` for any row patched before this version
