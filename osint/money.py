"""What the money is doing — indices, metals, energy and the stress gauges, in one shape.

Every tile here is the same dictionary: a name, a unit, and a reading from osint.series. That
sameness is the point. The panels draw tiles without knowing whether they are looking at the
VIX or at the price of tin, so adding a row is a line in a table rather than a new renderer.

A note on cadence, because it shows on screen: index levels and the VIX are daily, metals and
the IMF commodity series are monthly, and the Fed funds rate is a monthly average. Each tile
carries its own as-of date for exactly that reason — "copper, last month" is honest, "copper"
beside a live index is not.
"""
from __future__ import annotations

import requests
import streamlit as st

from . import series

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; axn-pulse-news-monitor/1.0)"}

# (series id, what to call it, how to write the number)
INDICES = [("SP500", "S&P 500", "$"), ("DJIA", "Dow Jones", "$"), ("NASDAQCOM", "Nasdaq", "$"),
           ("DTWEXBGS", "Dollar index", "")]

ENERGY = [("DCOILWTICO", "WTI crude", "$"), ("DCOILBRENTEU", "Brent crude", "$"),
          ("DHHNGSP", "US natural gas", "$"), ("PNGASEUUSDM", "EU natural gas", "$"),
          ("GASREGW", "US pump price", "$"), ("PURANUSDM", "Uranium", "$")]

METALS = [("PCOPPUSDM", "Copper", "$"), ("PALUMUSDM", "Aluminium", "$"),
          ("PNICKUSDM", "Nickel", "$"), ("PZINCUSDM", "Zinc", "$"),
          ("PTINUSDM", "Tin", "$"), ("PIORECRUSDM", "Iron ore", "$")]

GRAINS = [("PWHEAMTUSDM", "Wheat", "$"), ("PMAIZMTUSDM", "Maize", "$"),
          ("PCOTTINDUSDM", "Cotton", "¢"), ("PSUGAISAUSDM", "Sugar", "¢")]

# The stress gauges. Each carries the direction that means trouble, because "up is bad" is not
# obvious for a spread and is the opposite of what it means for a stock index.
MACRO = [("VIXCLS", "VIX", "", "up"), ("STLFSI4", "Financial stress", "", "up"),
         ("NFCI", "Financial conditions", "", "up"), ("T10Y2Y", "10Y–2Y spread", "%", "down"),
         ("DGS10", "10-year yield", "%", "up"), ("FEDFUNDS", "Fed funds rate", "%", "up"),
         ("T5YIE", "5y inflation expected", "%", "up"), ("UNRATE", "US unemployment", "%", "up")]


def _tiles(table) -> list[dict]:
    out = []
    for row in table:
        sid, label, unit = row[0], row[1], row[2]
        read = series.reading(sid)
        if not read:
            continue                       # a series that is out simply leaves no tile
        # Falling is the bad direction for a price or an index, so that is the default. The
        # gauges that run the other way — the VIX, the stress indices — say so in their row.
        out.append({"name": label, "unit": unit, "worse": row[3] if len(row) > 3 else "down",
                    **read})
    return out


def indices() -> list[dict]:
    return _tiles(INDICES)


def energy() -> list[dict]:
    return _tiles(ENERGY)


def metals() -> list[dict]:
    return _tiles(METALS)


def grains() -> list[dict]:
    return _tiles(GRAINS)


def macro() -> list[dict]:
    return _tiles(MACRO)


def stress() -> tuple[str, str]:
    """A plain-words verdict on the macro picture, from the gauges that are actually answering.

    This counts how many of the four fast gauges sit on the wrong side of their usual range —
    it is a summary of the tiles below it, not a forecast, and it says so on the page.
    """
    marks = 0
    seen = 0
    for sid, limit, bad_above in (("VIXCLS", 22, True), ("STLFSI4", 0.5, True),
                                  ("NFCI", 0, True), ("T10Y2Y", 0, False)):
        read = series.reading(sid)
        if not read:
            continue
        seen += 1
        if (read["value"] > limit) if bad_above else (read["value"] < limit):
            marks += 1
    if not seen:
        return "Unknown", "None of the stress gauges answered."
    if marks == 0:
        return "Steady", "Volatility, credit and the yield curve are all in their usual range."
    if marks == 1:
        return "Watchful", "One gauge is outside its usual range; the rest are calm."
    if marks == 2:
        return "Strained", "Two gauges are outside their usual range at once."
    return "Stressed", f"{marks} of {seen} gauges are outside their usual range together."


# The OECD publishes a share price index for each of its members and a few besides, under one
# series name with the country code in the middle. Countries it does not cover simply have no
# market tile, which is better than showing them someone else's.
def country_index(cc: str) -> dict | None:
    if not cc or len(cc) != 2:
        return None
    read = series.reading(f"SPASTT01{cc.upper()}M661N", points=60)
    return {"name": "Share prices", "unit": "", "worse": "down", **read} if read else None


@st.cache_data(ttl=600, show_spinner=False)
def crypto() -> list[dict]:
    """Bitcoin and ether from CoinGecko — the one live, keyless price feed left standing."""
    try:
        reply = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin,ethereum", "vs_currencies": "usd",
                    "include_24hr_change": "true"},
            headers=HEADERS, timeout=12)
        reply.raise_for_status()
        body = reply.json()
    except (requests.RequestException, ValueError):
        return []
    named = {"bitcoin": "Bitcoin", "ethereum": "Ether"}
    out = []
    for key, label in named.items():
        if key in body:
            out.append({"name": label, "unit": "$", "value": body[key]["usd"],
                        "percent": body[key].get("usd_24h_change", 0.0),
                        "change": 0.0, "as_of": "live", "worse": "down", "history": []})
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def fear_and_greed() -> dict | None:
    """Alternative.me's crypto fear-and-greed index, 0–100, with the word it gives the number.

    It is a sentiment gauge rather than a price, which is why it sits beside the crypto tiles
    rather than among them: it says how the people holding the thing feel, not what it costs.
    """
    try:
        reply = requests.get("https://api.alternative.me/fng/", params={"limit": 2},
                             headers=HEADERS, timeout=12)
        reply.raise_for_status()
        rows = reply.json().get("data") or []
    except (requests.RequestException, ValueError):
        return None
    if not rows:
        return None
    now = int(rows[0]["value"])
    before = int(rows[1]["value"]) if len(rows) > 1 else now
    return {"value": now, "words": rows[0].get("value_classification", ""),
            "change": now - before}
