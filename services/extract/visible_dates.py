"""Step 3b: every date written in the visible page, with where it sits.

htmldate's checks each return one date, and the raw signals only cover what a
site states in tags. Neither reliably catches the byline under the headline
("Published on Wednesday, September 11, 2024"). This scan reads the page the
way a person does: every date in the visible text and in <time> tags, each with

  region                  article_meta, article, chrome or unknown
  label                   the word right before it: Published, Posted, Updated...
  elements_from_headline  how far it sits from the h1, in document order
  same_as_fetch_day       true for a masthead clock or "today" widget
  raw                     the text around it

Uses lxml (htmldate already depends on it) and htmldate's own date parser.
"""

import re
from datetime import datetime
from functools import lru_cache

from htmldate import extractors
from htmldate.utils import load_html
from htmldate.validators import get_max_date, get_min_date
from lxml import etree

# --- dates as people write them ---------------------------------------------

MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
)
DATE_IN_TEXT = re.compile(
    rf"\b{MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b"      # Nov 14, 2026 / Sept. 3, 2025
    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTH},?\s+\d{{4}}\b"     # 14 November 2026
    r"|\b\d{4}-\d{2}-\d{2}\b"                                    # 2026-11-14
    r"|\b\d{1,2}/\d{1,2}/\d{4}\b",                               # 11/14/2026
    re.IGNORECASE,
)
HAS_YEAR = re.compile(r"(?:19|20)\d\d")
US_SLASH = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})$")

# --- where a date sits --------------------------------------------------------
# Matched against the element's lowercased "class id". The lookarounds stop
# short words matching inside longer ones ("ad" in "header", "nav" in "canvas").

ARTICLE_META = re.compile(
    r"(?:entry|post|article|story|news|single)[-_]*(?:header|meta|title|byline|info|date|details|head)(?![a-z])"
    r"|(?<![a-z])(?:byline|dateline|posted[-_]on|post[-_]date|entry[-_]date|article[-_]date|published)(?![a-z])"
)
CHROME = re.compile(
    r"(?<![a-z])(?:site[-_]header|masthead|site[-_]branding|branding|logo|top[-_]?bar|navbar|nav|menu|footer|"
    r"sidebar|widgets?|related|recent|popular|trending|more[-_]news|breadcrumbs?|ticker|copyright|newsletter|"
    r"subscribe|ads?|advert\w*|sponsor\w*|promo|share|social|comments?)(?![a-z])"
)
ARTICLE = re.compile(
    r"(?<![a-z])(?:article|entry[-_]content|post[-_]content|article[-_]body|story[-_]body|content|main)(?![a-z])"
)
CHROME_TAGS = {"header", "nav", "footer", "aside"}
ARTICLE_TAGS = {"article", "main"}

# --- the word right before a date ---------------------------------------------
# Checked against only the ~50 characters before the date; the nearest one wins.

LABEL_WINDOW = 50
LABELS = [
    ("Updated", re.compile(r"\b(?:last\s+)?updated\b", re.I)),
    ("Modified", re.compile(r"\bmodified\b", re.I)),
    ("Published", re.compile(r"\bpublished\b", re.I)),
    ("Posted", re.compile(r"\bposted\b", re.I)),
    ("Released", re.compile(r"\breleased\b", re.I)),
    ("Date", re.compile(r"\bdate\s*:", re.I)),
    ("By-line", re.compile(r"\bby\s+[A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){0,3},?\s+on\b")),
    ("Newswire dateline", re.compile(r"business\s*wire", re.I)),
]
# PR Newswire and GlobeNewswire put their mark just after the date: "DALLAS, Sept. 16, 2026 /PRNewswire/ --"
WIRE_AFTER = re.compile(r"^\W{0,3}(?:/PRNewswire|\(?GLOBE\s*NEWSWIRE|\(?BUSINESS\s*WIRE)", re.I)


@lru_cache(maxsize=4096)
def _parse(text: str) -> str | None:
    """Any date string to YYYY-MM-DD, or None if it isn't a believable date."""
    text = text.strip()
    if not any(char.isdigit() for char in text):  # "today", "yesterday": relative to the clock, not the page
        return None
    low, high = get_min_date(None), get_max_date(None)
    slash = US_SLASH.match(text)
    if slash:  # read 3/4/2025 the US way, month first, when that is a real date
        try:
            parsed = datetime(int(slash[3]), int(slash[1]), int(slash[2]))
            if low <= parsed <= high:
                return parsed.strftime("%Y-%m-%d")
        except ValueError:
            pass
    try:
        return extractors.try_date_expr(text, "%Y-%m-%d", True, low, high)
    except Exception:
        return None


def _squash(text: str) -> str:
    return " ".join(text.split())


def _names(elem) -> str:
    return f"{elem.get('class') or ''} {elem.get('id') or ''}".lower()


