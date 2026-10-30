"""The air, and the atmosphere behind it.

Two different timescales sit in this one module on purpose. Air quality is what a place is
like to breathe this hour, from Open-Meteo's CAMS-derived forecast — the same provider the
weather strip already uses. The carbon dioxide figure is NOAA's global daily mean, which moves
by a couple of parts per million a year and is here as the slow background against which all
the fast numbers on this dashboard are happening.

Both are free and keyless. Neither raises: a panel without them is better than a page without
any of them.
"""
from __future__ import annotations

import requests
import streamlit as st

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; axn-pulse-news-monitor/1.0)"}
TIMEOUT = 15

# The US AQI bands, in the words the people who publish them use.
BANDS = [(50, "Good"), (100, "Moderate"), (150, "Unhealthy for some"),
         (200, "Unhealthy"), (300, "Very unhealthy"), (10_000, "Hazardous")]

# Enough of the world's big, badly-breathing cities to make a worldwide panel worth reading.
WATCHED = [("Delhi", 28.61, 77.21, "IN"), ("Dhaka", 23.81, 90.41, "BD"),
           ("Lahore", 31.55, 74.34, "PK"), ("Beijing", 39.90, 116.41, "CN"),
           ("Jakarta", -6.21, 106.85, "ID"), ("Cairo", 30.04, 31.24, "EG"),
           ("Lagos", 6.52, 3.38, "NG"), ("São Paulo", -23.55, -46.63, "BR"),
           ("Mexico City", 19.43, -99.13, "MX"), ("Los Angeles", 34.05, -118.24, "US"),
           ("London", 51.51, -0.13, "GB"), ("Tokyo", 35.68, 139.69, "JP")]


def band(aqi: float) -> str:
    return next(words for limit, words in BANDS if aqi <= limit)


@st.cache_data(ttl=3600, show_spinner=False)
def air(lat: float, lng: float) -> dict | None:
    """{aqi, band, pm2_5, pm10, no2, ozone} for one place, or None if the feed is out."""
    try:
        reply = requests.get("https://air-quality-api.open-meteo.com/v1/air-quality",
                             params={"latitude": lat, "longitude": lng,
                                     "current": "us_aqi,pm2_5,pm10,nitrogen_dioxide,ozone"},
                             headers=HEADERS, timeout=TIMEOUT)
        reply.raise_for_status()
        now = reply.json().get("current") or {}
    except (requests.RequestException, ValueError):
        return None
    if now.get("us_aqi") is None:
        return None
    return {"aqi": float(now["us_aqi"]), "band": band(float(now["us_aqi"])),
            "pm2_5": now.get("pm2_5"), "pm10": now.get("pm10"),
            "no2": now.get("nitrogen_dioxide"), "ozone": now.get("ozone")}


@st.cache_data(ttl=3600, show_spinner=False)
def watched() -> list[dict]:
    """Air quality in the cities worth watching, worst first.

    One request for all twelve, not twelve requests. Open-Meteo takes a list of coordinates and
    answers in the order it was given them, which turns forty seconds of waiting into one — and
    forty seconds is long enough that every panel below this one stands blank meanwhile.

    It answers by position, not by coordinate: the latitudes it echoes back are rounded to its
    own grid, so they cannot be matched against the ones we sent. Hence the length check — if
    the two ever stop lining up, the right thing is no reading rather than a wrong city.
    """
    try:
        reply = requests.get("https://air-quality-api.open-meteo.com/v1/air-quality",
                             params={"latitude": ",".join(f"{lat}" for _, lat, _, _ in WATCHED),
                                     "longitude": ",".join(f"{lng}" for _, _, lng, _ in WATCHED),
                                     "current": "us_aqi,pm2_5,pm10,nitrogen_dioxide,ozone"},
                             headers=HEADERS, timeout=TIMEOUT)
        reply.raise_for_status()
        places = reply.json()
    except (requests.RequestException, ValueError):
        return []
    if not isinstance(places, list) or len(places) != len(WATCHED):
        return []

    out = []
    for (name, _, _, cc), place in zip(WATCHED, places):
        now = (place or {}).get("current") or {}
        if now.get("us_aqi") is None:
            continue
        aqi = float(now["us_aqi"])
        out.append({"name": name, "cc": cc, "aqi": aqi, "band": band(aqi),
                    "pm2_5": now.get("pm2_5"), "pm10": now.get("pm10"),
                    "no2": now.get("nitrogen_dioxide"), "ozone": now.get("ozone")})
    return sorted(out, key=lambda row: -row["aqi"])


@st.cache_data(ttl=86400, show_spinner=False)
def carbon() -> dict | None:
    """NOAA's global mean CO2: {ppm, a_year_ago, as_of}.

    The file is a comment header and then year, month, day, cycle, trend. "Trend" is the
    seasonally adjusted series, which is the one to quote — the raw cycle rises and falls
    every year with the northern hemisphere's growing season and would read as noise.
    """
    try:
        reply = requests.get("https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_trend_gl.txt",
                             headers=HEADERS, timeout=TIMEOUT)
        reply.raise_for_status()
    except requests.RequestException:
        return None
    rows = []
    for line in reply.text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            rows.append((f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}", float(parts[4])))
        except ValueError:
            continue
    if not rows:
        return None
    as_of, ppm = rows[-1]
    year_ago = rows[-366][1] if len(rows) > 366 else rows[0][1]
    return {"ppm": ppm, "a_year_ago": year_ago, "as_of": as_of,
            "history": [v for _, v in rows[-365:]]}
