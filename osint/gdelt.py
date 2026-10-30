"""How the world's press is sounding, from GDELT's running index of it.

GDELT reads news in a hundred languages and publishes, among much else, two series we can use
directly: the average *tone* of coverage and its *volume*. Tone is their own scoring, roughly
negative to positive; volume is the share of all monitored coverage the query accounts for.
Together they say whether a place is being written about more than usual, and in what mood —
which is a different question from anything our own collection can answer, since ours is a few
dozen feeds and theirs is the whole open web.

The one hard rule here is politeness. GDELT asks for no more than a request every five
seconds and enforces it more strictly than that; a burst earns a plain-text scolding instead
of JSON. So this module makes one request per query, holds the answer for half an hour, and
when it is turned away it says so rather than retrying.
"""
from __future__ import annotations

from datetime import datetime

import requests
from .cache import remember

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; axn-pulse-news-monitor/1.0)"}
API = "https://api.gdeltproject.org/api/v2/doc/doc"

# What "the world" means when no country is picked: the subjects this dashboard is for.
WORLD_QUERY = '("war" OR "sanctions" OR "ceasefire" OR "airstrike")'


@remember(1800)
def timeline(country: str = "", kind: str = "tone", days: int = 7) -> tuple[list[dict], str]:
    """[{when, value}] and a note. kind is "tone" or "volume"."""
    query = f"sourcecountry:{country.upper()}" if country else WORLD_QUERY
    mode = "timelinetone" if kind == "tone" else "timelinevol"
    try:
        reply = requests.get(API, params={"query": query, "mode": mode, "format": "json",
                                          "timespan": f"{days}d"},
                             headers=HEADERS, timeout=15)
    except requests.RequestException as problem:
        return [], f"GDELT unreachable ({type(problem).__name__})."

    if "limit requests" in reply.text[:200]:
        return [], "GDELT is asking us to slow down — this panel will fill in shortly."
    try:
        body = reply.json()
    except ValueError:
        return [], "GDELT returned something that was not data."

    series = (body.get("timeline") or [{}])[0].get("data", [])
    rows = []
    for point in series:
        try:
            when = datetime.strptime(point["date"], "%Y%m%dT%H%M%SZ")
        except (KeyError, ValueError):
            continue
        rows.append({"when": when, "value": float(point.get("value", 0))})
    if not rows:
        return [], "GDELT has nothing for this query in the last week."
    return rows, ""
