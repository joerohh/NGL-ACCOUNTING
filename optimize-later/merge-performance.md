# Merge Performance — Quick Wins

Expected gain: ~20-30% faster merges, low risk.

## Changes
- **Parallel file reads** in `readAsArrayBuffer()` calls — currently sequential.
- **`objectsPerTick: Infinity`** on `merged.save()` — removes ~1-2s of yield delays per 100 containers.
- **`updateMetadata: false`** on `PDFDocument.load()`.
- **`updateFieldAppearances: false`** on `merged.save()`.
- **Pre-compute match index** — avoid repeated `toLowerCase()` in the match loop.

## Files
- `app/assets/js/merge.js` — `mergePdfFiles()` (~line 413), `mergePerContainer()` (~line 462)
- `app/assets/js/utils.js` — `readAsArrayBuffer()` (~line 20)

## Bigger win (deferred)
Web Workers for parallel PDF parsing across CPU cores → 2-3x faster. User has 10 cores / 12 logical processors. Skipped for now over lag concerns on background work.
