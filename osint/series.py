"""Economic and market series, read straight from the people who publish them.

FRED (the St. Louis Fed) re-publishes most of what a market panel needs — index levels, the
VIX, Treasury spreads, oil, the dollar — and its graph endpoint hands back plain CSV with no
key and no quota. That is the whole reason it is the spine of the money panels: every
no-key alternative we tried is now behind a browser challenge, and anything else wants a
registration we would then have to keep alive.

Two things this module insists on:

  * a series that does not answer returns an empty result, never an exception, so one slow
    morning at the Fed costs a panel rather than the page;
  * the last value is the last *real* value. FRED writes "." for a day a market was shut, and
    a chart that reads that as zero draws a cliff that never happened.
"""
from __future__ import annotations

import json
import time
from functools import lru_cache
from pathlib import Path

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; axn-pulse-news-monitor/1.0)"}
CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
TIMEOUT = 15


# When FRED is unreachable it is unreachable for everything, and a page that asks it for eight
# series pays the timeout eight times over — long enough that every panel below stands blank
# while it waits. One unreachable host shuts the door for a minute; the next success opens it.
# A 404 on one series id is not that, and leaves the door alone.
_SHUT_UNTIL = 0.0
_SHUT_FOR = 60

# A reading that arrived stands for an hour; a reading that did not stands for two minutes.
# One ttl for both is what turned a single slow moment at FRED into an hour of blank panels:
# the empty answer was kept exactly as long as a real one, so the page went on insisting the
# host was unreachable long after it had come back.
_CACHE: dict[tuple[str, int], tuple[float, list]] = {}
_GOOD_FOR = 3600
_BAD_FOR = 120


@lru_cache(maxsize=1)
def _shipped() -> dict[str, list[tuple[str, float]]]:
    """The series tools/build_series.py fetched and committed.

    FRED answers a laptop and a GitHub runner and does not answer Streamlit Cloud, where every
    request times out and every money panel stands empty. So the series ride in the repo, like
    the map layers do, and the deployed app reads a file instead of asking. A day-old daily
    series is worth more than a live one that never arrives.
    """
    path = Path(__file__).resolve().parent / "data" / "series.json"
    try:
        body = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return {name: [(day, value) for day, value in rows]
            for name, rows in (body.get("series") or {}).items()}


def built_on() -> str:
    """The day the shipped series were fetched, or "" if none are shipped."""
    path = Path(__file__).resolve().parent / "data" / "series.json"
    try:
        return json.loads(path.read_text()).get("built", "")
    except (OSError, ValueError):
        return ""


def fred(series_id: str, points: int = 90) -> list[tuple[str, float]]:
    """The last `points` observations of a FRED series, oldest first, gaps dropped."""
    rows = _shipped().get(series_id)
    if rows:
        return rows[-points:]

    global _SHUT_UNTIL
    now = time.monotonic()
    held = _CACHE.get((series_id, points))
    if held and now < held[0]:
        return held[1]

    # Nothing is remembered while the door is shut: the moment it opens the next caller should
    # go and ask, rather than be handed the silence that shut it.
    if now < _SHUT_UNTIL:
        return []
    try:
        reply = requests.get(CSV, params={"id": series_id}, headers=HEADERS, timeout=TIMEOUT)
        reply.raise_for_status()
    except (requests.Timeout, requests.ConnectionError):
        # The host itself is unreachable, so the next seven series would be too
        _SHUT_UNTIL = time.monotonic() + _SHUT_FOR
        return []
    except requests.RequestException:
        # A refusal about this one series says nothing about the rest
        _CACHE[(series_id, points)] = (now + _BAD_FOR, [])
        return []
    _SHUT_UNTIL = 0.0

    rows = []
    for line in reply.text.strip().splitlines()[1:]:
        day, _, value = line.partition(",")
        value = value.strip()
        if value in ("", "."):          # a holiday, or a figure not published yet
            continue
        try:
            rows.append((day, float(value)))
        except ValueError:
            continue
    rows = rows[-points:]
    _CACHE[(series_id, points)] = (now + (_GOOD_FOR if rows else _BAD_FOR), rows)
    return rows


def reading(series_id: str, points: int = 90) -> dict | None:
    """{value, change, percent, as_of, history} — what a tile needs, or None if the feed is out.

    `change` is against the previous observation, which for a daily series is the last session
    and for a monthly one is last month. Both are the comparison a reader expects of that
    series, so one rule covers them.
    """
    rows = fred(series_id, points)
    if not rows:
        return None
    as_of, value = rows[-1]
    previous = rows[-2][1] if len(rows) > 1 else value
    change = value - previous
    return {"value": value, "change": change, "as_of": as_of,
            "percent": (change / previous * 100) if previous else 0.0,
            "history": [v for _, v in rows]}
