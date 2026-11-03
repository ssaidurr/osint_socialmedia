"""A GET that is actually over when it says it is.

`requests`' `timeout` is not a deadline. It is the longest silence the socket will tolerate
between two bytes, so a server that dribbles a megabyte at fifty kilobytes a second satisfies
an eight-second timeout for forty seconds together — and nothing in the call ever fires.
Caltrans' camera file does exactly that: 1.8 MB at ~48 kB/s, first byte in one second, body in
thirty-eight. Measured once at 148 seconds. A single panel held the whole page open that long,
because Streamlit renders a page in one pass and every panel waits its turn.

So the body is read against the clock here, in chunks, and abandoned when the budget is spent.
A source that cannot answer inside its budget is treated as a source that did not answer, which
is what the panels already know how to show.
"""
from __future__ import annotations

import json as _json
import time

import requests

AGENT = "axn-pulse-news-monitor/1.0"
CHUNK = 64 * 1024
CAP = 8_000_000  # a feed larger than this is a feed we have misunderstood


def fetch(url: str, budget: float = 8.0, *, headers: dict | None = None,
          params: dict | None = None, cap: int = CAP) -> tuple[bytes | None, int]:
    """(body, HTTP status). The body is None unless it arrived whole, within `budget` seconds.

    `budget` covers the whole exchange — connect, first byte and the body together — which is
    the promise the caller actually needs to make to the page it is drawing. A status of 0 is a
    network error, and 408 is our own verdict: the far end was still talking when time ran out.
    """
    deadline = time.monotonic() + budget
    sent = dict(headers or {})
    sent.setdefault("User-Agent", AGENT)
    try:
        reply = requests.get(url, params=params, headers=sent, stream=True,
                             timeout=(min(4.0, budget), budget))
        if reply.status_code != 200:
            reply.close()
            return None, reply.status_code
        body = bytearray()
        # iter_content undoes any content-encoding for us, so these are the real bytes
        for chunk in reply.iter_content(CHUNK):
            body += chunk
            if time.monotonic() > deadline or len(body) > cap:
                reply.close()
                return None, 408
        return bytes(body), 200
    except Exception:
        return None, 0


def get(url: str, budget: float = 8.0, **kw) -> bytes | None:
    """`fetch` for the callers that only want the body."""
    return fetch(url, budget, **kw)[0]


def json(url: str, budget: float = 8.0, **kw):
    """`get`, parsed. None covers every way this can fail: late, too big, or not JSON."""
    body = get(url, budget, **kw)
    if not body:
        return None
    try:
        return _json.loads(body)
    except ValueError:
        return None