def _contains(ancestor, elem) -> bool:
    return elem is not None and (ancestor is elem or any(node is ancestor for node in elem.iterancestors()))


def _region(elem, headline) -> str:
    """Walk up from the element; the nearest ancestor that says something wins."""
    for node in [elem, *elem.iterancestors()]:
        tag, names = node.tag, _names(node)
        if tag in ("html", "body"):
            break
        if ARTICLE_META.search(names):
            return "article_meta"
        if tag in CHROME_TAGS or CHROME.search(names):
            if tag == "header" and (
                _contains(node, headline) or any(parent.tag == "article" for parent in node.iterancestors())
            ):
                return "article_meta"  # the article's own header, not the site's
            if not _contains(node, headline):
                return "chrome"
            # a "sidebar"-classed wrapper around the whole article is a layout class, keep walking
        if tag in ARTICLE_TAGS or ARTICLE.search(names):
            return "article"
    return "unknown"


def _label(before: str, after: str) -> str | None:
    window = before[-LABEL_WINDOW:]
    best, best_end = None, -1
    for name, pattern in LABELS:
        for match in pattern.finditer(window):
            if match.end() > best_end:
                best, best_end = name, match.end()
    if best is None and WIRE_AFTER.search(after[:30]):
        return "Newswire dateline"
    return best


def _text_before(elem) -> str:
    """The text of the parent that comes right before this element."""
    parent = elem.getparent()
    if parent is None:
        return ""
    pieces = [parent.text or ""]
    for sibling in parent:
        if sibling is elem:
            break
        if isinstance(sibling.tag, str):
            pieces.append(sibling.text_content())
        pieces.append(sibling.tail or "")
    return " ".join(pieces)[-LABEL_WINDOW:] + " "  # spaces, so "…Expansion" + "Published" stay two words


def _find_headline(tree):
    """The h1 whose words best overlap og:title or <title>; else the first h1 (h2s if the page has no h1)."""
    headings = tree.xpath("//h1") or tree.xpath("//h2")  # some themes put the headline in an h2
    if not headings:
        return None
    title = tree.xpath('string(//meta[@property="og:title"]/@content)') or tree.xpath("string(//title)")
    wanted = set(re.findall(r"\w+", title.lower()))
    if not wanted:
        return headings[0]

    def overlap(heading) -> float:
        words = set(re.findall(r"\w+", heading.text_content().lower()))
        return len(words & wanted) / len(words | wanted) if words else 0.0

    best = max(headings, key=overlap)
    return best if overlap(best) > 0 else headings[0]


def find_visible_dates(html: str, today: str) -> list[dict]:
    """`today` is the day the page was fetched, as YYYY-MM-DD."""
    tree = load_html(html)
    if tree is None:
        return []

    # drop what a reader never sees (a node without a parent can't be dropped)
    hidden = list(tree.iter("script", "style", "noscript", "template")) + list(tree.iter(etree.Comment))
    for node in hidden:
        if node.getparent() is not None:
            node.drop_tree()

    headline = _find_headline(tree)
    order = {elem: index for index, elem in enumerate(tree.iter()) if isinstance(elem.tag, str)}
    found, seen = [], set()

    def record(elem, kind: str, date: str | None, before: str, matched: str, after: str):
        if not date or (elem, date) in seen:
            return
        seen.add((elem, date))
        found.append({
            "date": date,
            "kind": kind,  # "time tag" or "text"
            "region": _region(elem, headline),
            "label": _label(before, after),
            "elements_from_headline": order[elem] - order[headline] if headline is not None else None,
            "same_as_fetch_day": date == today,
            "raw": _squash(before[-30:] + matched + after[:30])[:90],
        })

    for elem in order:
        # <time> tags: the datetime attribute, else the text
        if elem.tag == "time":
            own = elem.text_content()
            value = elem.get("datetime") or own
            written = DATE_IN_TEXT.search(own)  # "Posted Fri, Aug 25, 2023": the label can sit inside the tag
            before = _text_before(elem) + (own[:written.start()] if written else "")
            record(elem, "time tag", _parse(value), before, own[written.start():] if written else (own or value), "")

        # dates in the text, reported from the smallest element that holds the whole date
        text = elem.text_content()
        if not HAS_YEAR.search(text):
            continue
        child_spans, position = [], len(elem.text or "")
        for child in elem:
            length = len(child.text_content()) if isinstance(child.tag, str) else 0
            child_spans.append((position, position + length))
            position += length + len(child.tail or "")
        for match in DATE_IN_TEXT.finditer(text):
            if any(start <= match.start() and match.end() <= end for start, end in child_spans):
                continue  # sits wholly inside a child, which reports it
            before = text[:match.start()]
            if len(before) < LABEL_WINDOW:
                before = _text_before(elem) + before
            record(elem, "text", _parse(match[0]), before, match[0], text[match.end():])

    return found
