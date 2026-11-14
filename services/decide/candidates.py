"""Step 4a: merge everything the earlier steps found into one list of candidate dates.

One candidate per distinct date. Each carries every place that date was found:
the htmldate checks that landed on it (from the publish pass, the update pass,
or both) and the raw signals selectolax saw (meta tags, JSON-LD, time tags...).
"""

MAX_CANDIDATES = 12
IDS = "ABCDEFGHIJKL"


def build_candidates(published: dict, updated: dict, signals: list[dict]) -> list[dict]:
    by_date: dict[str, list[dict]] = {}

    def add(date: str, source: dict):
        by_date.setdefault(date, []).append(source)

    # htmldate checks: the same check can land on different dates in the two passes
    for pub_step, upd_step in zip(published["steps"], updated["steps"]):
        hits = {}
        if pub_step["date"]:
            hits[pub_step["date"]] = ("publish pass", pub_step)
        if upd_step["date"]:
            same = upd_step["date"] in hits
            hits[upd_step["date"]] = ("both passes" if same else "update pass", upd_step)
        for date, (which, step) in hits.items():
            add(date, {
                "kind": "check", "where": f"htmldate check: {step['label']}",
                "pass": which, "strength": step["strength"], "detail": step["evidence"],
            })

    # raw signals on the page
    for signal in signals:
        add(signal["date"], {
            "kind": "signal", "where": f"{signal['source']}: {signal['name']}",
            "detail": signal["raw"],
        })

    # if a page is full of dates, keep the ones found in the most places
    dates = sorted(by_date, key=lambda date: len(by_date[date]), reverse=True)[:MAX_CANDIDATES]
    return [
        {"id": IDS[index], "date": date, "sources": by_date[date]}
        for index, date in enumerate(sorted(dates))
    ]
