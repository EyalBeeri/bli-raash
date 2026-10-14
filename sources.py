"""Fetch raw items from Israeli news outlets: RSS where it is live, page scraping where it isn't.

Every fetcher returns a list of dicts: {source, url, title, summary, published}
where `published` is an aware datetime or None (None = use first-seen time).
"""
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
IL = ZoneInfo("Asia/Jerusalem")  # naive timestamps from Israeli sites are local time (DST-aware)


def _get(url, timeout=20, hops=3):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "he,en;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        # Python 3.10's urllib doesn't follow 308 (Maariv uses it).
        loc = e.headers.get("Location")
        if e.code in (307, 308) and loc and hops:
            return _get(urllib.parse.urljoin(url, loc), timeout, hops - 1)
        raise


def _clean(s):
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _date(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo:
        return d
    # RFC 2822 "-0000" means UTC; any other zoneless time from these sites is Israel local time.
    return d.replace(tzinfo=timezone.utc if s.endswith("-0000") else IL)


def rss(source, url):
    raw = _get(url)
    # Kan declares utf-16 but serves utf-8; drop the declaration and decode ourselves.
    text = raw.decode("utf-8-sig", errors="replace")
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
    root = ET.fromstring(text)
    out = []
    for it in root.iter("item"):
        title = _clean(it.findtext("title"))
        if not title:
            continue
        out.append({
            "source": source,
            "url": (it.findtext("link") or it.findtext("guid") or "").strip(),
            "title": title,
            "summary": _clean(it.findtext("description"))[:400],
            "published": _date(it.findtext("pubDate")),
        })
    return out


def walla_breaking(source, url):
    """Walla's RSS is stale; its breaking-news page lists items in JSON-LD (no timestamps)."""
    s = _get(url).decode("utf-8", errors="replace")
    out = []
    for block in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', s, re.S):
        try:
            d = json.loads(block)
        except Exception:
            continue
        for block_d in (d if isinstance(d, list) else [d]):
            main = block_d.get("mainEntity") if isinstance(block_d, dict) else None
            for el in (main.get("itemListElement", []) if isinstance(main, dict) else []):
                if not (isinstance(el, dict) and el.get("url") and el.get("name")):
                    continue
                out.append({"source": source, "url": el["url"], "title": _clean(el["name"]),
                            "summary": "", "published": None})
    return out


def ynet_flash_page(source, url):
    """ynet's breaking-news page: the last ~100 flashes with full text (the RSS has only 30, no text)."""
    s = _get(url).decode("utf-8", errors="replace")
    out = []
    for block in s.split('class="special-page-flash-item')[1:]:
        m_id = re.search(r'data_analytics_id="([^"]+)"', block)
        m_title = re.search(r'<div class="slotTitle">(.*?)</div>', block, re.S)
        m_time = re.search(r'dateTime="([^"]+)"', block)
        m_text = re.search(r'<div class="slotText">(.*?)</div>', block, re.S)
        if not (m_id and m_title):
            continue
        out.append({"source": source, "url": f"https://www.ynet.co.il/news/article/{m_id.group(1)}",
                    "title": _clean(m_title.group(1)), "summary": _clean(m_text.group(1))[:400] if m_text else "",
                    "published": _date(m_time.group(1)) if m_time else None})
    return out


def n12_home(source, url):
    """N12's RSS is stale; the home page embeds its teasers in __NEXT_DATA__."""
    s = _get(url).decode("utf-8", errors="replace")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', s, re.S)
    if not m:
        return []
    out, seen = [], set()

    def find_url(o):
        if isinstance(o, dict):
            for k in ("clicked_item_url", "link", "url", "shareUrl"):
                v = o.get(k)
                if isinstance(v, str) and re.search(r"(mako|n12)\.co\.il/.+\.htm", v):
                    return v
            for v in o.values():
                u = find_url(v)
                if u:
                    return u
        elif isinstance(o, list):
            for v in o:
                u = find_url(v)
                if u:
                    return u
        return None

    def walk(o):
        if isinstance(o, dict):
            t, d = o.get("title"), o.get("date")
            title = t.get("text") if isinstance(t, dict) else (t if isinstance(t, str) else None)
            when = None
            if isinstance(d, dict) and d.get("datetime"):
                when = _date(d["datetime"])
            elif isinstance(o.get("publishDate"), (int, float)):
                when = datetime.fromtimestamp(o["publishDate"] / 1000, tz=timezone.utc)
            if title and when:
                u = find_url(o)
                if u and u not in seen and "/news-shorts/" not in u:
                    seen.add(u)
                    sub = o.get("subTitle") or o.get("description") or ""
                    sub = sub.get("text", "") if isinstance(sub, dict) else sub
                    out.append({"source": source, "url": u, "title": _clean(title),
                                "summary": _clean(sub)[:400], "published": when})
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(json.loads(m.group(1)))
    return out


# name shown to readers, fetcher, url. Channel 14 is excluded by the user's decision.
SOURCES = [
    ("ynet", rss, "https://www.ynet.co.il/Integration/StoryRss2.xml"),
    ("ynet", ynet_flash_page, "https://www.ynet.co.il/news/category/184"),
    ("הארץ", rss, "https://www.haaretz.co.il/srv/htz---all-articles"),
    ("ישראל היום", rss, "https://www.israelhayom.co.il/rss.xml"),
    ("מעריב", rss, "https://www.maariv.co.il/Rss/RssFeedsMivzakiChadashot"),
    ("כאן", rss, "https://www.kan.org.il/api/newsflash/v2/Newsflash"),
    ("וואלה", walla_breaking, "https://www.walla.co.il/news/breaking-news"),
    ("N12", n12_home, "https://www.n12.co.il/"),
    ("גלובס", rss, "https://www.globes.co.il/webservice/rss/rssfeeder.asmx/FeederNode?iID=2"),
    ("דבר", rss, "https://www.davar1.co.il/feed/"),
    ("Times of Israel", rss, "https://www.timesofisrael.com/feed/"),
]


# Seconds between checks for sources that don't like frequent visits (default: every loop).
MIN_INTERVAL = {"https://www.kan.org.il/api/newsflash/v2/Newsflash": 600}
_next_ok = {}    # url -> earliest time to try again
_failures = {}   # url -> consecutive failures, for backoff


def fetch_all():
    """Returns (items, health) where health maps each checked feed url to {source, ok, count, error}.
    A source that fails is left alone for 2, 4, 8... up to 30 minutes, so a refusal is never hammered."""
    import time
    items, health = [], {}
    now = time.time()
    for name, fn, url in SOURCES:
        if now < _next_ok.get(url, 0):
            health[url] = {"source": name, "ok": _failures.get(url, 0) == 0, "count": 0, "skipped": True}
            continue
        try:
            got = fn(name, url)
            if not got:
                raise ValueError("no items found (page layout may have changed)")
            items.extend(got)
            health[url] = {"source": name, "ok": True, "count": len(got)}
            _failures[url] = 0
            _next_ok[url] = now + MIN_INTERVAL.get(url, 0)
        except Exception as e:
            health[url] = {"source": name, "ok": False, "count": 0, "error": f"{type(e).__name__}: {e}"[:200]}
            _failures[url] = _failures.get(url, 0) + 1
            _next_ok[url] = now + max(MIN_INTERVAL.get(url, 0), min(1800, 60 * 2 ** _failures[url]))
    return items, health


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    items, health = fetch_all()
    now = datetime.now(timezone.utc)
    for url, h in health.items():
        mine = [i for i in items if i["source"] == h["source"] and i["published"]]
        newest = max((i["published"] for i in mine), default=None)
        age = f"{(now - newest).total_seconds() / 60:.0f} min" if newest else "n/a"
        print(f"{h['source']:16} ok={h['ok']} count={h['count']:3} newest={age:>8}  {h.get('error', '')}")
