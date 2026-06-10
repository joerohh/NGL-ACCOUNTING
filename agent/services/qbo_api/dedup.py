"""TMS-008 attachment dedup helper.

# WORKAROUND(TMS-008): see docs/tms-workarounds.md

When any new document is uploaded to a TMS work order, TMS re-uploads ALL prior
documents to the linked QBO invoice, producing exact-duplicate Attachable
records. This helper drops duplicates by (filename.lower().strip(), size) so
the Invoice Sender doesn't email five copies of the same POD.

Pure: no I/O, no logging. Caller is responsible for logging the outcome.
"""


def dedupe_attachments(attachments: list[dict]) -> tuple[list[dict], list[dict]]:
    """DISABLED from standard email send (2026-06-10). Still used by warehouse path.

    See docs/superpowers/specs/2026-06-10-tms-direct-email-design.md.

    Originally used by send_qbo_api._send_qbo_api to drop TMS-008 duplicate
    Attachable records before email. Standard email no longer reads QBO
    attachments at all (TMS-direct), so the workaround is moot for that path.

    ---

    Return (kept, skipped).

    Two attachments are duplicates if (filename.lower().strip(), size) match.
    Tie-breaker: keep the attachment with the highest int(id) — QBO IDs are
    monotonic, so highest = most recent upload. IDs are compared as ints, NOT
    strings (QBO returns IDs as digit strings of varying lengths).

    Stable order: kept preserves the position of the first occurrence of each
    match key in the input list, even when a later occurrence wins the
    tie-breaker.
    """
    if not attachments:
        return [], []

    # Group indexes by match key, preserving first-occurrence ordering.
    groups: dict[tuple[str, int], list[int]] = {}
    order: list[tuple[str, int]] = []
    for idx, att in enumerate(attachments):
        key = ((att.get("fileName") or "").lower().strip(), att.get("size") or 0)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(idx)

    kept: list[dict] = []
    skipped: list[dict] = []
    for key in order:
        idxs = groups[key]
        if len(idxs) == 1:
            kept.append(attachments[idxs[0]])
            continue
        # Pick winner by highest int(id); rest go to skipped.
        winner_idx = max(idxs, key=lambda i: int(attachments[i].get("id") or 0))
        kept.append(attachments[winner_idx])
        for i in idxs:
            if i != winner_idx:
                skipped.append(attachments[i])

    return kept, skipped
