"""Open live feeds the globe draws beside our own news.

All three are public, need no key, and are cached here so a page rerun does not hit them again:

  USGS   earthquakes in the last day            earthquake.usgs.gov
  GDACS  disaster alerts (UN/EC), last 7 days   gdacs.org — "public domain"
  EONET  natural events NASA is tracking        eonet.gsfc.nasa.gov

Every fetcher returns (points, note). A feed that is down returns an empty list and the reason,
never an exception: one unreachable server must not take the page with it.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import requests
import streamlit as st

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; axn-pulse-news-monitor/1.0)"}

# WMO weather codes, as Open-Meteo reports them
SKY = {0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Rime fog",
       51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 56: "Freezing drizzle",
       57: "Freezing drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
       66: "Freezing rain", 67: "Freezing rain", 71: "Light snow", 73: "Snow", 75: "Heavy snow",
       77: "Snow grains", 80: "Rain showers", 81: "Rain showers", 82: "Violent rain showers",
       85: "Snow showers", 86: "Snow showers", 95: "Thunderstorm",
       96: "Thunderstorm with hail", 99: "Thunderstorm with hail"}
TIMEOUT = 25

# Kinds the globe knows how to colour. Keep in step with LAYER_COLOR in the component.
QUAKE, ALERT, FIRE, STORM, ICE, SUN = "quake", "alert", "fire", "storm", "ice", "sun"


def _point(lat, lng, kind, title, detail="", level="", url="", value=None, ts="") -> dict:
    return {"lat": round(float(lat), 4), "lng": round(float(lng), 4), "kind": kind,
            "title": title, "detail": detail, "level": level, "url": url,
            "value": value, "ts": ts}


@st.cache_data(ttl=600, show_spinner=False)
def earthquakes(min_magnitude: float = 2.5) -> tuple[list[dict], str]:
    """Everything USGS recorded in the last 24 hours at or above the given magnitude."""
    try:
        r = requests.get("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson",
                         headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        out = []
        for f in r.json().get("features", []):
            p, g = f.get("properties") or {}, f.get("geometry") or {}
            coords = g.get("coordinates") or []
            mag = p.get("mag")
            if len(coords) < 2 or mag is None or mag < min_magnitude:
                continue
            when = datetime.fromtimestamp((p.get("time") or 0) / 1000, tz=timezone.utc)
            out.append(_point(coords[1], coords[0], QUAKE, f"M {mag:.1f}",
                              detail=p.get("place") or "", url=p.get("url") or "",
                              value=float(mag), ts=when.strftime("%d %b %H:%M UTC"),
                              level="red" if mag >= 6 else "amber" if mag >= 4.5 else "dim"))
        return out, ""
    except Exception as e:
        return [], f"USGS earthquakes unavailable ({type(e).__name__})"


@st.cache_data(ttl=900, show_spinner=False)
def disasters(days: int = 7) -> tuple[list[dict], str]:
    """GDACS — the UN and European Commission alert system: quakes, cyclones, floods,
    volcanoes, droughts and wildfires, each with the alert level it was given."""
    names = {"EQ": "Earthquake", "TC": "Tropical cyclone", "FL": "Flood", "VO": "Volcano",
             "DR": "Drought", "WF": "Wildfire"}
    try:
        today = datetime.now(timezone.utc).date()
        r = requests.get("https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH",
                         params={"fromDate": str(today - timedelta(days=days)), "toDate": str(today),
                                 "alertlevel": "Green;Orange;Red"},
                         headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        out = []
        for f in r.json().get("features", []):
            p, g = f.get("properties") or {}, f.get("geometry") or {}
            coords = g.get("coordinates") or []
            if len(coords) < 2:
                continue
            severity = (p.get("severitydata") or {}).get("severitytext") or ""
            level = str(p.get("alertlevel") or "").lower()
            out.append(_point(coords[1], coords[0], ALERT,
                              names.get(p.get("eventtype"), p.get("eventtype") or "Alert"),
                              detail=" · ".join(x for x in (p.get("country"), severity) if x),
                              level="red" if level == "red" else "amber" if level == "orange" else "dim",
                              url=p.get("url", {}).get("report", "") if isinstance(p.get("url"), dict) else "",
                              ts=(p.get("fromdate") or "")[:16].replace("T", " ")))
        return out, ""
    except Exception as e:
        return [], f"GDACS alerts unavailable ({type(e).__name__})"


@st.cache_data(ttl=1800, show_spinner=False)
def natural_events(limit: int = 300) -> tuple[list[dict], str]:
    """NASA EONET: the wildfires, storms and ice events currently open, at their latest position."""
    kinds = {"Wildfires": FIRE, "Severe Storms": STORM, "Sea and Lake Ice": ICE,
             "Volcanoes": ALERT, "Floods": ALERT}
    try:
        r = requests.get("https://eonet.gsfc.nasa.gov/api/v3/events",
                         params={"status": "open", "limit": limit}, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        out = []
        for ev in r.json().get("events", []):
            category = (ev.get("categories") or [{}])[0].get("title", "")
            kind = kinds.get(category)
            geometry = [g for g in (ev.get("geometry") or []) if g.get("type") == "Point"]
            if not kind or not geometry:
                continue
            last = geometry[-1]                       # events move; take where it is now
            coords = last.get("coordinates") or []
            if len(coords) < 2:
                continue
            size = last.get("magnitudeValue")
            unit = last.get("magnitudeUnit") or ""
            size_text = f"{size:,.0f} {unit}".strip() if size else ""
            out.append(_point(coords[1], coords[0], kind, category.rstrip("s"),
                              detail=" · ".join(x for x in (ev.get("title"), size_text) if x),
                              url=(ev.get("sources") or [{}])[0].get("url", ""),
                              value=float(size) if size else None,
                              ts=(last.get("date") or "")[:16].replace("T", " "),
                              level="amber" if size and size > 1000 else "dim"))
        return out, ""
    except Exception as e:
        return [], f"NASA EONET unavailable ({type(e).__name__})"


def subsolar_point(when: datetime | None = None) -> dict:
    """Where the sun is overhead right now — the globe's day/night line is drawn 90° from it.

    NOAA's low-precision solar position: good to a fraction of a degree, which is far closer
    than a terminator drawn on a 700-pixel globe can show.
    """
    when = when or datetime.now(timezone.utc)
    jd = when.timestamp() / 86400.0 + 2440587.5
    n = jd - 2451545.0                                   # days from J2000
    mean_long = (280.460 + 0.9856474 * n) % 360
    mean_anom = math.radians((357.528 + 0.9856003 * n) % 360)
    ecliptic = math.radians(mean_long + 1.915 * math.sin(mean_anom) + 0.020 * math.sin(2 * mean_anom))
    obliquity = math.radians(23.439 - 0.0000004 * n)
    declination = math.degrees(math.asin(math.sin(obliquity) * math.sin(ecliptic)))
    # equation of time, in minutes
    y = math.tan(obliquity / 2) ** 2
    ml = math.radians(mean_long)
    eot = 4 * math.degrees(y * math.sin(2 * ml) - 2 * 0.0167 * math.sin(mean_anom)
                           + 4 * 0.0167 * y * math.sin(mean_anom) * math.cos(2 * ml)
                           - 0.5 * y * y * math.sin(4 * ml)
                           - 1.25 * 0.0167 ** 2 * math.sin(2 * mean_anom))
    hours = when.hour + when.minute / 60 + when.second / 3600
    longitude = -15 * (hours + eot / 60 - 12)
    return {"lat": round(declination, 3), "lng": round(((longitude + 180) % 360) - 180, 3)}


@st.cache_data(ttl=1800, show_spinner=False)
def weather(lat: float, lng: float) -> dict | None:
    """What it is doing right now at a point — Open-Meteo, free and keyless."""
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast",
                         params={"latitude": round(lat, 2), "longitude": round(lng, 2),
                                 "current": "temperature_2m,relative_humidity_2m,weather_code,"
                                            "wind_speed_10m"},
                         headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        now = r.json().get("current") or {}
        if "temperature_2m" not in now:
            return None
        return {"sky": SKY.get(now.get("weather_code"), "—"),
                "temp": round(now["temperature_2m"]),
                "humidity": now.get("relative_humidity_2m"),
                "wind": round(now.get("wind_speed_10m") or 0)}
    except Exception:
        return None          # weather is a nicety; it never holds the panel up


@st.cache_data(ttl=21600, show_spinner=False)
def rates() -> tuple[dict, str]:
    """How much of each currency one US dollar buys, and the day the rates are from."""
    try:
        r = requests.get("https://open.er-api.com/v6/latest/USD", headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if data.get("result") != "success":
            return {}, ""
        return data.get("rates", {}), (data.get("time_last_update_utc") or "")[5:16]
    except Exception:
        return {}, ""
