"""The editor step: one headless Claude call decides what each new item is and writes the stories."""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import store

ROOT = Path(__file__).parent
MODEL = "sonnet"
TRIAGE_MODEL = "haiku"  # cheap first pass: drops only the obvious, so the editor sees real candidates
FULL_CONTEXT_HOURS = 12  # older stories are shown to the editor by headline only
MAX_BATCH = 60          # new items per call; halved after each failed attempt
MAX_ATTEMPTS = 3        # an item the editor fails on this many times is dropped
CONTEXT_HOURS = 48      # existing stories shown to the editor (same window as the page)
TOPICS = ["ביטחון", "מדיני", "פוליטיקה", "משפט", "כלכלה", "חברה", "בריאות", "סביבה ותשתיות", "עולם"]
CUT_CATEGORIES = ["פשיעה ותאונות", "דעות ופרשנות", "קמפיין וסקרים", "השערות ודיווחים לא מאומתים",
                  "חדשות חוץ", "ספורט", "תרבות ופנאי", "צרכנות ולייף סטייל", "עסקים ושוק ההון", "אחר"]

SYSTEM = """You are the editor of "בלי רעש", a calm Hebrew news feed for Israeli readers who want to know what matters without drama, fear-mongering or bias.

You receive (1) the stories already in the feed and (2) new raw items scraped from Israeli outlets. For every new item, decide what it is, and write or update stories, strictly following the editorial rules below. Respond only through the JSON schema.

For each new item (by its index i) choose exactly one action:
- "cut": the item fails the test. Give cut_category (one of the listed cut categories) and cut_reason (Hebrew, at most 10 words).
- "attach": the item is about a story already in the feed and adds no material new fact. Set story to that story's id.
- "story": the item passes the test. Set story to an existing story id when it is the same event and adds a material new fact, or to a new key ("new1", "new2", ...) for a new story. Items about the same new event share one key.

UNCONFIRMED REPORTS are items cut earlier because only one outlet reported them. When a new item from a different outlet reports the same thing, the report is now confirmed: make it a story and list the report's id (u0, u1, ...) in that story's confirms.

Then, in stories, output one entry for every new key and for every existing story that received a material new fact, with its complete current text (not a diff): headline, summary, topic, urgent.
- When updating an existing story, rewrite it to the current state of affairs; keep it as short as before.
- Use only facts from the items and the existing story. Never invent or infer facts. Attribute claims by outlet or speaker.
- Many outlets covering something is not a reason to include it.
- All text is Hebrew.

EDITORIAL RULES:
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": {"type": "object", "properties": {
            "i": {"type": "integer"},
            "action": {"type": "string", "enum": ["cut", "attach", "story"]},
            "story": {"type": "string"},
            "cut_category": {"type": "string", "enum": CUT_CATEGORIES},
            "cut_reason": {"type": "string"},
        }, "required": ["i", "action"]}},
        "stories": {"type": "array", "items": {"type": "object", "properties": {
            "key": {"type": "string"},
            "headline": {"type": "string"},
            "summary": {"type": "string"},
            "topic": {"type": "string", "enum": TOPICS},
            "urgent": {"type": "boolean"},
            "confirms": {"type": "array", "items": {"type": "string"}},
        }, "required": ["key", "headline", "summary", "topic", "urgent"]}},
    },
    "required": ["items", "stories"],
}


def _il(iso):
    return datetime.fromisoformat(iso).astimezone(ZoneInfo("Asia/Jerusalem")).strftime("%d.%m %H:%M")


UNCONFIRMED = "השערות ודיווחים לא מאומתים"


TRIAGE_SYSTEM = """You pre-screen raw news items for "בלי רעש", a calm Hebrew news feed for Israeli readers. A stronger editor decides afterwards; your only job is to remove items that obviously do not belong, to save its time.

Mark an item "drop" ONLY when it plainly is one of: sports; culture, TV, celebrities, gossip; lifestyle, food, travel, consumer tips; horoscope; sponsored content; opinion columns, analysis and interviews; daily election-campaign news (polls, attacks between politicians, candidates joining or leaving lists, campaign ads); a single crime, accident or personal tragedy with no instruction to the public; routine foreign politics, crime abroad or curiosities; business news about a single company.

Everything else is "keep": government and court decisions, security events, the economy, safety instructions, significant world events, anything that might relate to one of the existing stories, and anything you are not sure about. When in doubt, keep.

