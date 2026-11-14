"""Step 1: fetch the page.

curl_cffi sends the request with a real Chrome TLS/HTTP2 fingerprint, which gets
past most "are you a browser" checks that block plain requests/httpx.

Set PROXY_URL (e.g. a residential proxy) if a site still blocks you.
"""

import os
import time

from curl_cffi import requests

IMPERSONATE = "chrome"
TIMEOUT_SECONDS = 20


class FetchError(Exception):
    """Raised with a message that is safe to show in the UI."""


def fetch_page(url: str) -> dict:
    proxy = os.environ.get("PROXY_URL")
    started = time.perf_counter()

    try:
        response = requests.get(
            url,
            impersonate=IMPERSONATE,
            timeout=TIMEOUT_SECONDS,
            allow_redirects=True,
            proxies={"http": proxy, "https": proxy} if proxy else None,
        )
    except requests.RequestsError as err:
        raise FetchError(f"Couldn't reach that page. {err}") from err

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    content_type = response.headers.get("content-type", "")

    if response.status_code >= 400:
        hint = (
            " The site is probably blocking the request. Set PROXY_URL and try again."
            if response.status_code in (401, 403, 429)
            else ""
        )
        raise FetchError(f"The site answered with HTTP {response.status_code}.{hint}")

    if content_type and "html" not in content_type.lower():
        raise FetchError(
            f"That URL is not an HTML page (it came back as {content_type.split(';')[0]})."
        )

    return {
        "html": response.text,
        "final_url": str(response.url),
        "status": response.status_code,
        "elapsed_ms": elapsed_ms,
        "bytes": len(response.content),
        "content_type": content_type,
        "last_modified": response.headers.get("last-modified"),
        "impersonate": IMPERSONATE,
        "proxied": bool(proxy),
    }
