"""Phone notifications, delivered by the installed בלי רעש app itself (Web Push): urgent stories as they're
confirmed, and an alert when the feed stalls (the editor stops working, or an outlet stops answering for hours).

Two secrets: PUSH_SUBSCRIPTION (the phone's subscription, from the page's ?notify setup) and VAPID_PRIVATE_KEY
(signs our pushes; its public half is in page/index.html). Without them nothing is sent. Everything here is
best effort: a failed notification is logged and never stops the feed.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import store

SITE = "https://eyalbeeri.github.io/bli-raash/"
URGENT_WINDOW_MIN = 90      # only stories confirmed this recently are pushed (a lost cache mustn't replay old ones)
EDITOR_STALL_MIN = 60       # items waiting this long with no working editor run: alert
SOURCE_STALL_HOURS = 3      # an outlet failing this long: alert
REALERT_HOURS = 6           # while a problem lasts, remind at most this often


def send(title, message, priority=3, url=SITE):
    """Returns True if a notification went out. priority 4+ is urgent: delivered at once, and dropped after an
    hour if the phone stays offline (old urgent news isn't urgent); anything lower waits up to a day."""
    sub, key = os.environ.get("PUSH_SUBSCRIPTION"), os.environ.get("VAPID_PRIVATE_KEY")
    if not sub or not key:
        return False
    from pywebpush import WebPushException, webpush
    payload = json.dumps({"title": title, "body": message, "url": url, "tag": title[:40]}, ensure_ascii=False)
    try:
        webpush(json.loads(sub), payload, vapid_private_key=key, vapid_claims={"sub": "https://eyalbeeri.github.io"},  # contact: an origin, no path
                ttl=3600 if priority >= 4 else 86400, headers={"Urgency": "high" if priority >= 4 else "normal"},
                timeout=15)
    except WebPushException as e:
        code = getattr(e.response, "status_code", None)
        if code in (404, 410):
            raise RuntimeError("the phone's subscription has expired: open the page with ?notify and send the new code")
        raise
    return True


def _now():
    return datetime.now(timezone.utc)


def _since(con, key):
    v = store.get_meta(con, key)
    return datetime.fromisoformat(v) if v else None


def push_urgent(con, stories, log=print):
    """Push each urgent story once, when it first appears on the page (i.e. once it is confirmed)."""
    pushed = set(json.loads(store.get_meta(con, "pushed_urgent", "[]")))
    recent = (_now() - timedelta(minutes=URGENT_WINDOW_MIN)).isoformat(timespec="seconds")
    for s in stories:
        if not s.get("urgent") or s["id"] in pushed or s["updated"] < recent:
            continue
        try:
            if send(s["headline"], s["summary"][:400], priority=4):
                log(f"pushed urgent: {s['headline'][:60]}")
            pushed.add(s["id"])
        except Exception as e:   # try again next run, while the story is still recent
            log(f"push failed: {type(e).__name__}: {str(e)[:120]}")
    store.set_meta(con, "pushed_urgent", json.dumps(sorted(pushed)[-300:]))


def _alert(con, key, title, message, log, priority=4):
    """Send a problem alert, then stay quiet about the same problem for REALERT_HOURS."""
    last = _since(con, key)
    if last and _now() - last < timedelta(hours=REALERT_HOURS):
        return
    try:
        send(title, message, priority=priority)
        store.set_meta(con, key, store.now_iso())
        log(f"alert sent: {title}")
    except Exception as e:
        log(f"alert failed: {type(e).__name__}: {str(e)[:120]}")


def _recovered(con, key, title, message, log):
    if store.get_meta(con, key):
        store.set_meta(con, key, "")
        try:
            send(title, message, priority=2)
            log(f"recovery sent: {title}")
        except Exception as e:
            log(f"recovery note failed: {type(e).__name__}: {str(e)[:120]}")


def check_editor(con, waiting, worked, error, log=print):
    """`worked`: an editor run succeeded this cycle. `error`: the last failure, if any."""
    if worked or not waiting:
        store.set_meta(con, "editor_ok_at", store.now_iso())
        _recovered(con, "alerted_editor", "בלי רעש: הסינון חזר לעבוד", "העריכה פועלת שוב והדף מתעדכן.", log)
        return
    ok_at = _since(con, "editor_ok_at")
    if not ok_at:
        store.set_meta(con, "editor_ok_at", store.now_iso())
        return
    minutes = (_now() - ok_at).total_seconds() / 60
    if minutes >= EDITOR_STALL_MIN:
        _alert(con, "alerted_editor", "בלי רעש: הסינון תקוע",
               f"{waiting} ידיעות מחכות לעריכה כבר {round(minutes)} דקות. השגיאה האחרונה: {(error or 'לא ידועה')[:200]}", log)


def check_sources(con, health, log=print):
    """`health`: url -> {source, ok, ...} from sources.fetch_all. An outlet is down when all its feeds failed."""
    outlets = {}
    for h in health.values():
        o = outlets.setdefault(h["source"], {"ok": False, "error": ""})
        o["ok"] = o["ok"] or h["ok"]
        o["error"] = o["error"] or h.get("error", "")
    for name, o in outlets.items():
        key = f"source_down:{name}"
        if o["ok"]:
            store.set_meta(con, key, "")
            _recovered(con, f"alerted_source:{name}", f"בלי רעש: {name} חזר", f"{name} עונה שוב.", log)
            continue
        down = _since(con, key)
        if not down:
            store.set_meta(con, key, store.now_iso())
        elif _now() - down >= timedelta(hours=SOURCE_STALL_HOURS):
            hours = round((_now() - down).total_seconds() / 3600)
            _alert(con, f"alerted_source:{name}", f"בלי רעש: {name} לא עונה",
                   f"כבר {hours} שעות אין ידיעות מ{name}. {o['error'][:150]}", log, priority=3)
