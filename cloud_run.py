"""One cycle of the feed for the scheduled GitHub workflow: fetch, edit, and build the static site.

State (data/bli.db) is carried between runs by the workflow's cache. The site goes to site/; the
workflow deploys it to GitHub Pages only when this prints `deploy=true` to $GITHUB_OUTPUT.
"""
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import editor
import notify
import publish
import sources
import store

ROOT = Path(__file__).parent
SITE = ROOT / "site"
BACKLOG_HOURS = 6       # on a fresh state, ignore anything older
STALE_HOURS = 12        # always: an item first seen this long after it was published isn't news
MAX_EDIT_BATCHES = 6    # editor runs per cycle (~1 min each); a big backlog is worked off over several cycles
HEARTBEAT_MINUTES = 20  # deploy at least this often, so the page can show the system is alive
PAUSE_NOTE_MIN = 20     # the editor failing this long while items wait: the page says filtering is paused
# These outlets refuse every request from data centers (checked 2026-10-14); we respect that and don't ask.
CLOUD_SKIP = {"דבר", "Times of Israel"}

PAGE = ROOT / "page" / "index.html"
STATIC = ROOT / "page" / "static"   # manifest, service worker, icons: the installable app


def log(msg):
    print(f"{datetime.now(timezone.utc):%H:%M:%S} {msg}", flush=True)


# The Claude CLI's ways of saying the subscription's usage limit was reached, and when it resets
# ("You've hit your session limit · resets 2:40am (UTC)", as on 2026-10-16, or "...limit reached|1760630400").
LIMIT_RE = re.compile(r"hit your (?:\w+ )?limit|usage limit|limit reached|rate.?limit|\b429\b", re.I)
RESET_EPOCH_RE = re.compile(r"\|(\d{10})\b")
RESET_CLOCK_RE = re.compile(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:\(([\w/+-]+)\))?", re.I)


def _reset_time(error):
    """When a usage limit lifts, if the message says (as an ISO time), else None."""
    m = RESET_EPOCH_RE.search(error)
    if m:
        return datetime.fromtimestamp(int(m.group(1)), timezone.utc).isoformat(timespec="minutes")
    m = RESET_CLOCK_RE.search(error)
    if not m:
        return None
    hour, minute, ampm, tz = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower(), m.group(4)
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    try:
        zone = ZoneInfo(tz) if tz else timezone.utc
    except Exception:
        zone = timezone.utc
    now = datetime.now(zone)
    t = now.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
    if t < now:
        t += timedelta(days=1)
    return t.astimezone(timezone.utc).isoformat(timespec="minutes")


def editor_pause(con, waiting, error):
    """What the page says about filtering, for feed.json: None while the editor keeps up. Once items have waited
    PAUSE_NOTE_MIN with every editor run failing, filtering is "paused": since when, whether it's the usage limit,
    and when that lifts if known. The page shows only that, never the error itself."""
    if not waiting or not error:
        store.set_meta(con, "editor_ok_at", store.now_iso())
        return None
    ok_at = store.get_meta(con, "editor_ok_at")
    if not ok_at:
        store.set_meta(con, "editor_ok_at", ok_at := store.now_iso())
    if datetime.now(timezone.utc) - datetime.fromisoformat(ok_at) < timedelta(minutes=PAUSE_NOTE_MIN):
        return None
    limit = bool(LIMIT_RE.search(error))
    return {"since": ok_at, "limit": limit, "until": _reset_time(error) if limit else None}


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
    edit_error = None if can_edit else "no Claude token secret (CLAUDE_CODE_OAUTH_TOKEN)"
    for _ in range(MAX_EDIT_BATCHES if can_edit else 0):
        if not con.execute("SELECT 1 FROM items WHERE status='new' LIMIT 1").fetchone():
            break
        try:
            editor.run_once(con, log=log)
        except Exception as e:
            edit_error = f"{type(e).__name__}: {str(e)[:300]}"
            log(f"editor error: {edit_error}")
            break

    if can_edit:
        try:
            editor.rewrite_old(con, log=log)
        except Exception as e:
            log(f"rewrite error: {type(e).__name__}: {str(e)[:300]}")

    waiting = con.execute("SELECT COUNT(*) FROM items WHERE status='new'").fetchone()[0]
    if waiting:
        log(f"still waiting for the editor: {waiting} items")

    feed, filtered = publish.build(con, publish.health_from_db(con, health))
    feed["paused"] = editor_pause(con, waiting, edit_error)
    if feed["paused"]:
        log(f"page says filtering is paused: {feed['paused']}")

    # Phone notifications: urgent stories once confirmed, and nothing about how the feed itself is running.
    try:
        notify.push_urgent(con, feed["stories"], log)
    except Exception as e:
        log(f"notify error: {type(e).__name__}: {str(e)[:200]}")
    # Content plus the page's own files, so a new design deploys on the next run too.
    h = hashlib.sha1(json.dumps([feed["stories"], filtered["items"], feed["paused"]], ensure_ascii=False,
                                sort_keys=True).encode())
    for f in [PAGE] + sorted(p for p in STATIC.rglob("*") if p.is_file()):
        h.update(f.read_bytes())
    digest = h.hexdigest()
    last = store.get_meta(con, "site_deployed_at")
    due = not last or datetime.now(timezone.utc) - datetime.fromisoformat(last) > timedelta(minutes=HEARTBEAT_MINUTES)
    deploy = digest != store.get_meta(con, "site_digest") or due

    shutil.rmtree(SITE, ignore_errors=True)
    SITE.mkdir()
    (SITE / "feed.json").write_text(json.dumps(feed, ensure_ascii=False), encoding="utf-8")
    (SITE / "filtered.json").write_text(json.dumps(filtered, ensure_ascii=False), encoding="utf-8")
    # The page shows which commit it was deployed from, so a stale copy on a phone is easy to spot.
    build = (os.environ.get("GITHUB_SHA") or "local")[:7]
    (SITE / "index.html").write_text(PAGE.read_text(encoding="utf-8").replace("__BUILD__", build), encoding="utf-8")
    shutil.copytree(STATIC, SITE, dirs_exist_ok=True)
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
