# בלי רעש

A calm Hebrew news feed: Israeli outlets are checked on a schedule, and Claude keeps only what matters and rewrites it neutrally, with attribution, newest first.

- `sources.py`: fetchers (RSS where live, page scraping where not). Channel 14 is excluded by design.
- `editor.py` + `rules.md`: what gets in and how it's written. The rules are calibrated against hand-labeled sets (`calibrate.py`).
- `publish.py`, `page/index.html`: the feed documents and the page.
- `docs/cloud-plan.md`: the move from a home PC to GitHub Actions and Pages.

Stories are original summaries that link to the outlets that reported them.
