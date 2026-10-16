"""Send a test notification to the phone, to check the Web Push setup end to end.

  python watchdog.py --test

There is no "the page stopped updating" alert any more (2026-10-16): the phone gets news only, never anything
about how the feed itself is running.
"""
import sys

import notify


def main():
    if "--test" not in sys.argv:
        print("nothing to do: run with --test to send a test notification")
        return
    ok = notify.send("בלי רעש: בדיקה", "ההתראות עובדות. ידיעות דחופות יגיעו לכאן.")
    print("test sent" if ok else "no PUSH_SUBSCRIPTION / VAPID_PRIVATE_KEY: nothing sent")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
