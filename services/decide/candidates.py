"""Step 4a: merge everything the earlier steps found into one list of candidate dates.

One candidate per distinct date. Each carries every place that date was found:
  visible   dates written in the page, with region, label and distance from the headline
  signal    what the site states in tags (meta, JSON-LD, time tags, URL, HTTP header)
  check     the htmldate checks that landed on it (publish pass, update pass, or both)

Sources are listed in that order because Jev only reads the first few per
candidate, and where a date sits on the page is what tells a byline from a
masthead clock.
"""

MAX_CANDIDATES = 12
IDS = "ABCDEFGHIJKL"

MAX_VISIBLE_PER_DATE = 3
PUBLISH_LABELS = {"Published", "Posted", "By-line"}
NEAR_HEADLINE = 60  # elements, either side
REGION_RANK = {"article_meta": 0, "article": 1, "unknown": 2, "chrome": 3}


def _distance(source: dict) -> int:
    away = source["elements_from_headline"]
    return abs(away) if away is not None else 10_000


def _is_byline(source: dict) -> bool:
    """A publish-labelled date outside site chrome, close to the headline."""
    return (
        source["kind"] == "visible"
        and source["label"] in PUBLISH_LABELS
        and source["region"] != "chrome"
        and _distance(source) <= NEAR_HEADLINE
    )


def _where(found: dict) -> str:
    parts = [f"visible {found['kind']}", found["region"]]
    if found["label"]:
        parts.append(f"labelled '{found['label']}'")
    away = found["elements_from_headline"]
    if away == 0:
        parts.append("in the headline")
    elif away is not None:
        parts.append(f"{abs(away)} element{'s' if abs(away) != 1 else ''} {'after' if away > 0 else 'before'} headline")
    if found["same_as_fetch_day"]:
        parts.append("equals the fetch day")
    return ", ".join(parts)


def _visible_sources(found_for_date: list[dict]) -> list[dict]:
    """A date can be written many times. Keep the closest one per (region, label), best first."""
    closest: dict[tuple, dict] = {}
    for found in found_for_date:
        source = {
            "kind": "visible", "where": _where(found), "detail": found["raw"],
            "region": found["region"], "label": found["label"],
            "elements_from_headline": found["elements_from_headline"],
            "same_as_fetch_day": found["same_as_fetch_day"],
        }
        key = (found["region"], found["label"])
        if key not in closest or _distance(source) < _distance(closest[key]):
            closest[key] = source
    ranked = sorted(closest.values(), key=lambda s: (
        not _is_byline(s), s["label"] is None, REGION_RANK[s["region"]], _distance(s),
    ))
    return ranked[:MAX_VISIBLE_PER_DATE]


def build_candidates(published: dict, updated: dict, signals: list[dict], visible: list[dict]) -> list[dict]:
    seen_in_page: dict[str, list[dict]] = {}
    stated: dict[str, list[dict]] = {}
    checked: dict[str, list[dict]] = {}

    # dates written in the visible page
    for found in visible:
        seen_in_page.setdefault(found["date"], []).append(found)

    # raw signals the site states in tags
    for signal in signals:
        stated.setdefault(signal["date"], []).append({
            "kind": "signal", "where": f"{signal['source']}: {signal['name']}",
            "detail": signal["raw"],
        })

    # htmldate checks: the same check can land on different dates in the two passes
    for pub_step, upd_step in zip(published["steps"], updated["steps"]):
        hits = {}
        if pub_step["date"]:
            hits[pub_step["date"]] = ("publish pass", pub_step)
        if upd_step["date"]:
            same = upd_step["date"] in hits
            hits[upd_step["date"]] = ("both passes" if same else "update pass", upd_step)
        for date, (which, step) in hits.items():
            checked.setdefault(date, []).append({
                "kind": "check", "where": f"htmldate check: {step['label']}",
                "pass": which, "strength": step["strength"], "detail": step["evidence"],
            })

    by_date = {
        date: _visible_sources(seen_in_page.get(date, [])) + stated.get(date, []) + checked.get(date, [])
        for date in {*seen_in_page, *stated, *checked}
    }

    # if a page is full of dates: a byline by the headline always stays,
    # then the dates found in the most places
    bylines = [date for date, sources in by_date.items() if any(_is_byline(s) for s in sources)]
    others = sorted((date for date in by_date if date not in bylines),
                    key=lambda date: len(by_date[date]), reverse=True)
    keep = (bylines + others)[:MAX_CANDIDATES]

    return [
        {"id": IDS[index], "date": date, "sources": by_date[date]}
        for index, date in enumerate(sorted(keep))
    ]
