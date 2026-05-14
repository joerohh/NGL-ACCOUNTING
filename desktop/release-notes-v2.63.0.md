## Hotfix — Google login wasn't appearing on cold start

**The bug.** On a fresh app launch, Electron starts the agent (Python) and the web view in parallel. The web view loaded faster than the agent finished booting, so the one-shot health check at startup timed out after 3 seconds and gave up. The pill stayed stuck on "Connecting to agent…" and the Google login button never showed (its availability check requires the agent).

**The fix.** Replaced the one-shot health check with a polling loop that retries every 1.5s for up to 30 seconds while the pill shows "Connecting to agent…". Once the agent responds, the pill flips to green and Google login appears. If the agent never comes online within 30s, the pill flips to red as before.

This was a long-standing race condition that became more visible after v2.62 because the new modules slightly slow cold start. No functional changes to the Invoice Sender / Merge tool / anything else.
