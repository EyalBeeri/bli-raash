"""Build the page's two feed documents and hand them to the publisher session (see PUBLISHER.md).

Writes to the artifact need a person's one-time consent in an interactive Claude session, so this
module only prepares files and a pending flag; the publisher session pushes them and calls --ack."""
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sources
import store

ROOT = Path(__file__).parent
ARTIFACT_URL = "https://claude.ai/artifact/DqHRNjpSceDQVtsAggE5vZ"
OUT = ROOT / "data" / "out"
FEED_HOURS = 48
CUTS_HOURS = 24
# The store's limit is 256 KiB per document and it counts our JSON about 15% larger than we do.
MAX_DOC_BYTES = 200_000
HEARTBEAT_MINUTES = 20      # the feed doc is re-pushed at least this often so the page can show it's alive
FILTERED_MINUTES = 15       # the cut list alone is pushed at most this often (it changes almost every loop)


def _ago(hours, step_minutes=0):
    t = datetime.now(timezone.utc) - timedelta(hours=hours)
    if step_minutes:  # move the window edge in steps, so old entries don't drop out on every loop
        t -= timedelta(minutes=t.minute % step_minutes, seconds=t.second, microseconds=t.microsecond)
    return t.isoformat(timespec="seconds")


def _size(doc):
    return len(json.dumps(doc, ensure_ascii=False).encode())


MIN_OUTLETS = 2   # a story is shown only once at least this many different outlets reported it (Eyal's rule)
WAITING = "ממתין למקור נוסף"


def outlets(story):
    return {x["name"] for x in story["sources"]}


def build(con, health):
    stories = [store.story_dict(r) for r in con.execute(
        "SELECT * FROM stories WHERE updated >= ? ORDER BY updated DESC LIMIT 200", (_ago(FEED_HOURS, 30),))]
    for s in stories:
        for k in ("touched", "action", "impact"):   # internal / retired fields stay out of the public JSON
            s.pop(k, None)
    shown = [s for s in stories if len(outlets(s)) >= MIN_OUTLETS]
    # Single-outlet stories wait in the cut list until a second outlet reports them.
    waiting = [{"id": "w" + s["id"], "t": s["updated"], "source": next(iter(outlets(s)), ""), "title": s["headline"],
                "url": s["sources"][0]["url"] if s["sources"] else "", "category": WAITING,
                "reason": f"מקור יחיד עד עכשיו: {next(iter(outlets(s)), '')}"}
               for s in stories if len(outlets(s)) < MIN_OUTLETS and s["updated"] >= _ago(CUTS_HOURS, 30)]
    feed = {"updated": store.now_iso(), "stories": shown, "health": health}
    while _size(feed) > MAX_DOC_BYTES and feed["stories"]:
        feed["stories"].pop()

    cuts = []
    for r in con.execute("""SELECT * FROM items WHERE status='cut' AND COALESCE(published, first_seen) >= ?
                            ORDER BY COALESCE(published, first_seen) DESC LIMIT 900""", (_ago(CUTS_HOURS, 30),)):
        cuts.append({"id": hashlib.sha1(r["url"].encode()).hexdigest()[:12], "t": store.item_time(r),
                     "source": r["source"], "title": r["title"][:160], "url": r["url"],
                     "category": r["cut_category"] or "אחר", "reason": (r["cut_reason"] or "")[:80]})
    filtered = {"updated": store.now_iso(), "items": waiting + cuts}
    while _size(filtered) > MAX_DOC_BYTES and filtered["items"]:
        del filtered["items"][-25:]
    return feed, filtered


def _digest(doc):
    # Health and timestamps change on every check; only content changes count.
    body = {k: v for k, v in doc.items() if k not in ("updated", "health")}
    return hashlib.sha1(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _older_than(con, key, minutes):
    t = store.get_meta(con, key)
    return not t or datetime.now(timezone.utc) - datetime.fromisoformat(t) > timedelta(minutes=minutes)


def prepare(con, health, force=False, log=print):
    """Write changed documents to data/out and raise data/out/pending.json for the publisher session.
    Returns True when a new pending batch was raised."""
    OUT.mkdir(parents=True, exist_ok=True)
    feed, filtered = build(con, health)
    pending_path = OUT / "pending.json"
    pending = json.loads(pending_path.read_text(encoding="utf-8")) if pending_path.exists() else None
    acked = {n: store.get_meta(con, f"digest_{n}") for n in ("latest", "filtered")}
    digests = {"latest": _digest(feed), "filtered": _digest(filtered)}

    want = []
    if force or digests["latest"] != acked["latest"] or _older_than(con, "last_push_latest", HEARTBEAT_MINUTES):
        want.append("latest")
    if force or (digests["filtered"] != acked["filtered"]
                 and ("latest" in want or _older_than(con, "last_push_filtered", FILTERED_MINUTES))):
        want.append("filtered")
    if not want:
        return False
    if pending and set(want) <= set(pending["docs"]) and all(pending["digests"][n] == digests[n] for n in want):
        return False  # already waiting to be pushed with this exact content; don't restart the handshake

    docs = {"latest": feed, "filtered": filtered}
    for n in want:
        (OUT / f"{n}.json").write_text(json.dumps(docs[n], ensure_ascii=False), encoding="utf-8")
    if pending:  # fold in anything from the unpushed batch so it isn't lost
        for n in pending["docs"]:
            if n not in want:
                want.append(n)
                digests[n] = pending["digests"][n]
    seq = int(store.get_meta(con, "seq", 0)) + 1
    store.set_meta(con, "seq", seq)
    pending_path.write_text(json.dumps({"seq": seq, "docs": want, "digests": {n: digests[n] for n in want},
                                        "at": store.now_iso()}), encoding="utf-8")
    if digests["latest"] != acked["latest"] or "filtered" in want:
        log(f"prepare: seq {seq} ready ({', '.join(want)}; {len(feed['stories'])} stories, {len(filtered['items'])} cuts)")
    return True


def ack(con, seq):
    """Called by the publisher session after it pushed pending batch `seq`."""
    pending_path = OUT / "pending.json"
    if not pending_path.exists():
        return "nothing pending"
    p = json.loads(pending_path.read_text(encoding="utf-8"))
    if p["seq"] != seq:
        return f"pending is now seq {p['seq']}; push again"
    now = store.now_iso()
    for n, d in p["digests"].items():
        store.set_meta(con, f"digest_{n}", d)
        store.set_meta(con, f"last_push_{n}", now)
    pending_path.unlink()
    return f"acked seq {seq}"


def health_from_db(con, fetch_health=None):
    """One entry per outlet: whether its last check worked, its error, and when its newest item appeared."""
    status = {}
    for h in (fetch_health or {}).values():
        cur = status.setdefault(h["source"], {"ok": True, "error": ""})
        if not h["ok"]:
            cur["ok"], cur["error"] = False, h.get("error", "")
    newest = dict(con.execute("SELECT source, MAX(COALESCE(published, first_seen)) FROM items GROUP BY source").fetchall())
    out = []
    for name in dict.fromkeys(n for n, _, _ in sources.SOURCES):
        st = status.get(name, {"ok": True, "error": ""})
        out.append({"source": name, "ok": st["ok"], "error": st["error"][:120], "newest": newest.get(name)})
    out.sort(key=lambda x: x["newest"] or "", reverse=True)
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    con = store.connect()
    if len(sys.argv) == 3 and sys.argv[1] == "--ack":
        print(ack(con, int(sys.argv[2])))
    else:
        prepare(con, health_from_db(con), force=True)
