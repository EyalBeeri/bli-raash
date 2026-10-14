# בלי רעש: moving to the cloud for free

**Goal:** the feed keeps running with the PC off and the Claude app closed. No approval clicks, a link you can share, and no new costs.

## Recommended setup: GitHub (free for public repositories)

| Part | Today (on the PC) | In the cloud |
|---|---|---|
| Checking the outlets | `run.py` in a Terminal tab, every 2 min | A GitHub Actions workflow on a schedule, about every 5–15 min (GitHub doesn't run schedules more often than every 5 min, and runs them late when it's busy) |
| Filtering and rewriting | `claude -p` on your subscription | The same `claude -p`, authenticated with a long-lived subscription token (`claude setup-token`), stored as a GitHub secret |
| State (seen items, stories) | SQLite on the PC | JSON files on a separate `state` branch, overwritten on each run (no growing history) |
| The page | claude.ai artifact plus a publisher session you approve | GitHub Pages: a static page that reads `feed.json` from the same site and refreshes itself every minute |

**What it costs:**
- GitHub Actions and GitHub Pages are free for public repositories. No card needed.
- Claude's usage counts against the subscription you already have. There are no new charges, but it shares your limits, so step 3 cuts usage first.

**What becomes public:** the code (scrapers, the editing rules) and the page itself, at `eyalbeeri.github.io/bli-raash`. Your token stays in GitHub's encrypted secrets.

**What we lose:**
- **2-minute freshness:** updates arrive every 5–15 minutes. Safety alerts still belong in the Home Front Command app.
- **The vote buttons:** a static page has nowhere to save them. Calibration rounds keep using the claude.ai labeling page.

**Not doing:** keeping a GitHub job running all day in a loop to get 2-minute checks. GitHub's terms restrict using Actions as general-purpose hosting. A scheduled site rebuild is a normal use; a 24/7 job isn't.

## Fallback if Israeli sites block GitHub's servers

GitHub's machines are in US data centers, and some Israeli sites block or throttle foreign server traffic (Kan already returns 403 to us). If the probe in step 1 shows that, the fallback is Oracle Cloud's "Always Free" server in its Jerusalem region:
- an Israeli IP, a real 2-minute loop, and the current code almost unchanged;
- but the signup needs a credit card for identity checks (no charge within the free tier), and Oracle can reclaim free servers it considers idle.

## Status (2026-10-15)

Steps 1–5 are done. The feed runs from the GitHub workflow; the PC loop and the publisher session are stopped, and the old claude.ai link points to the new site. Davar and Times of Israel are skipped in the cloud because they refuse data-center traffic.

## Steps

1. **Probe (me, about 30 min).** Create the public repo `EyalBeeri/bli-raash` and a one-off workflow that fetches all 10 outlets from GitHub's servers and runs one tiny `claude -p` call. This answers the two unknowns that decide the plan: whether the sites answer foreign servers, and whether the subscription token works there.
2. **Token (you, 2 min).** Run `claude setup-token` and store the result with `gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo EyalBeeri/bli-raash`. The token never passes through me.
3. **Cut usage (me).** Add a cheap first-pass model (Haiku) to throw out obvious junk, so the stronger model only sees real candidates. Skip the editor entirely when nothing new arrived. Measure a real day's usage.
4. **Port (me).** Scheduled workflow, the `state` branch, a GitHub Pages deploy, and the page reading `feed.json`. Test it against the labeled set (`calibrate.py score set1`) to confirm the cloud editor behaves the same.
5. **Switch over (me).** Stop the PC loop and the publisher session, and point the old claude.ai link to the new address.
6. **Later, for a product others use:** a personal subscription shouldn't power a public service. Before sharing widely, score free model APIs against your labeled sets and switch if one comes close.
