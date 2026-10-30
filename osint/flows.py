"""How much shipping is actually getting through the world's chokepoints, day by day.

The IMF's PortWatch publishes a daily count of the vessels passing each of the straits and
canals that world trade cannot route around — Hormuz, Suez, Panama, Bab el-Mandeb, Malacca and
the rest — derived from AIS transponder data. It is open, needs no key, and is the one feed
here that measures a consequence rather than an event: a strait that is being avoided shows up
as a number falling days before anyone writes about it.

The comparison that matters is a chokepoint against its own recent self, never against another
one. Two reasons, and the second is the important one. Malacca carries several times Panama's
traffic on an ordinary day, so a league table of raw counts says little. And these are vessels
PortWatch could attribute from AIS: a ship with its transponder off is not in the count, which
is why PortWatch's Hormuz figure sits in single digits while the strait is one of the busiest
in the world. Read every number here as a change against the fortnight before, which is what
this module returns.

PortWatch is served from an ArcGIS feature service that is often slow and sometimes answers an
empty set for a query that worked a minute earlier. Every call here treats that as "no data
right now" rather than as an error.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

import requests
import streamlit as st

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; axn-pulse-news-monitor/1.0)"}
CHOKEPOINTS = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
               "Daily_Chokepoints_Data/FeatureServer/0/query")
TIMEOUT = 20

# Which country a strait or canal is reached through. PortWatch names a waterway, not a place,
# and several of these are shared or international water — this is only enough to answer "does
# this chokepoint belong on the panel when that country is selected".
NEARBY = {
    "Suez Canal": ("EG",), "Strait of Hormuz": ("IR", "OM", "AE"),
    "Bab el-Mandeb Strait": ("YE", "DJ", "ER"), "Panama Canal": ("PA",),
    "Malacca Strait": ("MY", "ID", "SG"), "Bosporus Strait": ("TR",),
    "Gibraltar Strait": ("ES", "MA"), "Taiwan Strait": ("TW", "CN"),
    "Bohai Strait": ("CN",), "Cape of Good Hope": ("ZA",),
    "Dover Strait": ("GB", "FR"), "Korea Strait": ("KR", "JP"),
    "Luzon Strait": ("PH", "TW"), "Mindoro Strait": ("PH",),
    "Balabac Strait": ("PH", "MY"), "Sunda Strait": ("ID",),
    "Lombok Strait": ("ID",), "Makassar Strait": ("ID",), "Ombai Strait": ("ID", "TL"),
    "Torres Strait": ("AU", "PG"), "Bering Strait": ("RU", "US"),
    "Magellan Strait": ("CL", "AR"), "Oresund Strait": ("DK", "SE"),
    "Tsugaru Strait": ("JP",), "Kerch Strait": ("RU", "UA"),
    "Yucatan Channel": ("MX", "CU"), "Windward Passage": ("HT", "CU"),
    "Mona Passage": ("DO", "PR"),
}


def _page(since: str) -> tuple[list[dict], str]:
    """Every daily row since `since`, walked a page at a time.

    The service caps a single answer at a thousand rows, and a quarter of a year across twenty
    waterways is twice that, so it has to be asked for in pieces. It says when it has held
    some back; when it stops saying so, we have them all.
    """
    rows, offset = [], 0
    for _ in range(3):                      # three pages hold a quarter of a year comfortably
        body = None
        for attempt in range(3):
            # The host refuses about a quarter of well-formed queries, at random — measured,
            # not guessed. One try in three pages would fail outright a fifth of the time, so
            # each page gets three attempts, which puts the whole fetch under one in twenty.
            try:
                reply = requests.get(CHOKEPOINTS, params={
                    "where": f"date >= DATE '{since}'",
                    "outFields": "date,portname,n_total,n_tanker,n_container",
                    # Newest first, so that a page we never get costs us the far end of the
                    # baseline rather than the week we are measuring. Ascending would lose the
                    # recent days silently and quietly compute the wrong thing.
                    "orderByFields": "date DESC",
                    "f": "json", "returnGeometry": "false",
                    "resultRecordCount": 1000, "resultOffset": offset},
                    headers=HEADERS, timeout=TIMEOUT)
                reply.raise_for_status()
                body = reply.json()
            except (requests.RequestException, ValueError):
                body = None
            if body is not None and "error" not in body:
                break
            body = None
            if attempt < 2:
                time.sleep(1.5)
        if body is None:
            return rows, "" if rows else "IMF PortWatch is not answering."

        page = [f["attributes"] for f in body.get("features", []) if f.get("attributes")]
        rows += page
        if not body.get("exceededTransferLimit") or not page:
            break
        offset += len(page)
    return rows, ""


@st.cache_data(ttl=10800, show_spinner=False)
def chokepoints(days: int = 97) -> tuple[list[dict], str]:
    """[{name, ships, norm, change, level, tankers, containers, as_of, countries}] and a note.

    A waterway is measured against itself: the last seven days against the ninety before them.
    Comparing straits to each other says only which is busier, which never changes; comparing
    a strait to its own habit is what tells you something happened. Ninety days rather than a
    fortnight because a fortnight still carries a holiday or a storm in it.
    """
    since = (date.today() - timedelta(days=days)).isoformat()
    rows, note = _page(since)
    if not rows:
        return [], note or "PortWatch has nothing for the last few weeks yet."

    days_seen = sorted({row["date"] for row in rows}, reverse=True)
    week, baseline = set(days_seen[:7]), set(days_seen[7:97])
    newest = days_seen[0]

    out = []
    for name in sorted({row["portname"] for row in rows}):
        mine = [row for row in rows if row["portname"] == name]
        recent = [row["n_total"] or 0 for row in mine if row["date"] in week]
        earlier = [row["n_total"] or 0 for row in mine if row["date"] in baseline]
        if not recent:
            continue
        now = sum(recent) / len(recent)
        norm = (sum(earlier) / len(earlier)) if earlier else now
        change = ((now - norm) / norm * 100) if norm else 0.0
        last = max(mine, key=lambda row: row["date"])
        out.append({
            "name": name,
            "ships": round(now, 1),
            "norm": round(norm, 1),
            "change": change,
            # Three weeks is the least that can be called a habit. With less than that the
            # figure still shows, ungraded, rather than being dressed up as a verdict.
            "level": level(change) if len(earlier) >= 21 else "",
            "tankers": last.get("n_tanker") or 0,
            "containers": last.get("n_container") or 0,
            "as_of": last["date"],
            "countries": NEARBY.get(name, ()),
        })
    return sorted(out, key=lambda row: row["change"]), f"newest day {newest}"


# How far below its own habit a waterway has to fall before it is worth a word. Only falls are
# graded: traffic above the norm is a busy week, not a disruption.
def level(change: float) -> str:
    if change <= -50:
        return "critical"
    if change <= -25:
        return "elevated"
    if change <= -10:
        return "moderate"
    return "normal"
