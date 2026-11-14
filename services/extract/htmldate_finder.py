"""Step 2: run every htmldate check and record what each one finds.

htmldate.find_date() stops at the first check that works and only hands back
the date. This file runs htmldate's own 12 checks one at a time, in the same
order find_date() does (mirrors find_date in htmldate 1.11.0, extensive search
on), but keeps going after the first hit. Every check reports the date it
found, what it matched, and the debug lines htmldate logged along the way.

The first check that hits is what htmldate alone would return. That is
compared against a plain find_date() call as a safety net: if the two ever
disagree (say, after an htmldate upgrade), find_date() wins and the result is
flagged as unverified.
"""

import logging
import threading
from contextlib import contextmanager

from htmldate import core, extractors
from htmldate.settings import CLEANING_LIST, MAX_SEGMENT_LEN, MIN_SEGMENT_LEN
from htmldate.utils import Extractor, clean_html, load_html
from htmldate.validators import check_extracted_reference, get_max_date, get_min_date

# The checks, in the order htmldate tries them. "strength" is how much to trust
# a date that comes from that check.
STEPS = [
    ("url", "Date in the URL", "strong",
     "The address has a year, month and day in it."),
    ("meta", "Meta tags in the page head", "strong",
     "Tags like article:published_time that the site sets for search engines and social cards."),
    ("jsonld", "JSON-LD structured data", "strong",
     "The datePublished / dateModified fields in the page's schema.org block."),
    ("abbr", "Abbr timestamp elements", "decent",
     "Older blog and Facebook-style markup that stores a timestamp on an abbr tag."),
    ("markup", "Elements labelled as dates", "decent",
     "Visible elements whose class or id says date, byline, published, footer and so on."),
    ("title", "Title and main heading", "decent",
     "A date written into the page title or the h1."),
    ("time", "Time elements", "decent",
     "The datetime attribute or text of time tags."),
    ("timestamp", "Full timestamp in the HTML", "decent",
     "The first complete timestamp (date plus time) anywhere in the source."),
    ("image", "Date in the share image URL", "weak",
     "A year/month/day folder in the og:image address, usually the upload date."),
    ("phrase", "Phrases like \"Published on 3/2/2024\"", "decent",
     "A numeric date right after words such as published, updated or posted."),
    ("text", "Dates in the body text", "weak",
     "Dates written out in paragraphs. The earliest or latest one wins."),
    ("scan", "Pattern scan of the whole page", "weak",
     "Last resort: every date-shaped string in the HTML is counted and the most likely one is picked. Can fall back to the copyright year."),
]

# htmldate debug lines that are noise for a human reader
NOISE = (
    "custom parse test", "send to external parser", "not an ISO date",
    "dateutil parsing error", "8-digit error", "not a year pattern",
    "no potential year", "YYYYMMDD value error", "regex value error",
    "Y-M value error", "extensive search started",
)


# --- capture htmldate's debug log, per thread ------------------------------

class _ThreadCapture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.local = threading.local()

    def emit(self, record):
        buffer = getattr(self.local, "buffer", None)
        if buffer is not None:
            buffer.append(record.getMessage())


_capture_handler = _ThreadCapture()
_htmldate_logger = logging.getLogger("htmldate")
_htmldate_logger.addHandler(_capture_handler)
_htmldate_logger.setLevel(logging.DEBUG)
_htmldate_logger.propagate = False


@contextmanager
def _capture_logs():
    buffer = []
    _capture_handler.local.buffer = buffer
    try:
        yield buffer
    finally:
        _capture_handler.local.buffer = None


def _readable(logs: list[str]) -> list[str]:
    lines = [line for line in logs if not line.startswith(NOISE)]
    return [line if len(line) <= 300 else line[:300] + "…" for line in lines][-8:]


# --- small helpers for evidence --------------------------------------------

def _first_match(elements, options):
    """Same as htmldate's examine_elements, but also returns the element that matched."""
    if not core.has_plausible_candidates(elements):
        return None, None
    for elem in elements:
        for text in [elem.text_content(), elem.get("title", "")]:
            attempt = core.examine_text(text, options)
            if attempt:
                return attempt, elem
    return None, None


def _describe(elem) -> str | None:
    if elem is None:
        return None
    attrs = "".join(f' {key}="{elem.get(key)}"' for key in ("id", "class", "itemprop") if elem.get(key))
    text = " ".join(elem.text_content().split())
    return f"<{elem.tag}{attrs}>{text[:160]}</{elem.tag}>"


def _around(text: str, match, pad: int = 60) -> str:
    start, end = max(0, match.start() - pad), min(len(text), match.end() + pad)
    return " ".join(text[start:end].split())


def _last_line(logs: list[str], *needles: str) -> str | None:
    for line in reversed(logs):
        if any(needle in line for needle in needles):
            return line
    return None


# --- the traced run ---------------------------------------------------------

