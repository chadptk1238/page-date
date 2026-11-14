# Page Date

Give it a URL. It collects every date on the page, then Jev picks the one the page was published on (and the one it was last updated on), with a probability for each candidate.

## Run

```
infisical run --env=dev -- uv run app.py
```

Then open http://127.0.0.1:8765. Dependencies are declared at the top of `app.py`, so uv installs them on first run. No venv to manage.

The Jev key is read from `TYPESAFE_API_KEY` or `typesafe_key` (its name in Infisical). Without a key the tool still works: it shows htmldate's own first match and says Jev did not run.

API, if you want it from a script or an agent:

```
curl "http://127.0.0.1:8765/api/date?url=https://example.com/some-post"
```

Blocked site (403/429)? Run it behind a proxy by setting `PROXY_URL="http://user:pass@host:port"`.

## Layout

```
app.py                                  routes, wires the steps together
services/fetch/curl_cffi_client.py      1. fetch the page as Chrome (curl_cffi)
services/extract/htmldate_finder.py     2. run all 12 htmldate checks, publish pass and update pass
services/extract/selectolax_signals.py  3. every date the page states outright + page context (selectolax)
services/decide/candidates.py           4a. merge it all into one candidate per distinct date
services/decide/typesafe_jev.py         4b. Jev picks the publish date and the last-updated date
static/index.html                       the UI, plain HTML/CSS/JS
```

## How the pick works

`htmldate.find_date()` stops at the first check that finds a date, which is how a
copyright year in a footer becomes "January 1". Here all 12 checks run to the end,
in both modes, and their dates are merged with the page's raw signals (HTTP header,
URL, meta tags, JSON-LD, time tags) into one list of candidates.

Jev gets one request with the page context and the candidates, and answers three
questions: is this a dated piece of content, which candidate is the publish date,
which is the last-updated date. Each choice has a "none" option, so a homepage
with only a copyright year comes back as "no publish date" instead of a guess.
A lookup is about 1,000 to 2,500 input tokens, roughly $0.0001.

htmldate's own answer is still shown next to Jev's, so you can see when they differ.

Because `htmldate_finder.py` calls htmldate internals, htmldate is pinned to 1.11.0.
