"""The live loop: check every source every 2 minutes, edit when there is news, push to the page."""
import re
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import editor
import publish
import sources
import store

POLL_SECONDS = 120
EDIT_GAP_DAY = 600       # at most one editor run per 10 minutes...
EDIT_GAP_URGENT = 120    # ...unless something new looks urgent
EDIT_GAP_NIGHT = 1800    # 01:00-06:00 Israel time
BACKLOG_HOURS = 6        # on a fresh database, ignore anything older
STALE_HOURS = 12         # always: an item first seen this long after it was published isn't news
URGENT = re.compile(r"פיקוד העורף|אזעק|התרע|צבע אדום|מרחב מוגן|ירי רקטות|שיגור|חדירת|הנחיות|נתב\"ג|שביתה כללית|סגירת")
IL = ZoneInfo("Asia/Jerusalem")
LOG = Path(__file__).parent / "data" / "run.log"


def log(msg):
    line = f"{datetime.now(IL):%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def edit_gap(urgent):
    if urgent:
        return EDIT_GAP_URGENT
    return EDIT_GAP_NIGHT if 1 <= datetime.now(IL).hour < 6 else EDIT_GAP_DAY


def main():
    con = store.connect()
    fresh_db = con.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    last_edit = 0.0
    log("started")
    while True:
        started = time.time()
        try:
            items, health = sources.fetch_all()
            hours = BACKLOG_HOURS if fresh_db else STALE_HOURS
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
            added = store.add_items(con, items, backlog_cutoff=cutoff)
            fresh_db = False
            bad = [f"{h['source']} ({h.get('error', '')[:60]})" for h in health.values() if not h["ok"] and not h.get("skipped")]
            pending = con.execute("SELECT title FROM items WHERE status='new'").fetchall()
            urgent = any(URGENT.search(r["title"]) for r in pending)
            if added or bad:
                log(f"fetch: {added} new, {len(pending)} pending{' (urgent)' if urgent else ''}"
                    f"{'; failing: ' + ', '.join(bad) if bad else ''}")
            if pending and time.time() - last_edit >= edit_gap(urgent):
                last_edit = time.time()
                editor.run_once(con, log=log)
            publish.prepare(con, publish.health_from_db(con, health), log=log)
        except Exception as e:
            log(f"error: {type(e).__name__}: {e}")
            traceback.print_exc()
        time.sleep(max(5, POLL_SECONDS - (time.time() - started)))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
