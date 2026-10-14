"""SQLite state: raw items seen from sources, and the stories built from them."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "bli.db"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE IF NOT EXISTS items (
        url TEXT PRIMARY KEY, source TEXT, title TEXT, summary TEXT,
        published TEXT, first_seen TEXT,
        status TEXT DEFAULT 'new',          -- new | cut | story | skipped
        story_id TEXT, cut_category TEXT, cut_reason TEXT,
        attempts INTEGER DEFAULT 0, processed_at TEXT);
    CREATE INDEX IF NOT EXISTS items_status ON items(status);
    CREATE TABLE IF NOT EXISTS stories (
        id TEXT PRIMARY KEY, headline TEXT, summary TEXT, action TEXT,
        topic TEXT, impact TEXT, urgent INTEGER,
        created TEXT, updated TEXT, updates INTEGER DEFAULT 0, sources TEXT);
    CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
    """)
    # touched = last time any outlet reported on the story (updates and attaches); keeps follow-ups in context.
    if "touched" not in {r[1] for r in con.execute("PRAGMA table_info(stories)")}:
        con.execute("ALTER TABLE stories ADD COLUMN touched TEXT")
        con.commit()
    return con


def add_items(con, items, backlog_cutoff=None):
    """Insert unseen items. Items published before `backlog_cutoff` (an ISO time) are stored as skipped, so a
    recovering or newly added source doesn't dump yesterday's news into the feed."""
    seen = now_iso()
    added = 0
    for it in items:
        if not it["url"]:
            continue
        pub = it["published"].astimezone(timezone.utc).isoformat(timespec="seconds") if it["published"] else None
        status = "skipped" if (backlog_cutoff and pub and pub < backlog_cutoff) else "new"
        cur = con.execute(
            "INSERT OR IGNORE INTO items(url, source, title, summary, published, first_seen, status) VALUES (?,?,?,?,?,?,?)",
            (it["url"], it["source"], it["title"], it["summary"], pub, seen, status))
        added += cur.rowcount if status == "new" else 0
    con.commit()
    return added


def item_time(row):
    return row["published"] or row["first_seen"]


def story_dict(row):
    d = dict(row)
    d["impact"] = json.loads(d["impact"] or "[]")
    d["sources"] = json.loads(d["sources"] or "[]")
    d["urgent"] = bool(d["urgent"])
    return d


def get_meta(con, k, default=None):
    r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r["v"] if r else default


def set_meta(con, k, v):
    con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES (?,?)", (k, str(v)))
    con.commit()
