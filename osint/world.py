"""What a country is, in numbers — the World Bank's indicators and UNHCR's displacement counts.

News tells you what happened this week. These say what the place is: how many people live
there, what they earn, how many have left. Both publishers are open, keyless and slow-moving,
so everything here is cached for a day.

The indicators are deliberately annual and plainly dated. A dashboard that puts "GDP" beside a
live oil price invites the reader to think both were measured this morning; the panel prints
the year next to every figure instead.
"""
from __future__ import annotations

import requests
import streamlit as st

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; axn-pulse-news-monitor/1.0)"}
WB = "https://api.worldbank.org/v2"
UNHCR = "https://api.unhcr.org/population/v1/population/"
TIMEOUT = 15
DAY = 86400

# (indicator, label, how to write it). The set is chosen to describe a country rather than to
# rank it: size, income, prices, work, connection, and what it spends on its army.
INDICATORS = [
    ("SP.POP.TOTL", "Population", "count"),
    ("NY.GDP.MKTP.CD", "GDP", "money"),
    ("NY.GDP.PCAP.CD", "GDP per person", "money"),
    ("NY.GDP.MKTP.KD.ZG", "Growth", "percent"),
    ("FP.CPI.TOTL.ZG", "Inflation", "percent"),
    ("SL.UEM.TOTL.ZS", "Unemployment", "percent"),
    ("MS.MIL.XPND.GD.ZS", "Military spending", "percent of GDP"),
    ("IT.NET.USER.ZS", "Online", "percent of people"),
]


@st.cache_data(ttl=DAY * 7, show_spinner=False)
def _codes() -> dict[str, str]:
    """ISO-2 → ISO-3, from the World Bank's own country list rather than a table kept here."""
    try:
        reply = requests.get(f"{WB}/country", params={"format": "json", "per_page": 400},
                             headers=HEADERS, timeout=TIMEOUT)
        reply.raise_for_status()
        rows = reply.json()[1]
    except (requests.RequestException, ValueError, IndexError, KeyError):
        return {}
    return {row["iso2Code"]: row["id"] for row in rows if row.get("iso2Code")}


def iso3(cc: str) -> str:
    return _codes().get((cc or "").upper(), "")


@st.cache_data(ttl=DAY, show_spinner=False)
def profile(cc: str) -> list[dict]:
    """The latest reading of each indicator for one country: [{name, value, unit, year}]."""
    if not cc:
        return []
    wanted = ";".join(code for code, _, _ in INDICATORS)
    try:
        reply = requests.get(f"{WB}/country/{cc}/indicator/{wanted}",
                             params={"format": "json", "source": 2, "mrnev": 1, "per_page": 60},
                             headers=HEADERS, timeout=TIMEOUT)
        reply.raise_for_status()
        rows = reply.json()[1] or []
    except (requests.RequestException, ValueError, IndexError, KeyError):
        return []

    found = {row["indicator"]["id"]: row for row in rows if row.get("value") is not None}
    out = []
    for code, label, unit in INDICATORS:
        row = found.get(code)
        if row:
            out.append({"name": label, "value": float(row["value"]), "unit": unit,
                        "year": str(row.get("date", ""))})
    return out


@st.cache_data(ttl=DAY, show_spinner=False)
def displacement(cc: str, year: int = 0) -> dict | None:
    """How many people have left this country, and how many are displaced inside it.

    UNHCR publishes a year at a time and the newest one lands in June, so an empty answer for
    this year is normal rather than a fault — we step back a year and ask again.
    """
    code = iso3(cc)
    if not code:
        return None
    from datetime import date
    for step in range(3):
        ask = (year or date.today().year - 1) - step
        try:
            reply = requests.get(UNHCR, params={"limit": 20, "yearFrom": ask, "yearTo": ask,
                                                "coo": code}, headers=HEADERS, timeout=TIMEOUT)
            reply.raise_for_status()
            items = reply.json().get("items", [])
        except (requests.RequestException, ValueError):
            return None
        if not items:
            continue
        total = {"year": ask}
        for field in ("refugees", "asylum_seekers", "idps", "returned_refugees", "stateless"):
            total[field] = sum(int(row.get(field) or 0) for row in items)
        if any(total[f] for f in ("refugees", "asylum_seekers", "idps")):
            return total
    return None


@st.cache_data(ttl=DAY, show_spinner=False)
def biggest_origins(limit: int = 8) -> tuple[list[dict], int]:
    """The countries most people have fled, and the worldwide refugee total behind them."""
    from datetime import date
    for step in range(3):
        year = date.today().year - 1 - step
        try:
            reply = requests.get(UNHCR, params={"limit": 400, "yearFrom": year, "yearTo": year,
                                                "coo_all": "true"},
                                 headers=HEADERS, timeout=TIMEOUT)
            reply.raise_for_status()
            items = reply.json().get("items", [])
        except (requests.RequestException, ValueError):
            return [], 0
        rows = [{"name": row.get("coo_name", ""), "iso3": row.get("coo_iso", ""),
                 "refugees": int(row.get("refugees") or 0),
                 "idps": int(row.get("idps") or 0), "year": year}
                for row in items if row.get("coo_iso")]
        rows = [r for r in rows if r["refugees"] or r["idps"]]
        if rows:
            total = sum(r["refugees"] for r in rows)
            return sorted(rows, key=lambda r: -(r["refugees"] + r["idps"]))[:limit], total
    return [], 0
