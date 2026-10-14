"""Build a labeling set: group ynet's breaking-news flashes into events and record the editor's verdict on each.

Eyal labels the events blind on the calibration page; the verdicts are revealed afterwards for comparison.
Labeled sets are kept in calib/ and double as a test of the rules: re-run `score` after changing rules.md.

  python calibrate.py build set1      fetch flashes, group, judge -> calib/set1.json + page/calibrate.html
  python calibrate.py page set1       regenerate page/calibrate.html from calib/set1.json
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import editor
import sources

ROOT = Path(__file__).parent
CALIB = ROOT / "calib"
YNET_FLASH = "https://www.ynet.co.il/news/category/184"

SYSTEM = """You are the editor of "בלי רעש", a calm Hebrew news feed for Israeli readers who want to know what matters without drama, fear-mongering or bias.

Below are breaking-news flashes from ynet. Merge flashes only when they report the very same specific event (the same incident, decision or statement, e.g. several updates about one attack). Never group different events because they share a theme, place or category: two different shootings, two different statements or two different accidents are separate events. Every flash belongs to exactly one event; most events have one flash. For each event write a neutral Hebrew title of at most 12 words, and decide under the editorial rules whether it belongs in the feed: verdict "in" or "out", with a short Hebrew reason (at most 12 words), its topic, and for "out" its cut_category. Judge each event on its own; do not assume an earlier feed exists.

EDITORIAL RULES:
"""

SCHEMA = {
    "type": "object",
    "properties": {"events": {"type": "array", "items": {"type": "object", "properties": {
        "items": {"type": "array", "items": {"type": "integer"}},
        "title": {"type": "string"},
        "verdict": {"type": "string", "enum": ["in", "out"]},
        "reason": {"type": "string"},
        "topic": {"type": "string", "enum": editor.TOPICS},
        "cut_category": {"type": "string", "enum": editor.CUT_CATEGORIES},
    }, "required": ["items", "title", "verdict", "reason", "topic"]}}},
    "required": ["events"],
}


def judge(flashes):
    lines = [f"{i} | {f['published'].astimezone(timezone(timedelta(hours=3))):%d.%m %H:%M} | {f['title']} | {f['summary']}"
             for i, f in enumerate(flashes)]
    system = SYSTEM + (ROOT / "rules.md").read_text(encoding="utf-8")
    cmd = ["claude", "-p", "Group and judge the flashes according to your instructions.",
           "--model", editor.MODEL, "--tools", "", "--system-prompt", system,
           "--json-schema", json.dumps(SCHEMA, ensure_ascii=False),
           "--output-format", "json", "--no-session-persistence"]
    r = subprocess.run(cmd, input="FLASHES (i | time | title | text):\n" + "\n".join(lines),
                       capture_output=True, text=True, encoding="utf-8", timeout=900)
    out = json.loads(r.stdout)
    if out.get("is_error") or not out.get("structured_output"):
        raise RuntimeError(f"judge failed: {str(out.get('result'))[:500]}")
    return out["structured_output"]["events"], out.get("total_cost_usd")


def build(name):
    flashes = sources.ynet_flash_page("ynet", YNET_FLASH)
    events, cost = judge(flashes)
    seen, out = set(), []
    for n, e in enumerate(events):
        idx = [i for i in e["items"] if 0 <= i < len(flashes) and i not in seen]
        if not idx:
            continue
        seen.update(idx)
        fl = sorted((flashes[i] for i in idx), key=lambda f: f["published"])
        out.append({"id": f"e{n}", "t": fl[0]["published"].astimezone(timezone.utc).isoformat(timespec="seconds"),
                    "title": e["title"], "verdict": e["verdict"], "reason": e["reason"], "topic": e["topic"],
                    "cut_category": e.get("cut_category", ""),
                    "flashes": [{"title": f["title"], "text": f["summary"][:280], "url": f["url"]} for f in fl]})
    missing = [i for i in range(len(flashes)) if i not in seen]
    out.sort(key=lambda e: e["t"], reverse=True)
    CALIB.mkdir(exist_ok=True)
    data = {"set": name, "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "ynet מבזקים", "flash_count": len(flashes), "events": out}
    (CALIB / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(flashes)} flashes -> {len(out)} events ({sum(e['verdict'] == 'in' for e in out)} in); "
          f"{len(missing)} unassigned; cost ${cost or 0:.3f}")
    page(name)


def page(name):
    data = (CALIB / f"{name}.json").read_text(encoding="utf-8")
    tpl = (ROOT / "page" / "calibrate.template.html").read_text(encoding="utf-8")
    safe = data.replace("</", "<\\/")  # keep the JSON from closing the script tag
    (ROOT / "page" / "calibrate.html").write_text(tpl.replace("/*DATA*/null", safe), encoding="utf-8")
    print("wrote page/calibrate.html")


SCORE_SYSTEM = """You are the editor of "בלי רעש", a calm Hebrew news feed for Israeli readers who want to know what matters without drama, fear-mongering or bias.

Below are news events, each made of one or more ynet breaking-news flashes. For each event decide under the editorial rules whether it belongs in the feed: verdict "in" or "out", with a short Hebrew reason (at most 12 words). Judge each event on its own.

EDITORIAL RULES:
"""

SCORE_SCHEMA = {
    "type": "object",
    "properties": {"verdicts": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "verdict": {"type": "string", "enum": ["in", "out"]}, "reason": {"type": "string"},
    }, "required": ["id", "verdict", "reason"]}}},
    "required": ["verdicts"],
}


def score(name):
    """Re-judge a labeled set under the current rules.md and compare with Eyal's answers."""
    data = json.loads((CALIB / f"{name}.json").read_text(encoding="utf-8"))
    labels = json.loads((CALIB / f"{name}_labels.json").read_text(encoding="utf-8"))["labels"]
    lines = []
    for e in data["events"]:
        titles = " / ".join(f["title"] for f in e["flashes"])
        lines.append(f"{e['id']} | {titles} | {e['flashes'][0]['text']}")
    cmd = ["claude", "-p", "Judge the events according to your instructions.",
           "--model", editor.MODEL, "--tools", "",
           "--system-prompt", SCORE_SYSTEM + (ROOT / "rules.md").read_text(encoding="utf-8"),
           "--json-schema", json.dumps(SCORE_SCHEMA, ensure_ascii=False),
           "--output-format", "json", "--no-session-persistence"]
    r = subprocess.run(cmd, input="EVENTS (id | flash titles | first flash text):\n" + "\n".join(lines),
                       capture_output=True, text=True, encoding="utf-8", timeout=900)
    out = json.loads(r.stdout)
    if out.get("is_error") or not out.get("structured_output"):
        raise RuntimeError(f"score failed: {str(out.get('result'))[:500]}")
    got = {v["id"]: v for v in out["structured_output"]["verdicts"]}
    titles = {e["id"]: e["flashes"][0]["title"] for e in data["events"]}
    judged = [i for i, l in labels.items() if l.get("v") in ("in", "out") and i in got]
    wrong = [i for i in judged if got[i]["verdict"] != labels[i]["v"]]
    print(f"{name}: agree {len(judged) - len(wrong)}/{len(judged)}  (missing verdicts: {len(labels) - len(judged)}); "
          f"cost ${out.get('total_cost_usd') or 0:.3f}")
    for i in wrong:
        print(f"  Eyal {labels[i]['v']:3} / editor {got[i]['verdict']:3} | {titles[i][:70]} | {got[i]['reason']}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    {"build": build, "page": page, "score": score}[sys.argv[1]](sys.argv[2])
