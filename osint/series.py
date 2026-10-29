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

import requests
import streamlit as st

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; axn-pulse-news-monitor/1.0)"}
CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
TIMEOUT = 40


@st.cache_data(ttl=3600, show_spinner=False)
def fred(series_id: str, points: int = 90) -> list[tuple[str, float]]:
    """The last `points` observations of a FRED series, oldest first, gaps dropped."""
    try:
        reply = requests.get(CSV, params={"id": series_id}, headers=HEADERS, timeout=TIMEOUT)
        reply.raise_for_status()
    except requests.RequestException:
        return []

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
    return rows[-points:]


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
