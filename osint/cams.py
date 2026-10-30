"""The cameras pointed at the world, and the newsrooms pointed at it.

Two feeds that answer the same question from opposite ends. A broadcaster tells you what an
editor thinks happened; a webcam tells you what a street looks like with nobody deciding. When
a story is still forming the second one is often worth more, which is why both sit here rather
than only the first.

The television side is keyless: every broadcaster below publishes its own round-the-clock
stream, addressed by channel rather than by video so the link survives the nightly restart
that gives the stream a new video id. Nothing loads until someone presses play.

The webcam side wants a free Windy key. Windy carries the only geolocated public webcam
catalogue worth having — tens of thousands of cameras, each with a country, which is what lets
this panel narrow to whatever country is selected. Without the key the panel falls back to the
handful of always-on public streams listed at the bottom, and says so plainly rather than
pretending the world has six cameras in it.
"""
from __future__ import annotations

import os
import time

import requests
import streamlit as st

WEBCAMS = "https://api.windy.com/webcams/api/v3/webcams"
JAMCAMS = "https://api.tfl.gov.uk/Place/Type/JamCam"
CALTRANS = "https://cwwp2.dot.ca.gov/data/d{d}/cctv/cctvStatusD{d:0>2}.json"
SINGAPORE = "https://api.data.gov.sg/v1/transport/traffic-images"
TIMEOUT = 12

# ─── Television ───────────────────────────────────────────────────────────────
# Ordered so the four that most often carry a story first sit at the front of the strip.
CHANNELS = [
    ("Al Jazeera", "UCNye-wNBqNL5ZzHSJj3l8Bg"),
    ("Al Arabiya", "UCIZJ9a6P_nxCFJTmL0gh_IQ"),
    ("Sky News", "UCoMdktPbSTixAyNGwb-UYkQ"),
    ("CNN", "UCupvZG-5ko_eiXAupbDfxWw"),
    ("Bloomberg", "UCIALMKvObZNtJ6AmdCLP7Lg"),
    ("DW", "UCknLrEdhRCp1aegoMqRaCZg"),
    ("France 24", "UCQfwfsi5VrQ8yKZ-UWmAEFg"),
    ("euronews", "UCSrZ3UV4jOidv8ppoVuvW9Q"),
    ("TRT World", "UC7fWeaHhqgM4Ry-RMpM2YYw"),
    ("CNA", "UC83jt4dlz1Gjl58fzQrrKZg"),
    ("NHK World", "UCSPEjw8F2nQDtmUKPFNF7_A"),
    ("ABC Australia", "UCVgO39Bk5sMo66-6o6Spn6Q"),
]

# Which broadcaster to open on when a country is selected — the one whose newsroom is nearest
# and most likely to be carrying it. Everything unlisted opens on Al Jazeera.
NEAREST = {
    "IL": "Sky News", "PS": "Al Jazeera", "LB": "Al Jazeera", "SY": "Al Arabiya",
    "IR": "Al Arabiya", "IQ": "Al Arabiya", "SA": "Al Arabiya", "AE": "Al Arabiya",
    "QA": "Al Jazeera", "YE": "Al Jazeera", "EG": "Al Arabiya", "JO": "Al Arabiya",
    "TR": "TRT World", "UA": "DW", "RU": "DW", "PL": "DW", "DE": "DW", "FR": "France 24",
    "BE": "euronews", "IT": "euronews", "ES": "euronews", "GR": "euronews",
    "GB": "Sky News", "IE": "Sky News", "US": "CNN", "CA": "CNN", "MX": "CNN",
    "CN": "CNA", "SG": "CNA", "MY": "CNA", "ID": "CNA", "TH": "CNA", "VN": "CNA",
    "JP": "NHK World", "KR": "NHK World", "TW": "NHK World",
    "IN": "NHK World", "PK": "Al Jazeera", "BD": "Al Jazeera", "AF": "Al Jazeera",
    "AU": "ABC Australia", "NZ": "ABC Australia", "PG": "ABC Australia", "FJ": "ABC Australia",
}


