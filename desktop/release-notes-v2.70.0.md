# v2.70.0 — Customer list column fix + repo cleanup

## What's new

### Customer Manager — Status + Actions no longer get cut off
On narrower viewports, the **Status** and **Actions** columns at the right of the customer table were getting pushed off-screen — the scrollbar was easy to miss, so the Edit / Delete buttons looked like they didn't exist. Fixed three ways:

- **Actions is now pinned to the right edge** with a soft shadow. It floats on top of the scrolling content, so it's always one click away regardless of viewport width.
- **Tighter column widths.** Emails column 240→170px, Required Docs 220→150px, Notes 150→100px (with hover tooltip preserving the full text). Saves ~250px overall so most viewports don't need horizontal scrolling at all.
- **Smaller Edit/Delete buttons** so the Actions column takes less real estate.

### Repo housekeeping (invisible to end users)
- Removed 100+ historical build logs, debug screenshots, and ad-hoc test scripts.
- Moved 7 customer/billing `.xlsx` files **out of the repo** to a private folder — sensitive business data shouldn't live in source control.
- Archived 23 shipped plans/specs into `docs/superpowers/{plans,specs}/archive/` — keeps the active design folders focused on what's still in flight.
- Extended `.gitignore` so build noise, customer data, and local workspace state stop accumulating.

## On deck (mocked but not shipped)
- **Combined Results HUD redesign** for the Invoice Sender — replaces today's three stacked result surfaces (Send Complete card · old filter/table · v2.62 tabbed view) with one HUD that morphs across Idle / Uploaded / Sending / Complete states. Mockup at `app/mockups/v2.70-results-hud-mockup.html` — click through six scenarios to validate copy and flow before we commit to the implementation.

## What didn't change
- Send dispatcher, QBO API, TMS auto-fetch, OEC flow — untouched.
- Database schema, customer schema, send-method routing — untouched.
- The login flow and agent connection logic — untouched.
