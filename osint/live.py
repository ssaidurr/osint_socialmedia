"""Live layers that move: aircraft, satellites, outbreaks and outages.

These are the feeds that change while you are looking at them, kept apart from feeds.py so the
earthquakes-and-weather set stays readable. None of them needs a key:

  adsb.lol    military aircraft currently transmitting ADS-B, and anyone squawking distress
  CelesTrak   orbital elements, propagated here so satellites sit where they actually are
  WHO         Disease Outbreak News, the official notifications
  IODA        country-level internet outages, Georgia Tech

Each one returns (points, note) like feeds.py does, and the same rule holds: a feed that is down
gives back an empty list and the reason, never an exception.
"""
from __future__ import annotations

import datetime as dt
import math

import requests
from sgp4 import omm
from sgp4.api import Satrec, jday

from .feeds import HEADERS, _held

# Squawk codes a crew sets when something has gone wrong. 7500 is unlawful interference, 7600 a
# radio failure, 7700 a general emergency — they are worth their own layer because they are
# rare, and because when one appears it is usually news before it is anything else.
DISTRESS = {"7500": "Unlawful interference", "7600": "Radio failure", "7700": "Emergency"}


def _aircraft() -> tuple[list[dict], str]:
    try:
        r = requests.get("https://api.adsb.lol/v2/mil", headers=HEADERS, timeout=25)
        r.raise_for_status()
        craft = r.json().get("ac") or []
    except Exception as e:
        return [], f"Military aircraft unavailable ({type(e).__name__})"
    out = []
    for a in craft:
        if "lat" not in a or "lon" not in a:
            continue                                  # heard, but not yet placed
        flight = (a.get("flight") or a.get("r") or a.get("hex") or "").strip()
        out.append({"lat": float(a["lat"]), "lng": float(a["lon"]),
                    "call": flight, "kind": a.get("t") or "",
                    "alt": a.get("alt_baro") if isinstance(a.get("alt_baro"), int) else 0,
                    "track": a.get("track") or 0,
                    "squawk": str(a.get("squawk") or "")})
    return out, ""


def military_flights() -> tuple[list[dict], str]:
    """Military aircraft in the air right now, as far as ground receivers can hear them."""
    return _held("adsb", 90, _aircraft)


def aircraft_emergencies() -> tuple[list[dict], str]:
    """The ones squawking distress. Usually none, which is the point of showing the count."""
    craft, note = military_flights()
    return [dict(a, reason=DISTRESS[a["squawk"]]) for a in craft if a["squawk"] in DISTRESS], note


# ─── Satellites ───────────────────────────────────────────────────────────────
# CelesTrak publishes mean orbital elements, not positions, and the two-body shortcut drifts a
# few hundred kilometres a day on a low orbit — visible, on a globe this size. SGP4 is the model
# the elements were fitted with, so it is the one used here.

EARTH_RADIUS = 6_378.137                     # km, equatorial


def _gmst(when: dt.datetime) -> float:
    """Greenwich mean sidereal time in radians — how far the earth has turned under the orbit."""
    days = (when - dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc)).total_seconds() / 86_400
    return math.radians((280.46061837 + 360.98564736629 * days) % 360)


def _satellites(group: str) -> tuple[list[dict], str]:
    try:
        r = requests.get("https://celestrak.org/NORAD/elements/gp.php",
                         params={"GROUP": group, "FORMAT": "json"},
                         headers=HEADERS, timeout=30)
        r.raise_for_status()
        elements = r.json()
        if not isinstance(elements, list):
            raise ValueError("CelesTrak returned a message, not elements")
    except Exception as e:
        return [], f"Orbital elements unavailable ({type(e).__name__})"

    now = dt.datetime.now(dt.timezone.utc)
    julian, fraction = jday(now.year, now.month, now.day, now.hour, now.minute,
                            now.second + now.microsecond / 1e6)
    spin = _gmst(now)
    out = []
    for s in elements:
        try:
            propagator = Satrec()
            omm.initialize(propagator, s)
            error, position, _ = propagator.sgp4(julian, fraction)
        except Exception:
            continue
        if error:                                     # decayed, or elements too old to propagate
            continue
        # SGP4 answers in TEME; turning it to earth-fixed is one rotation by sidereal time
        x, y, z = position
        earth_x = x * math.cos(spin) + y * math.sin(spin)
        earth_y = -x * math.sin(spin) + y * math.cos(spin)
        radius = math.sqrt(earth_x ** 2 + earth_y ** 2 + z ** 2)
        out.append({"lat": round(math.degrees(math.asin(z / radius)), 3),
                    "lng": round((math.degrees(math.atan2(earth_y, earth_x)) + 180) % 360 - 180, 3),
                    "name": (s.get("OBJECT_NAME") or "").strip(),
                    "norad": s.get("NORAD_CAT_ID", ""),
                    "altitude": round(radius - EARTH_RADIUS)})
    return out, ""


def satellites() -> tuple[list[dict], str]:
    """Earth-observation satellites: the ones that are looking back down."""
    return _held("satellites", 120, lambda: _satellites("resource"))


def stations() -> tuple[list[dict], str]:
    """The crewed stations and the few dozen objects keeping station with them."""
    return _held("stations", 120, lambda: _satellites("stations"))


# ─── Disease outbreaks ────────────────────────────────────────────────────────

def _outbreaks() -> tuple[list[dict], str]:
    try:
        r = requests.get("https://www.who.int/api/news/diseaseoutbreaknews",
                         params={"sf_provider": "dynamicProvider372", "sf_culture": "en",
                                 "$orderby": "PublicationDateAndTime desc", "$top": "60",
                                 "$format": "json"},
                         headers=HEADERS, timeout=30)
        r.raise_for_status()
        items = r.json().get("value") or []
    except Exception as e:
        return [], f"WHO outbreak news unavailable ({type(e).__name__})"
    out = []
    for item in items:
        title = (item.get("Title") or "").strip()
        if not title:
            continue
        out.append({"title": title,
                    "ts": (item.get("PublicationDateAndTime") or "")[:10],
                    "url": "https://www.who.int/emergencies/disease-outbreak-news/item/"
                           + (item.get("UrlName") or "")})
    return out, ""


def outbreaks() -> tuple[list[dict], str]:
    """WHO Disease Outbreak News. The notices name a country in the title and nothing else, so
    the place is found the same way our own stories are: by reading the headline."""
    return _held("who", 3600, _outbreaks)


# ─── Internet disruptions ─────────────────────────────────────────────────────

def _outages() -> tuple[list[dict], str]:
    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    try:
        r = requests.get("https://api.ioda.inetintel.cc.gatech.edu/v2/outages/alerts",
                         params={"from": now - 86_400, "until": now, "limit": 300},
                         headers=HEADERS, timeout=30)
        r.raise_for_status()
        alerts = r.json().get("data") or []
    except Exception as e:
        return [], f"IODA outage alerts unavailable ({type(e).__name__})"
    seen, out = set(), []
    for a in alerts:
        entity = a.get("entity") or {}
        if entity.get("type") != "country":
            continue                                  # regions and ASNs have no one place
        code = entity.get("code") or ""
        if code in seen:
            continue                                  # one line per country, the first is newest
        seen.add(code)
        out.append({"cc": code, "name": entity.get("name") or code,
                    "level": a.get("level") or "",
                    "ts": dt.datetime.fromtimestamp(a.get("time", now), dt.timezone.utc)
                            .strftime("%d %b %H:%M UTC")})
    return out, ""


def outages() -> tuple[list[dict], str]:
    """Countries IODA saw drop off the internet in the last day."""
    return _held("ioda", 900, _outages)
