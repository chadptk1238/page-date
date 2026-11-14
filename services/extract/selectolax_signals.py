"""Step 3: list every raw date signal on the page with selectolax.

htmldate stops at the first check that works. This is the wider view: all the
places the page states a date (HTTP header, URL, meta tags, JSON-LD, time tags)
so you can see whether they agree with the date htmldate picked.
"""

import json
import re
from datetime import datetime
from email.utils import parsedate_to_datetime

from htmldate import extractors
from htmldate.validators import get_max_date, get_min_date
from selectolax.lexbor import LexborHTMLParser

META_KEY = re.compile(r"date|time|publish|modif|created|updated|issued")
JSON_KEYS = ("datePublished", "dateModified", "dateCreated", "uploadDate")
JSON_FALLBACK = re.compile(r'"(datePublished|dateModified|dateCreated|uploadDate)"\s*:\s*"([^"]+)"')
ISO_PREFIX = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:\D|$)")
MAX_TIME_TAGS = 8


def _parse(raw: str) -> str | None:
    """Normalise any date string to YYYY-MM-DD: ISO prefix first, then htmldate's own parser."""
    iso = ISO_PREFIX.match(raw.strip())
    if iso:
        try:
            return datetime(int(iso[1]), int(iso[2]), int(iso[3])).strftime("%Y-%m-%d")
        except ValueError:
            pass
    try:
        return extractors.try_date_expr(raw, "%Y-%m-%d", False, get_min_date(None), get_max_date(None))
    except Exception:
        return None


def _walk_json(node, found: list):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in JSON_KEYS and isinstance(value, str):
                found.append((key, value))
            else:
                _walk_json(value, found)
    elif isinstance(node, list):
        for item in node:
            _walk_json(item, found)


def collect_signals(html: str, url: str, last_modified: str | None) -> list[dict]:
    signals, listed = [], set()

    def add(source: str, name: str, raw: str, date: str | None = None):
        date = date or _parse(raw)
        if date and (source, name, date) not in listed:  # skip repeats of the same signal
            listed.add((source, name, date))
            signals.append({"source": source, "name": name, "raw": raw.strip()[:200], "date": date})

    # HTTP header
    if last_modified:
        try:
            add("HTTP header", "Last-Modified", last_modified,
                parsedate_to_datetime(last_modified).strftime("%Y-%m-%d"))
        except (TypeError, ValueError):
            pass

    # URL
    match = extractors.COMPLETE_URL.search(url)
    if match:
        add("URL", "path", match[0].strip("/"), f"{match[1]}-{int(match[2]):02d}-{int(match[3]):02d}")

    tree = LexborHTMLParser(html)

    # meta tags
    for node in tree.css("meta"):
        attrs = node.attributes
        key = attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or attrs.get("http-equiv")
        content = attrs.get("content") or attrs.get("datetime")
        if key and content and META_KEY.search(key.lower()):
            add("Meta tag", key, content)

    # JSON-LD
    for node in tree.css('script[type="application/ld+json"]'):
        text = node.text() or ""
        found = []
        try:
            _walk_json(json.loads(text), found)
        except ValueError:
            found = JSON_FALLBACK.findall(text)
        for key, value in found:
            add("JSON-LD", key, value)

    # time tags
    seen = set()
    for node in tree.css("time"):
        attr = node.attributes.get("datetime")
        raw = attr or node.text(strip=True)
        if raw and raw not in seen and len(seen) < MAX_TIME_TAGS:
            seen.add(raw)
            add("Time tag", "datetime" if attr else "text", raw)

    return signals


def page_context(html: str) -> dict:
    """What kind of page this is, in a few fields. Jev reads this to judge the candidates."""
    tree = LexborHTMLParser(html)

    def meta(key: str) -> str | None:
        node = tree.css_first(f'meta[property="{key}"], meta[name="{key}"]')
        return (node.attributes.get("content") or "").strip()[:300] or None if node else None

    title = tree.css_first("title")
    heading = tree.css_first("h1")
    context = {
        "title": title.text(strip=True)[:200] if title else None,
        "heading": heading.text(strip=True)[:200] if heading else None,
        "type": meta("og:type"),
        "site": meta("og:site_name"),
        "description": meta("og:description") or meta("description"),
    }

    # opening of the visible text, with the non-content tags removed
    tree.strip_tags(["script", "style", "noscript", "svg", "template", "nav"])
    body = tree.body
    context["text_start"] = " ".join(body.text(separator=" ").split())[:1200] if body else None
    return {key: value for key, value in context.items() if value}
