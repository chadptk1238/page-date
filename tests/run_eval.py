# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "htmldate==1.11.0",
#     "curl_cffi>=0.11",
#     "selectolax>=0.3.27",
#     "fastapi>=0.115",
#     "uvicorn>=0.30",
# ]
# ///
"""Score Page Date against tests/truth.csv.

Run from the repo root:
    infisical run --env=dev -- uv run tests/run_eval.py
    infisical run --env=dev -- uv run tests/run_eval.py --refetch     (ignore the cache)

Calls the same analyze() the app uses, so the score is what the app returns.
Fetched pages are cached in tests/.cache together with the day they were
fetched, so a rerun doesn't refetch and still treats that day as "today".

Scoring, for Jev's published pick:
  truth is a date   right if the pick is that date
  truth is "none"   right if the pick is no date, or the page can't be fetched

Each run is saved, and the next run prints every page whose answer changed.
"""

import csv
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import analyze  # noqa: E402
from services.fetch.curl_cffi_client import FetchError, fetch_page  # noqa: E402

TRUTH = ROOT / "tests" / "truth.csv"
CACHE = ROOT / "tests" / ".cache"
LAST_RUN = CACHE / "last_run.json"
WORKERS = 6


def cached_fetch(url: str, refetch: bool) -> dict:
    """The page as fetch_page returns it, plus fetched_on. Fetch errors are cached too."""
    path = CACHE / "pages" / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".json")
    if path.exists() and not refetch:
        return json.loads(path.read_text())
    try:
        page = fetch_page(url)
    except FetchError as err:
        page = {"error": str(err)}
    page = {"url": url, "fetched_on": date.today().isoformat(), **page}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(page))
    return page


def run_one(row: dict, refetch: bool) -> dict:
    url, truth = row["url"], row["truth"]
    page = cached_fetch(url, refetch)
    out = {"url": url, "truth": truth, "pick": None, "note": "", "tokens": 0, "dated": None, "confidence": None}

    if "error" in page:
        out["note"] = "fetch error: " + page["error"][:60]
    else:
        result = analyze(page["html"], page["final_url"], page["last_modified"], today=page["fetched_on"])
        published, jev = result["published"], result["jev"]
        out["pick"] = published["date"]
        out["confidence"] = published["confidence"]
        out["dated"] = jev.get("dated")
        out["tokens"] = jev.get("input_tokens", 0)
        if not jev["used"]:
            out["note"] = "Jev did not run: " + jev["reason"][:60]

    out["right"] = out["pick"] is None if truth == "none" else out["pick"] == truth
    return out


def main():
    refetch = "--refetch" in sys.argv
    rows = list(csv.DictReader(TRUTH.open()))
    with ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(lambda row: run_one(row, refetch), rows))

    for r in results:
        mark = "right" if r["right"] else "WRONG"
        extra = f"  conf {r['confidence']:.2f}" if r["confidence"] is not None else ""
        extra += f"  dated {r['dated']:.2f}" if r["dated"] is not None else ""
        print(f"{mark}  truth {r['truth']:<10}  pick {str(r['pick']):<10}{extra}  {r['url'][:90]}  {r['note']}")

    right = sum(r["right"] for r in results)
    tokens = sum(r["tokens"] for r in results)
    print(f"\nScore: {right}/{len(results)}")
    print(f"Jev: {tokens} input tokens, ${tokens * 0.042 / 1_000_000:.4f}")

    # what changed since the last run
    if LAST_RUN.exists():
        before = {r["url"]: r for r in json.loads(LAST_RUN.read_text())}
        changed = [r for r in results if r["url"] in before and before[r["url"]]["pick"] != r["pick"]]
        was = sum(r["right"] for r in before.values())
        print(f"\nLast run scored {was}/{len(before)}. Answers changed on {len(changed)} page(s):")
        for r in changed:
            b = before[r["url"]]
            print(f"  {b['pick']} ({'right' if b['right'] else 'wrong'}) -> {r['pick']} ({'right' if r['right'] else 'wrong'})  {r['url'][:90]}")
    CACHE.mkdir(parents=True, exist_ok=True)
    LAST_RUN.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
