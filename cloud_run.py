"""One cycle of the feed for the scheduled GitHub workflow: fetch, edit, and build the static site.

State (data/bli.db) is carried between runs by the workflow's cache. The site goes to site/; the
workflow deploys it to GitHub Pages only when this prints `deploy=true` to $GITHUB_OUTPUT.
"""
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import editor
import publish
import sources
import store

ROOT = Path(__file__).parent
SITE = ROOT / "site"
BACKLOG_HOURS = 6       # on a fresh state, ignore anything older
STALE_HOURS = 12        # always: an item first seen this long after it was published isn't news
MAX_EDIT_BATCHES = 3    # editor runs per cycle; a big backlog is worked off over several cycles
HEARTBEAT_MINUTES = 20  # deploy at least this often, so the page can show the system is alive
# These outlets refuse every request from data centers (checked 2026-10-14); we respect that and don't ask.
CLOUD_SKIP = {"דבר", "Times of Israel"}

SKELETON = ('<!doctype html><html lang="he"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            '<style>body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>'
            '</head><body>{page}</body></html>')


def log(msg):
    print(f"{datetime.now(timezone.utc):%H:%M:%S} {msg}", flush=True)


def main():
    sources.SOURCES[:] = [s for s in sources.SOURCES if s[0] not in CLOUD_SKIP]
    con = store.connect()
    fresh = con.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    items, health = sources.fetch_all()
    hours = BACKLOG_HOURS if fresh else STALE_HOURS
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    added = store.add_items(con, items, backlog_cutoff=cutoff)
    bad = [f"{h['source']} ({h.get('error', '')[:60]})" for h in health.values() if not h["ok"]]
    log(f"fetch: {added} new{'; failing: ' + ', '.join(bad) if bad else ''}")

    stale = store.expire_new(con, (datetime.now(timezone.utc) - timedelta(hours=STALE_HOURS)).isoformat(timespec="seconds"))
    if stale:
        log(f"expired {stale} items that went stale before the editor saw them")
    can_edit = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")) or not os.environ.get("GITHUB_ACTIONS")
    if not can_edit:
        log("no Claude token secret yet: filtering paused, items wait")
    for _ in range(MAX_EDIT_BATCHES if can_edit else 0):
        if not con.execute("SELECT 1 FROM items WHERE status='new' LIMIT 1").fetchone():
            break
        try:
            editor.run_once(con, log=log)
        except Exception as e:
            log(f"editor error: {type(e).__name__}: {str(e)[:300]}")
            break

    feed, filtered = publish.build(con, publish.health_from_db(con, health))
    digest = hashlib.sha1(json.dumps([feed["stories"], filtered["items"]], ensure_ascii=False,
                                     sort_keys=True).encode()).hexdigest()
    last = store.get_meta(con, "site_deployed_at")
    due = not last or datetime.now(timezone.utc) - datetime.fromisoformat(last) > timedelta(minutes=HEARTBEAT_MINUTES)
    deploy = digest != store.get_meta(con, "site_digest") or due

    shutil.rmtree(SITE, ignore_errors=True)
    SITE.mkdir()
    (SITE / "feed.json").write_text(json.dumps(feed, ensure_ascii=False), encoding="utf-8")
    (SITE / "filtered.json").write_text(json.dumps(filtered, ensure_ascii=False), encoding="utf-8")
    page = (ROOT / "page" / "index.html").read_text(encoding="utf-8")
    (SITE / "index.html").write_text(SKELETON.replace("{page}", page), encoding="utf-8")
    if deploy:
        store.set_meta(con, "site_digest", digest)
        store.set_meta(con, "site_deployed_at", store.now_iso())
    log(f"site: {len(feed['stories'])} stories, {len(filtered['items'])} cuts; deploy={str(deploy).lower()}")

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"deploy={str(deploy).lower()}\n")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
