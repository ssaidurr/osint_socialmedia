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


@st.cache_data(ttl=10800, show_spinner=False)
def chokepoints(days: int = 28) -> tuple[list[dict], str]:
    """[{name, ships, before, change, tankers, containers, as_of}] and a note if it is out.

    `change` compares the most recent week against the week before it, so a one-quiet-Sunday
    wobble does not read as a blockade.
    """
    since = (date.today() - timedelta(days=days)).isoformat()
    try:
        reply = requests.get(CHOKEPOINTS, params={
            "where": f"date >= DATE '{since}'",
            "outFields": "date,portname,n_total,n_tanker,n_container",
            # the service refuses anything above its own maxRecordCount of 1000, and a month
            # of roughly twenty chokepoints fits inside that with room to spare
            "f": "json", "returnGeometry": "false", "resultRecordCount": 1000},
            headers=HEADERS, timeout=TIMEOUT)
        reply.raise_for_status()
        body = reply.json()
    except (requests.RequestException, ValueError):
        return [], "IMF PortWatch is not answering."
    if "error" in body:
        return [], "IMF PortWatch turned the query away."

    rows = [f["attributes"] for f in body.get("features", []) if f.get("attributes")]
    if not rows:
        return [], "PortWatch has nothing for the last few weeks yet."

    newest = max(row["date"] for row in rows)
    # The dates are plain YYYY-MM-DD strings, so string order is date order
    week = sorted({row["date"] for row in rows}, reverse=True)[:7]
    before = sorted({row["date"] for row in rows}, reverse=True)[7:14]

    out = []
    for name in sorted({row["portname"] for row in rows}):
        mine = [row for row in rows if row["portname"] == name]
        recent = [row["n_total"] or 0 for row in mine if row["date"] in week]
        earlier = [row["n_total"] or 0 for row in mine if row["date"] in before]
        if not recent:
            continue
        now = sum(recent) / len(recent)
        then = (sum(earlier) / len(earlier)) if earlier else now
        last = max(mine, key=lambda row: row["date"])
        out.append({
            "name": name,
            "ships": round(now, 1),
            "before": round(then, 1),
            "change": ((now - then) / then * 100) if then else 0.0,
            "tankers": last.get("n_tanker") or 0,
            "containers": last.get("n_container") or 0,
            "as_of": last["date"],
            "countries": NEARBY.get(name, ()),
        })
    return sorted(out, key=lambda row: -row["ships"]), f"newest day {newest}"
