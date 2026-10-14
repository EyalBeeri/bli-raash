"""Prints one line each time a new pending batch appears (for the publisher session's Monitor)."""
import json
import sys
import time
from pathlib import Path

PENDING = Path(__file__).parent / "data" / "out" / "pending.json"
last = None
while True:
    try:
        p = json.loads(PENDING.read_text(encoding="utf-8"))
        if p["seq"] != last:
            last = p["seq"]
            print(f"PENDING seq={p['seq']} docs={','.join(p['docs'])}", flush=True)
    except FileNotFoundError:
        last = None
    except Exception as e:  # half-written file: try again next tick
        print(f"watch error: {e}", file=sys.stderr, flush=True)
    time.sleep(5)
