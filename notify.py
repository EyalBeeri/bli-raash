"""Phone notifications, delivered by the installed בלי רעש app itself (Web Push): urgent stories as they're
confirmed, and nothing else. How the feed itself is running (a stuck editor, an outlet that stopped answering,
a usage limit) never goes to the phone: that stays in the workflow logs.

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
