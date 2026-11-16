"""Step 4b: Jev (TypeSafe AI's decision model) picks the right date from the candidates.

One request, three questions over the same state:
  dated      noul    is this a dated piece of content at all?
  published  choice  which candidate is the publish date (or none)
  updated    choice  which candidate is the last-updated date (or none)

Jev returns a probability for every option, so the UI can show how sure it is
instead of a bare answer.

Key: TYPESAFE_API_KEY, or typesafe_key (the name it has in Infisical, so
`infisical run --env=dev -- uv run app.py` just works).
"""

import os

from curl_cffi import requests

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
TIMEOUT_SECONDS = 30
USD_PER_MILLION_INPUT_TOKENS = 0.042

RULES = (
    "Choose only from the candidates. Trust dates the site states on purpose for this page "
    "(JSON-LD datePublished or dateModified, article:published_time or article:modified_time meta tags, "
    "a time tag by the headline, a date in the URL) over dates that merely appear in body text, "
    "comments, related links, image paths, footers or copyright lines. An HTTP Last-Modified header "
    "often just reflects a server or cache refresh, not the content. "
    "A date written in the visible page and labelled Published, Posted or 'By <name> on', sitting next to "
    "the headline, is strong evidence of the publish date. A date in site chrome (masthead, navigation, "
    "sidebar, footer, widgets) that equals today's date is the masthead clock or a widget, not the publish "
    "date, unless that same date is also labelled Published or Posted next to the headline."
)


def _api_key() -> str | None:
    return os.environ.get("TYPESAFE_API_KEY") or os.environ.get("typesafe_key")


def _describe(candidate: dict) -> str:
    places = "; ".join(source["where"] for source in candidate["sources"][:6])
    return f"{candidate['date']}, found in: {places}"


def pick_dates(url: str, context: dict, candidates: list[dict], today: str) -> dict:
    """Returns {"used": True, published, updated, dated, ...} or {"used": False, "reason": ...}."""
    key = _api_key()
    if not key:
        return {"used": False, "reason": "No TypeSafe key in the environment (TYPESAFE_API_KEY or typesafe_key)."}
    if not candidates:
        return {"used": False, "reason": "No candidate dates were found, so there was nothing to pick from."}

    state = {
        "task": "Work out when this web page was published and when it was last updated.",
        "today": today,  # the day the page was fetched
        "page": {"url": url, **context},
        "candidates": [
            {
                "id": candidate["id"],
                "date": candidate["date"],
                "found_in": [
                    {"where": source["where"], "matched": (source.get("detail") or "")[:200]}
                    for source in candidate["sources"][:6]
                ],
            }
            for candidate in candidates
        ],
    }
    options = {candidate["id"]: _describe(candidate) for candidate in candidates}

    questions = {
        "dated": {
            "type": "noul",
            "instructions": "This page is a dated piece of content, and its publish date is among the candidates.",
            "criteria": {
                "yes": "An article, post, news item, release, listing or similar with a real publish date that one candidate matches.",
                "no": "An evergreen page (homepage, product or landing page, directory) or a page whose candidates are not its publish date.",
            },
        },
        "published": {
            "type": "choice",
            "instructions": "Which candidate is the date the main content of this page was first published? " + RULES,
            "criteria": {
                **options,
                "none": "No candidate is a believable publish date for this page, for example only a copyright year, "
                        "a site-wide timestamp, or a date that is about something else.",
            },
        },
        "updated": {
            "type": "choice",
            "instructions": "Which candidate is the date the main content of this page was most recently updated? "
                            "If nothing shows it changed after it was published, choose the publish date. " + RULES,
            "criteria": {
                **options,
                "none": "No candidate is a believable last-updated date for this page.",
            },
        },
    }

    try:
        response = requests.post(
            API_URL,
            json={"model": MODEL, "state": state, "questions": questions},
            headers={"Authorization": f"Bearer {key}"},
            timeout=TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            return {"used": False, "reason": f"Jev answered with HTTP {response.status_code}: {response.text[:200]}"}
        body = response.json()
        answers = body["answers"]
    except (requests.RequestsError, ValueError, KeyError) as err:
        return {"used": False, "reason": f"The Jev request failed: {err}"}

    dates = {candidate["id"]: candidate["date"] for candidate in candidates}

    def choice(name: str) -> dict:
        answer = answers[name]
        picked = answer["choice"]
        probabilities = answer.get("probabilities", {})
        return {
            "candidate_id": None if picked == "none" else picked,
            "date": dates.get(picked),
            "confidence": probabilities.get(picked, answer.get("confidence")),
            "probabilities": probabilities,
        }

    tokens = body.get("usage", {}).get("input_tokens", 0)
    return {
        "used": True,
        "model": body.get("model", MODEL),
        "dated": answers["dated"]["noul"],
        "published": choice("published"),
        "updated": choice("updated"),
        "input_tokens": tokens,
        "cost_usd": round(tokens * USD_PER_MILLION_INPUT_TOKENS / 1_000_000, 6),
    }
