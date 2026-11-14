# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "htmldate==1.11.0",   # pinned: services/extract/htmldate_finder.py calls its internals
#     "curl_cffi>=0.11",
#     "selectolax>=0.3.27",
#     "fastapi>=0.115",
#     "uvicorn>=0.30",
# ]
# ///
"""Page Date: give it a URL, get the date the page is from and how that was found.

Run:   infisical run --env=dev -- uv run app.py     (loads the Jev key)
       uv run app.py                                (no Jev, htmldate's own pick)
Open:  http://127.0.0.1:8765
API:   http://127.0.0.1:8765/api/date?url=https://example.com/some-post
"""

from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from services.decide.candidates import build_candidates
from services.decide.typesafe_jev import pick_dates
from services.extract.htmldate_finder import run_all_checks
from services.extract.selectolax_signals import collect_signals, page_context
from services.fetch.curl_cffi_client import FetchError, fetch_page

HOST, PORT = "127.0.0.1", 8765
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Page Date")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/date")
def page_date(url: str):
    url = url.strip()
    if not url:
        return JSONResponse({"error": "Paste a URL first."}, status_code=400)
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # 1. fetch
    try:
        page = fetch_page(url)
    except FetchError as err:
        return JSONResponse({"error": str(err)}, status_code=502)

    html = page.pop("html")
    final_url = page["final_url"]

    # 2. run all 12 htmldate checks, twice: once looking for the publish date, once for the last update
    checks = {
        "published": run_all_checks(html, final_url, original=True),
        "updated": run_all_checks(html, final_url, original=False),
    }

    # 3. every date the page states outright (headers, URL, meta tags, JSON-LD, time tags)
    signals = collect_signals(html, final_url, page["last_modified"])

    # 4. one candidate per distinct date, then Jev picks the right one for each question
    candidates = build_candidates(checks["published"], checks["updated"], signals)
    jev = pick_dates(final_url, page_context(html), candidates)

    # 5. final answer per view: Jev's pick if it ran, otherwise htmldate's first hit
    result = {}
    for view in ("published", "updated"):
        fallback = checks[view]["htmldate_date"]
        if jev["used"]:
            pick = {"picked_by": "jev", **jev[view]}
        else:
            match = next((c for c in candidates if c["date"] == fallback), None)
            pick = {
                "picked_by": "htmldate", "date": fallback,
                "candidate_id": match["id"] if match else None,
                "confidence": None, "probabilities": {},
            }
        result[view] = {**pick, **checks[view]}

    return {
        "url": url,
        "fetch": page,
        "candidates": candidates,
        "jev": {key: value for key, value in jev.items() if key not in ("published", "updated")},
        "published": result["published"],
        "updated": result["updated"],
    }


if __name__ == "__main__":
    print(f"Page Date running at http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