def embed(channel_id: str, rolling: bool = False) -> str:
    """YouTube's own channel-to-current-stream redirect. No key, no scraping, no API quota.

    `rolling` starts the picture at once with the sound off, which is how a camera should
    behave — nobody clicks play on a window. Broadcasters are left click-to-play: a newsroom
    that starts talking the moment a page loads is a different and much ruder thing.
    """
    url = f"https://www.youtube.com/embed/live_stream?channel={channel_id}"
    return url + "&autoplay=1&mute=1" if rolling else url


def opening_channel(cc: str | None) -> str:
    return NEAREST.get(cc or "", CHANNELS[0][0])


# ─── Webcams ──────────────────────────────────────────────────────────────────
# The tabs worth having, as Windy's own filters express them. Continents where a continent will
# do; an explicit country list for the Middle East, which Windy files under Asia with everyone
# from Japan to Kazakhstan. Space is not Windy's at all — see SPACE below.
REGIONS = [
    ("Mideast", {"countries": "IL,PS,LB,SY,IR,IQ,SA,AE,TR,EG"}),
    ("Europe", {"continents": "EU"}),
    ("Americas", {"continents": "NA,SA"}),
    ("Asia", {"continents": "AS"}),
    ("Africa", {"continents": "AF"}),
    ("Oceania", {"continents": "OC"}),
]

def key() -> str | None:
    """The Windy webcams key, from the environment or secrets.toml. Absent is not an error."""
    if os.getenv("WINDY_WEBCAMS_KEY"):
        return os.getenv("WINDY_WEBCAMS_KEY")
    try:
        return st.secrets.get("windy_webcams_key")
    except Exception:  # no secrets.toml when running locally
        return None


@st.cache_data(ttl=300, show_spinner=False)
def _ask(params: tuple) -> list[dict]:
    """One call to Windy. Cached for five minutes because the image links are signed and the
    free tier's signature dies after ten — a longer cache would serve pictures that 401."""
    token = key()
    if not token:
        return []
    query = dict(params)
    query.update({"include": "images,location,player,urls", "limit": "24",
                  "sortKey": "popularity", "sortDirection": "desc"})
    try:
        reply = requests.get(WEBCAMS, params=query, timeout=TIMEOUT,
                             headers={"x-windy-api-key": token})
        if reply.status_code != 200:
            return []
        rows = reply.json().get("webcams") or []
    except Exception:
        return []

    cams = []
    for row in rows:
        if not isinstance(row, dict) or row.get("status") not in (None, "active"):
            continue
        where = row.get("location") or {}
        images = ((row.get("images") or {}).get("current") or {})
        player = (row.get("player") or {})
        cams.append({
            "id": row.get("webcamId"),
            "title": row.get("title") or where.get("city") or "Webcam",
            "city": where.get("city") or "",
            "country": where.get("country") or "",
            "cc": (where.get("countryCode") or "").upper(),
            "lat": where.get("latitude"), "lng": where.get("longitude"),
            "thumb": images.get("preview") or images.get("thumbnail") or "",
            "live": (player.get("live") or {}).get("embed") or "",
            "day": (player.get("day") or {}).get("embed") or "",
            "page": (row.get("urls") or {}).get("detail") or "",
            "as_of": row.get("lastUpdatedOn") or "",
        })
    return cams


def region(name: str) -> list[dict]:
    """Every camera Windy will give us for one of the tabs above."""
    chosen = dict(REGIONS).get(name)
    return _ask(tuple(sorted(chosen.items()))) if chosen else []


def country(cc: str, lat: float | None = None, lng: float | None = None) -> list[dict]:
    """Cameras in one country, or — when Windy files none under it — within 250km of its
    largest city, which is the furthest its nearby filter will reach."""
    cams = _ask((("countries", cc),))
    if not cams and lat is not None and lng is not None:
        cams = _ask((("nearby", f"{lat:.3f},{lng:.3f},250"),))
    return cams