def run_all_checks(html: str, url: str, original: bool) -> dict:
    """original=True makes each check look for the publish date, False for the last update."""
    options = Extractor(True, get_max_date(None), get_min_date(None), original, "%Y-%m-%d")
    tree = load_html(html)
    if tree is None:
        return {"htmldate_date": None, "htmldate_check": None, "steps": [], "verified": True}

    cache = {}

    def pruned():
        # htmldate strips scripts, styles, nav etc. before the visible-content checks
        if "tree" not in cache:
            try:
                cache["tree"] = extractors.discard_unwanted(clean_html(tree, CLEANING_LIST))
            except ValueError:
                cache["tree"] = tree
        return cache["tree"]

    def page():
        if "html" not in cache:
            cache["html"] = core.serialize(pruned())
        return cache["html"]

    def check_url(logs):
        return extractors.extract_url_date(url, options), url

    def check_meta(logs):
        date = core.examine_header(tree, options)
        tag = _last_line(logs, "examining meta")
        evidence = tag.split(": ", 1)[1] if tag else None
        if evidence and _last_line(logs, "opting for reserve date"):
            evidence += "  (fallback tag, no exact match for this date type)"
        return date, evidence

    def check_jsonld(logs):
        date = extractors.json_search(tree, options)
        pattern = extractors.JSON_PUBLISHED if original else extractors.JSON_MODIFIED
        for elem in tree.xpath('.//script[@type="application/ld+json" or @type="application/settings+json"]'):
            match = pattern.search(elem.text or "")
            if match:
                return date, match[0] + '"'
        return date, None

    def check_abbr(logs):
        return core.examine_abbr_elements(tree, options), _last_line(logs, "found")

    def check_markup(logs):
        date, elem = _first_match(core.date_candidates(pruned(), True), options)
        return date, _describe(elem)

    def check_title(logs):
        date, elem = _first_match(pruned().xpath(".//title|.//h1"), options)
        return date, _describe(elem)

    def check_time(logs):
        date = core.examine_time_elements(pruned(), options)
        seen = [line.split(": ", 1)[1] for line in logs if "time/datetime found" in line]
        winner = next((value for value in seen if date and date in value), None)
        if winner:
            evidence = f'<time datetime="{winner}">'
        else:
            evidence = "; ".join(seen[:4]) or None
        if evidence and len(seen) > 1:
            pick = "earliest" if original else "latest"
            evidence += f"  ({len(seen)} time elements on the page, the {pick} one wins)"
        return date, evidence

    def check_timestamp(logs):
        date = extractors.pattern_search(page(), core.TIMESTAMP_PATTERN, options)
        match = core.TIMESTAMP_PATTERN.search(page())
        return date, _around(page(), match) if match else None

    def check_image(logs):
        date = extractors.img_search(pruned(), options)
        element = pruned().find('.//meta[@property="og:image"][@content]')
        return date, element.get("content") if element is not None else None

    def check_phrase(logs):
        date = extractors.idiosyncrasies_search(page(), options)
        match = extractors.TEXT_PATTERNS.search(page())
        return date, _around(page(), match, pad=30) if match else None

    def check_text(logs):
        reference, winner = None, None
        for segment in extractors.FREE_TEXT_EXPRESSIONS(pruned()):
            segment = segment.strip()
            if not MIN_SEGMENT_LEN < len(segment) < MAX_SEGMENT_LEN:
                continue
            updated = core.compare_reference(reference, segment, options)
            if updated != reference:
                winner = segment
            reference = updated
        return check_extracted_reference(reference, options), winner

    def check_scan(logs):
        date = core.search_page(page(), options)
        if _last_line(logs, "using copyright year as default"):
            return date, "Only a copyright year was found, so the date defaults to January 1 of that year."
        if _last_line(logs, "switching to one component"):
            return date, "Only a bare year was found in the page, so the date defaults to January 1."
        if _last_line(logs, "multilingual text found"):
            return date, "A written-out date (month name, day, year) somewhere in the page source."
        if _last_line(logs, "switching to two components"):
            return date, "A month and year with no day, so the day defaults to the 1st."
        return date, "The numeric year/month/day pattern that shows up most plausibly in the page source."

    checks = {
        "url": check_url, "meta": check_meta, "jsonld": check_jsonld, "abbr": check_abbr,
        "markup": check_markup, "title": check_title, "time": check_time,
        "timestamp": check_timestamp, "image": check_image, "phrase": check_phrase,
        "text": check_text, "scan": check_scan,
    }

    # run all 12, no early exit
    steps = []
    for key, label, strength, about in STEPS:
        with _capture_logs() as logs:
            try:
                date, evidence = checks[key](logs)
            except Exception as err:  # one odd page shouldn't sink the whole trace
                date, evidence = None, None
                logs.append(f"check failed: {err}")
        steps.append({
            "key": key, "label": label, "strength": strength, "about": about,
            "status": "hit" if date else "miss",
            "date": date,
            "evidence": evidence if date else None,
            "log": _readable(logs),
        })

    # what htmldate alone returns = the first check that hit
    first_hit = next((step for step in steps if step["date"]), None)
    expected = core.find_date(html, url=url, original_date=original)
    verified = expected == (first_hit["date"] if first_hit else None)

    return {
        "htmldate_date": expected,
        "htmldate_check": first_hit["key"] if first_hit and verified else None,
        "steps": steps,
        "verified": verified,
    }