For each "drop" give cut_category (one of the listed categories) and cut_reason (Hebrew, at most 8 words)."""

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
        "i": {"type": "integer"},
        "decision": {"type": "string", "enum": ["keep", "drop"]},
        "cut_category": {"type": "string", "enum": CUT_CATEGORIES},
        "cut_reason": {"type": "string"},
    }, "required": ["i", "decision"]}}},
    "required": ["items"],
}


def triage(stories, items):
    """Returns ({index: (cut_category, cut_reason)} for obvious drops, cost)."""
    lines = ["EXISTING STORY HEADLINES:"] + [f"- {s['headline']}" for s in stories] + ["", "NEW ITEMS (i | outlet | title | summary):"]
    lines += [f"{i} | {it['source']} | {it['title']} | {(it['summary'] or '')[:160]}" for i, it in enumerate(items)]
    out, cost = _claude_json(TRIAGE_MODEL, TRIAGE_SYSTEM, "\n".join(lines), TRIAGE_SCHEMA,
                             "Pre-screen the new items according to your instructions.")
    drops = {}
    for d in out["items"]:
        i = d.get("i")
        if isinstance(i, int) and 0 <= i < len(items) and d.get("decision") == "drop":
            drops[i] = (d.get("cut_category") or "אחר", d.get("cut_reason") or "")
    return drops, cost


def build_context(stories):
    """The feed so far. It goes into the system prompt, which is cached, so runs that don't change any
    story re-read it from the cache instead of paying for it again. Stable order keeps it cacheable."""
    lines = ["EXISTING STORIES IN THE FEED (id | last update | headline | summary; older stories by headline only):"]
    recent = (datetime.now(timezone.utc) - timedelta(hours=FULL_CONTEXT_HOURS)).isoformat(timespec="seconds")
    for s in sorted(stories, key=lambda s: s["id"]):
        tail = f" | {s['summary']}" if s["updated"] >= recent else ""
        lines.append(f"{s['id']} | {_il(s['updated'])} | {s['headline']}{tail}")
    if not stories:
        lines.append("(none yet)")
    return "\n".join(lines)


def build_input(items, unconfirmed=()):
    lines = []
    if unconfirmed:
        lines.append("UNCONFIRMED REPORTS (id | outlet | time | title):")
        for n, u in enumerate(unconfirmed):
            lines.append(f"u{n} | {u['source']} | {_il(store.item_time(u))} | {u['title']}")
        lines.append("")
    lines.append("NEW ITEMS (i | outlet | time | title | summary):")
    for i, it in enumerate(items):
        lines.append(f"{i} | {it['source']} | {_il(store.item_time(it))} | {it['title']} | {it['summary'] or ''}")
    return "\n".join(lines)


def call_claude(context, user_input):
    system = SYSTEM + (ROOT / "rules.md").read_text(encoding="utf-8") + "\n\n" + context
    return _claude_json(MODEL, system, user_input, SCHEMA, "Process the new items according to your instructions.")


def _claude_json(model, system, user_input, schema, prompt):
    cmd = ["claude", "-p", prompt, "--model", model, "--tools", "", "--system-prompt", system,
           "--json-schema", json.dumps(schema, ensure_ascii=False),
           "--output-format", "json", "--no-session-persistence"]
    r = subprocess.run(cmd, input=user_input, capture_output=True, text=True, encoding="utf-8", timeout=900)
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"editor returned non-JSON (exit {r.returncode}): {r.stdout[-500:]} {r.stderr[-500:]}")
    if out.get("is_error") or not out.get("structured_output"):
        raise RuntimeError(f"editor failed: {out.get('subtype')} {str(out.get('result'))[:500]}")
    return out["structured_output"], out.get("total_cost_usd")


def run_once(con, log=print):
    """Process one batch of new items. Returns number of items processed."""
    worst = con.execute("SELECT MAX(attempts) FROM items WHERE status='new'").fetchone()[0] or 0
    items = con.execute(
        "SELECT * FROM items WHERE status='new' ORDER BY attempts DESC, COALESCE(published, first_seen) LIMIT ?",
        (max(5, MAX_BATCH >> min(worst, 3)),)).fetchall()
    if not items:
        return 0
    since = (datetime.now(timezone.utc) - timedelta(hours=CONTEXT_HOURS)).isoformat(timespec="seconds")
    stories = [store.story_dict(r) for r in con.execute(
        "SELECT * FROM stories WHERE COALESCE(touched, updated) >= ? ORDER BY updated DESC", (since,))]
    existing = {s["id"]: s for s in stories}
    unconfirmed = con.execute(
        "SELECT * FROM items WHERE status='cut' AND cut_category=? AND COALESCE(published, first_seen) >= ? "
        "ORDER BY COALESCE(published, first_seen) DESC LIMIT 30",
        (UNCONFIRMED, (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat(timespec="seconds"))).fetchall()

    try:
        drops, triage_cost = triage(stories, items)
    except Exception as e:  # the first pass is an optimization; without it the editor sees everything
        drops, triage_cost = {}, 0
        log(f"triage failed, editor sees all items: {type(e).__name__}: {str(e)[:120]}")
    for i, (cat, reason) in drops.items():
        con.execute("UPDATE items SET status='cut', cut_category=?, cut_reason=?, processed_at=? WHERE url=?",
                    (cat, reason, store.now_iso(), items[i]["url"]))
    total = len(items)
    items = [it for i, it in enumerate(items) if i not in drops]
    if not items:
        con.commit()
        log(f"triage: {total} items, all dropped; cost ${triage_cost or 0:.3f}")
        return total

    try:
        result, cost = call_claude(build_context(stories), build_input(items, unconfirmed))
    except Exception:
        _note_attempt(con, items)
        raise
    cost = (cost or 0) + (triage_cost or 0)
    written = {s["key"]: s for s in result["stories"]}
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    key_to_id, members = {}, {}
    decided = set()

    for d in result["items"]:
        i = d.get("i")
        if not isinstance(i, int) or not 0 <= i < len(items) or i in decided:
            continue
        it = items[i]
        act, key = d["action"], d.get("story", "")
        if act == "cut":
            con.execute("UPDATE items SET status='cut', cut_category=?, cut_reason=?, processed_at=? WHERE url=?",
                        (d.get("cut_category", "אחר"), d.get("cut_reason", ""), store.now_iso(), it["url"]))
            decided.add(i)
        elif act == "attach" and key in existing:
            members.setdefault(key, []).append(it)
            decided.add(i)
        elif act == "story" and key in written:
            members.setdefault(key, []).append(it)
            decided.add(i)
        # "story" on an existing id without its rewrite would lose the new fact: leave undecided, retry.

    # A confirmed report joins the story that confirmed it, as an extra source.
    for key, w in written.items():
        if key not in members:
            continue
        for u in w.get("confirms") or []:
            if u.startswith("u") and u[1:].isdigit() and int(u[1:]) < len(unconfirmed):
                rep = unconfirmed[int(u[1:])]
                if rep["url"] not in {it["url"] for it in members[key]}:
                    members[key].append(rep)

    for key, its in members.items():
        new_sources = [{"name": it["source"], "url": it["url"]} for it in its]
        latest = max(store.item_time(it) for it in its)
        if key in existing:
            s = existing[key]
            srcs = s["sources"] + [x for x in new_sources if x["url"] not in {y["url"] for y in s["sources"]}]
            if key in written:   # material update: new text, moves to the top of the feed
                w = written[key]
                # An item processed late can carry a publish time older than the story; it still moves the story up.
                upd = latest if latest > s["updated"] else store.now_iso()
                con.execute("""UPDATE stories SET headline=?, summary=?, topic=?, urgent=?,
                               updated=?, touched=?, updates=updates+1, sources=? WHERE id=?""",
                            (w["headline"], w["summary"], w["topic"],
                             int(w["urgent"]), upd, store.now_iso(), json.dumps(srcs, ensure_ascii=False), key))
            else:                # attach: same facts, another outlet
                # A story becomes visible once a second outlet confirms it; show it at the confirmation time.
                before = {x["name"] for x in s["sources"]}
                after = {x["name"] for x in srcs}
                # A confirming item processed late can carry an older time; the story still surfaces now.
                confirmed_at = latest if latest > s["updated"] else store.now_iso()
                upd = confirmed_at if len(before) < 2 <= len(after) else s["updated"]
                con.execute("UPDATE stories SET sources=?, touched=?, updated=? WHERE id=?",
                            (json.dumps(srcs, ensure_ascii=False), store.now_iso(), upd, key))
            sid = key
        else:
            w = written[key]
            sid = key_to_id.setdefault(key, f"s{ts}{len(key_to_id)}")
            created = min(store.item_time(it) for it in its)
            con.execute("""INSERT INTO stories(id, headline, summary, action, topic, impact, urgent, created, updated, touched, sources)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (sid, w["headline"], w["summary"], "", w["topic"], "[]",   # action/impact: retired, columns kept
                         int(w["urgent"]), created, latest, store.now_iso(), json.dumps(new_sources, ensure_ascii=False)))
        for it in its:
            con.execute("UPDATE items SET status='story', story_id=?, processed_at=? WHERE url=?",
                        (sid, store.now_iso(), it["url"]))

    # Items the editor skipped or mis-referenced get retried, then dropped.
    _note_attempt(con, [it for i, it in enumerate(items) if i not in decided])
    n_story = sum(len(v) for v in members.values())
    log(f"editor: {total} items, {len(drops)} dropped by triage; {len(items)} edited -> {n_story} in stories, {len(decided) - n_story} cut, "
        f"{len(items) - len(decided)} undecided; {len(written)} stories written; cost ${cost or 0:.3f}")
    return len(items)


def _note_attempt(con, items):
    """Count a failed attempt on these items; drop the ones that have failed MAX_ATTEMPTS times."""
    for it in items:
        if it["attempts"] + 1 >= MAX_ATTEMPTS:
            con.execute("UPDATE items SET status='cut', cut_category='אחר', cut_reason='לא עובד', processed_at=? WHERE url=?",
                        (store.now_iso(), it["url"]))
        else:
            con.execute("UPDATE items SET attempts=attempts+1 WHERE url=?", (it["url"],))
    con.commit()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    con = store.connect()
    run_once(con)