# ─── The keyless sources ─────────────────────────────────────────────────────
# Windy is the only catalogue that covers the whole world, and it wants a key — worldmonitor's
# own webcam layer is the same API with the same requirement. What follows is what can be had
# without one: three transport authorities that leave their traffic cameras open, no key and no
# referrer check, a fresh JPEG every few minutes. Three cities are not the world, but they are
# real, placed, and already looking at something.
#
# Pictures rather than players throughout. Nearly every webcam channel on YouTube allowlists
# the domains allowed to embed it, so a wall built from them is dark everywhere but the
# owner's own site — measured, not assumed.
def _stamp() -> int:
    """A number that changes every five minutes. These cameras overwrite one fixed address, so
    without it the browser would go on showing this morning's traffic."""
    return int(time.time() // 300)


CAM_TIMEOUT = 8


def _fetch(url: str):
    try:
        reply = requests.get(url, timeout=CAM_TIMEOUT,
                             headers={"User-Agent": "axn-pulse-news-monitor/1.0"})
        return reply.json() if reply.status_code == 200 else None
    except Exception:
        return None


def _spread(cams: list[dict], limit: int) -> list[dict]:
    """Take them from across the list rather than off the front, which on a road authority's
    feed is the same junction photographed eight times."""
    step = max(1, len(cams) // limit) if cams else 1
    return cams[::step][:limit]


@st.cache_data(ttl=300, show_spinner=False)
def _london(limit: int = 6) -> list[dict]:
    """Transport for London's 890 traffic cameras."""
    rows = _fetch(JAMCAMS) or []
    cams = []
    for row in rows:
        extra = {p.get("key"): p.get("value") for p in (row.get("additionalProperties") or [])}
        if str(extra.get("available")).lower() != "true" or not extra.get("imageUrl"):
            continue
        cams.append({"title": row.get("commonName") or "Camera", "city": "London",
                     "country": "United Kingdom", "cc": "GB",
                     "lat": row.get("lat"), "lng": row.get("lon"),
                     "thumb": f'{extra["imageUrl"]}?t={_stamp()}',
                     "page": extra.get("videoUrl") or row.get("url") or "", "live": "",
                     "as_of": ""})
    return _spread(cams, limit)


@st.cache_data(ttl=300, show_spinner=False)
def _california(limit: int = 6) -> list[dict]:
    """Caltrans' Los Angeles cameras, about 600 of them. District 7 only: the other district
    files are served slowly enough to hold a panel open, and one city fewer beats a page that
    waits on a road authority."""
    cams = []
    for row in (_fetch(CALTRANS.format(d=7)) or {}).get("data", []):
        cctv = row.get("cctv") or {}
        where, image = cctv.get("location") or {}, cctv.get("imageData") or {}
        url = ((image.get("static") or {}).get("currentImageURL") or "").strip()
        if str(cctv.get("inService")).lower() != "true" or not url:
            continue
        cams.append({"title": where.get("locationName") or "Camera", "city": "Los Angeles",
                     "country": "United States", "cc": "US",
                     "lat": where.get("latitude"), "lng": where.get("longitude"),
                     "thumb": f"{url}?t={_stamp()}", "page": url, "live": "", "as_of": ""})
    return _spread(cams, limit)


@st.cache_data(ttl=300, show_spinner=False)
def _singapore(limit: int = 6) -> list[dict]:
    """Singapore's expressway cameras, published by the Land Transport Authority."""
    items = (_fetch(SINGAPORE) or {}).get("items") or []
    cams = []
    for row in (items[0].get("cameras") if items else []) or []:
        where = row.get("location") or {}
        if not row.get("image"):
            continue
        # Each capture gets its own address here, so this one needs no cache-buster
        cams.append({"title": f'Camera {row.get("camera_id", "")}'.strip(), "city": "Singapore",
                     "country": "Singapore", "cc": "SG",
                     "lat": where.get("latitude"), "lng": where.get("longitude"),
                     "thumb": row["image"], "page": row["image"], "live": "",
                     "as_of": str(row.get("timestamp", ""))[:10]})
    return _spread(cams, limit)


# Which open source answers for which of the tabs, and for which country when one is selected.
OPEN = {"Europe": ("London", _london), "Americas": ("California", _california),
        "Asia": ("Singapore", _singapore)}
OPEN_BY_CC = {"GB": "Europe", "US": "Americas", "SG": "Asia"}


def open_region(name: str, limit: int = 6) -> tuple[list[dict], str]:
    """The cameras for one tab without a key, and the name of the place they are in."""
    if name not in OPEN:
        return [], ""
    where, fetch = OPEN[name]
    return fetch(limit), where
