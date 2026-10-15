"""The feed can't report its own death, so a separate hourly workflow looks at the live page: if it hasn't been
updated for over an hour, say so on the phone. It keeps no state, so it alerts in the first hour of a stall and
then once a day while it lasts.

  python watchdog.py          check the live page
  python watchdog.py --test   send a test notification
"""
import json
import sys
import urllib.request
from datetime import datetime, timezone

import notify

STALE_FROM_MIN = 75   # the page deploys at least every 20 minutes; schedules can lag, so allow some slack


def main():
    if "--test" in sys.argv:
        ok = notify.send("בלי רעש: בדיקה", "ההתראות עובדות. ידיעות דחופות יגיעו לכאן, וגם הודעה אם הדף מפסיק להתעדכן.")
        print("test sent" if ok else "no PUSH_SUBSCRIPTION / VAPID_PRIVATE_KEY: nothing sent")
        return
    with urllib.request.urlopen(notify.SITE + "feed.json", timeout=30) as r:
        updated = json.load(r)["updated"]
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(updated)).total_seconds() / 60
    print(f"live page last updated {age:.0f} minutes ago")
    day = 24 * 60
    if STALE_FROM_MIN <= age < STALE_FROM_MIN + 60 or (age >= day and age % day < 60):
        hours = age / 60
        when = f"{round(age)} דקות" if hours < 2 else f"{round(hours)} שעות"
        notify.send("בלי רעש: הדף לא מתעדכן",
                    f"העדכון האחרון היה לפני {when}. כנראה שהעבודה המתוזמנת ב-GitHub נעצרה או נכשלת.",
                    priority=4, url="https://github.com/EyalBeeri/bli-raash/actions")
        print("alert sent")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
