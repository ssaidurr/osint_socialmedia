"""The globe: where the news came from, and what one country is saying.

Spin it, hover a country for its totals, click one to read it. The country outlines live in
components/globe/countries.geojson — generated once from Natural Earth, so nothing is fetched
from anyone else's CDN at render time.
"""
from __future__ import annotations

import html as html_lib
import math
from urllib.parse import urlencode
import altair as alt
import pandas as pd
import streamlit as st

from osint import (countries, feeds, gate, geo, layers as static_layers, live, panels,
                   places, ui, weather_tiles)
from osint.globe import drawable_countries, globe
from osint.config import load_config
from osint.ui import BRAND, NEG, NEU, POS, RANGES, SENTIMENTS, data_version, load

st.set_page_config(page_title="World map · News Monitor", page_icon="🌍", layout="wide")
cfg = load_config()
TZ = cfg.get("timezone", "Asia/Dhaka")

st.markdown(BRAND, unsafe_allow_html=True)
st.title("World map")
st.page_link("dashboard.py", label="← Back to the news monitor")

# The globe is behind the same gate as everything else
demo_user = gate.require_access(cfg)

items, tickets, collected, briefs = load(data_version())
if items.empty:
    st.info("No analyzed items yet — run the monitor, then come back.")
    st.stop()

# ─── Filters ──────────────────────────────────────────────────────────────────
# Open on a range that actually holds something. A monitor that has not reported for days
# would otherwise land every visitor on an empty page with nothing to click.
newest = items["ts"].max()
stale_hours = (pd.Timestamp.now(tz=TZ).tz_localize(None) - newest).total_seconds() / 3600
default_range = next((i for i, h in enumerate(RANGES.values()) if h >= stale_hours + 1), len(RANGES) - 1)

f1, f2, f3 = st.columns([1, 1.4, 1])
range_name = f1.selectbox("Time range", list(RANGES), index=max(1, default_range))
hours = RANGES[range_name]
all_types = sorted(items["source_type"].unique())
types = f2.multiselect("Source type", all_types, default=all_types)

now = pd.Timestamp.now(tz=TZ).tz_localize(None)
df = items[(items["ts"] >= now - pd.Timedelta(hours=hours)) & items["source_type"].isin(types)]
if df.empty:
    st.warning(f"No items in this range — the monitor last reported {stale_hours:.0f} hours ago. "
               "Pick a longer range above.")
    st.stop()

located = df[df["country"].notna()]
by_country = located.groupby("country").agg(
    total=("id", "size"),
    negative=("sentiment", lambda col: int((col == "negative").sum())),
    severity=("severity", "mean"),
).reset_index().rename(columns={"country": "iso2"})
by_country["share"] = by_country["negative"] / by_country["total"]
by_country["name"] = by_country["iso2"].map(lambda c: countries.NAMES.get(c, c))

# A country the map cannot draw is still a country with news in it — say so rather than drop it
drawable = drawable_countries()
off_map = by_country[~by_country["iso2"].isin(drawable)].sort_values("total", ascending=False)

ranked = by_country.sort_values("total", ascending=False)

# ─── Which country is selected ────────────────────────────────────────────────
# The picker and the map are two ways into one choice, so there is one place that choice is
# kept: st.session_state["globe_pick"]. Both used to assert their own answer on every rerun —
# the picker re-reading its value, the map component handing back the same click forever — and
# then overwrite each other, which is how the picker could say United States while the panel
# beside it said Afghanistan. Now each of them speaks only when it *changes*.
NOWHERE = "— none —"
busiest = [r.iso2 for _, r in ranked.iterrows() if r.iso2 in drawable]
quiet = sorted(drawable - set(busiest), key=lambda c: countries.NAMES.get(c, c))
# Every country the map can draw is on the list, not only the ones with news: the membership
# then never changes, so the picker can always show whatever the map has selected — and a quiet
# country is somewhere you can still go and look.
pick_from = [NOWHERE] + [f"{c} — {countries.NAMES.get(c, c)}" for c in busiest + quiet]


def _country_label(pick) -> str:
    """The picker's line for whatever is selected — a country, or the country a city is in."""
    if not isinstance(pick, str):
        return NOWHERE
    code = pick.rsplit("|", 1)[-1] if pick.startswith("city:") else pick
    label = f"{code} — {countries.NAMES.get(code, code)}"
    return label if label in pick_from else NOWHERE


def _picked_from_box() -> None:
    """Runs when the picker is actually changed, and only then."""
    choice = st.session_state.get("country_box", NOWHERE)
    if choice == NOWHERE:
        st.session_state.pop("globe_pick", None)
    else:
        st.session_state["globe_pick"] = choice.split(" — ")[0]


st.session_state["country_box"] = _country_label(st.session_state.get("globe_pick"))
f3.selectbox("Or pick a country", pick_from, key="country_box",
             on_change=_picked_from_box,
             help="The same as clicking it on the globe.")

# ─── The layers laid over the countries ───────────────────────────────────────
# Our own stories go on as one dot per city; the rest are public feeds that need no key.
# Colour and size are decided here, so the globe only has to draw what it is handed.
LAYERS = {
    # what our own collection knows
    "News by city": "news",
    "Protests": "protests",
    "Armed conflict": "conflict",
    # live feeds
    "Earthquakes": "quakes",
    "Disaster alerts": "gdacs",
    "Wildfires & storms": "eonet",
    # things that move, from feeds that need no key
    "Military aircraft": "milair",
    "Aircraft emergencies": "airemerg",
    "Satellites": "sats",
    "Disease outbreaks": "outbreaks",
    "Internet outages": "outages",
    # the world as it stands, built into the repo by tools/build_layers.py
    "Undersea cables": "cables",
    "Nuclear sites": "nuclear",
    "Clean energy": "renewables",
    "Spaceports": "spaceports",
    "Chokepoints": "chokepoints",
    "Military bases": "bases",
    "Data centres": "datacenters",
    "Ports": "ports",
    "Mining sites": "mines",
    "Refineries": "refineries",
    "Stock exchanges": "exchanges",
    "Central banks": "banks",
    "World happiness": "happiness",
    # the surface itself
    "Clouds": "clouds",
    "Rain radar": "rain",
    "Wind": "wind",
    "Day & night": "sun",
}
# One swatch per layer, far enough apart to read on a dark globe.
# One colour per layer, in one place, so the dot on the map and the glyph in the panel can
# never drift apart. Twenty-eight layers is more than there are colours anyone can tell apart,
# so colour groups them — our own collection cyan, hazards warm, air and space blue, energy
# green, money and people bright — and the icon in components/globe/icons.js tells them apart.
PALETTE = {
    # our own collection
    "News by city": "94,230,255",
    "Protests": "255,122,200",
    "Armed conflict": "255,99,71",
    # hazards
    "Earthquakes": "255,224,102",
    "Disaster alerts": "238,244,255",
    "Wildfires & storms": "255,159,28",
    "Disease outbreaks": "248,80,120",
    # air and space
    "Military aircraft": "96,165,250",
    "Aircraft emergencies": "244,63,94",
    "Satellites": "45,212,191",
    "Spaceports": "197,160,255",
    # infrastructure
    "Undersea cables": "94,230,255",
    "Internet outages": "251,191,36",
    "Data centres": "129,140,248",
    "Ports": "125,211,252",
    "Chokepoints": "255,214,54",
    # energy and industry
    "Nuclear sites": "126,255,164",
    "Clean energy": "74,222,128",
    "Mining sites": "180,140,100",
    "Refineries": "250,120,60",
    "Military bases": "148,163,184",
    # money and people
    "Stock exchanges": "163,230,53",
    "Central banks": "232,121,249",
    "World happiness": "255,214,102",
    # the surface itself
    "Clouds": "236,240,245",
    "Rain radar": "77,166,255",
    "Wind": "170,196,214",
    "Day & night": "255,214,54",
}
SWATCH = {name: f"rgba({rgb},0.95)" for name, rgb in PALETTE.items()}
SWATCH["Undersea cables"] = "rgba(94,230,255,0.6)"      # a line, not a dot: it reads brighter
# Which layers are on is the map's own business: the panel inside it sends the list back and we
# remember it here. Day & night starts off — it is the one layer that makes half the map harder
# to read before you have asked it to.
DEFAULT_LAYERS = ["News by city", "Earthquakes", "Disaster alerts", "Wildfires & storms"]
keys = set(st.session_state.get("globe_layers", [LAYERS[n] for n in DEFAULT_LAYERS]))

# One hue per layer, far enough apart to read on a dark globe. Our own city dots keep the
# sentiment ramp (cyan → violet → red), so the feeds avoid that stretch of the spectrum.
FEED_STYLE = {
    "quake": ("Earthquake · USGS", "255,224,102"),        # pale yellow
    "alert": ("Disaster alert · GDACS", "238,244,255"),   # white
    "fire":  ("Wildfire · NASA EONET", "255,159,28"),     # orange, clear of our negative red
    "storm": ("Storm · NASA EONET", "77,166,255"),        # blue
    "ice":   ("Sea ice · NASA EONET", "159,232,255"),     # pale blue
}

markers, paths, counted, feed_notes, pins = [], [], {}, [], []

if "news" in keys:
    # One dot per city, not per story: a hundred pins on Delhi says nothing a single sized dot
    # does not say better.
    pins[:] = places.locate(tuple(df[["id", "title", "summary", "country", "sentiment",
                                      "severity", "source", "url", "ts", "category_label"]]
                                  .itertuples(index=False, name=None)))
    if pins:
        frame = pd.DataFrame(pins)
        grouped = frame.groupby(["city", "country"], as_index=False).agg(
            items=("title", "size"),
            negative=("sentiment", lambda col: int((col == "negative").sum())),
            worst=("severity", "max"),
            lat=("lat", "first"), lng=("lng", "first"), headline=("title", "first"))
        biggest = grouped["items"].max()
        for _, c in grouped.iterrows():
            share = c["negative"] / c["items"]
            rgb = "248,113,113" if share >= 0.6 else "139,124,255" if share >= 0.3 else "94,230,255"
            markers.append({
                "lat": float(c["lat"]), "lng": float(c["lng"]),
                "r": round(0.16 + 0.34 * (math.log10(c["items"] + 1) / math.log10(biggest + 1)), 3),
                "color": f"rgba({rgb},0.9)",
                "pulse": bool(c["worst"] and c["worst"] >= 4),
                "title": f"{c['city']}", "kicker": c["country"],
                "layer": "City news · size = items", "swatch": SWATCH["News by city"], "icon": "news",
                "cc": c["country"],
                "detail": f"{int(c['items'])} items · {share:.0%} negative<br>{html_lib.escape(str(c['headline'])[:90])}",
                "ts": "", "pick": f"city:{c['city']}|{c['country']}"})
        counted["News by city"] = len(grouped)

# Our own stories, split out by what they are about: the same pins, asked a narrower question.
for key, label, wanted, rgb in (("protests", "Protests", "Political unrest", "255,122,200"),
                                ("conflict", "Armed conflict", "Security & terrorism", "255,99,71")):
    if key not in keys:
        continue
    subset = [pin for pin in pins if pin.get("category") == wanted]
    counted[label] = len(subset)
    for pin in subset:
        markers.append({
            "lat": pin["lat"], "lng": pin["lng"], "r": 0.2,
            "color": f"rgba({rgb},0.9)", "pulse": bool(pin["severity"] and pin["severity"] >= 4),
            "title": html_lib.escape(pin["city"]), "kicker": pin["country"],
            "layer": f"{label} · our collection", "swatch": f"rgba({rgb},0.95)", "icon": key,
            "cc": pin["country"], "detail": html_lib.escape(pin["title"][:110]),
            "ts": pin["ts"], "pick": f"city:{pin['city']}|{pin['country']}"})

# ─── The world as it stands ───────────────────────────────────────────────────
# These all come out of osint/data as rows of {name, lat, lng, cc}, so one table does for all of
# them: the only thing that separates a nuclear station from a stock exchange here is its
# colour and the line under its name. tools/build_layers.py is what fills the files.

def _plant(row) -> str:
    return f"{html_lib.escape(str(row.get('cc', '')))} · {row.get('mw', 0):,} MW"


def _named_country(row) -> str:
    return html_lib.escape(countries.NAMES.get(row.get("cc", ""), row.get("cc", "")))


SITE_LAYERS = {
    # key: (label, rows, radius, caption) — the colour comes from PALETTE, by label
    "nuclear":     ("Nuclear sites", lambda: static_layers.items("nuclear"),
                    0.18, _plant),
    "renewables":  ("Clean energy", lambda: static_layers.items("renewables"),
                    0.16, lambda r: f"{html_lib.escape(r.get('fuel', ''))} · {r.get('mw', 0):,} MW"),
    "spaceports":  ("Spaceports", static_layers.spaceports,
                    0.2, lambda r: html_lib.escape(r.get("cc", ""))),
    "chokepoints": ("Chokepoints", static_layers.chokepoints,
                    0.26, lambda r: html_lib.escape(r.get("detail", ""))),
    "bases":       ("Military bases", static_layers.military_bases,
                    0.15, _named_country),
    "datacenters": ("Data centres", lambda: static_layers.items("datacenters"),
                    0.17, _named_country),
    "ports":       ("Ports", lambda: static_layers.items("ports"),
                    0.15, _named_country),
    "mines":       ("Mining sites", lambda: static_layers.items("mines"),
                    0.15, _named_country),
    "refineries":  ("Refineries", lambda: static_layers.items("refineries"),
                    0.16, _named_country),
    "exchanges":   ("Stock exchanges", lambda: static_layers.items("exchanges"),
                    0.18, _named_country),
    "banks":       ("Central banks", lambda: static_layers.items("central_banks"),
                    0.18, _named_country),
}

for key, (label, rows, radius, caption) in SITE_LAYERS.items():
    if key not in keys:
        continue
    rgb = PALETTE[label]
    found = rows()
    counted[label] = len(found)
    for row in found:
        markers.append({
            "lat": row["lat"], "lng": row["lng"], "r": radius,
            "color": f"rgba({rgb},0.9)", "pulse": False,
            "title": html_lib.escape(row["name"]), "kicker": "",
            "layer": label, "swatch": f"rgba({rgb},0.95)", "icon": key,
            "cc": row.get("cc", ""), "detail": caption(row), "ts": "", "pick": ""})

if "cables" in keys:
    routes = static_layers.cables()
    counted["Undersea cables"] = len(routes)
    for route in routes:
        for line in route["lines"]:
            paths.append({"points": line, "name": html_lib.escape(route["name"])})

for key, label, fetch in (("quakes", "Earthquakes", feeds.earthquakes),
                          ("gdacs", "Disaster alerts", feeds.disasters),
                          ("eonet", "Wildfires & storms", feeds.natural_events)):
    if key not in keys:
        continue
    events, note = fetch()
    if note:
        feed_notes.append(note)
    counted[label] = len(events)
    for e in events:
        layer_name, rgb = FEED_STYLE.get(e["kind"], ("Event", "248,113,113"))
        loud = e["level"] in ("red", "amber")
        markers.append({
            "lat": e["lat"], "lng": e["lng"],
            "r": 0.3 if e["level"] == "red" else 0.24 if e["level"] == "amber" else 0.18,
            "color": f"rgba({rgb},{0.95 if loud else 0.75})", "pulse": e["level"] == "red",
            "title": html_lib.escape(e["title"]), "kicker": "",
            "layer": layer_name, "swatch": f"rgba({rgb},0.95)", "icon": key,
            # the feeds give coordinates, not countries: the outlines answer that
            "cc": geo.country_of(e["lat"], e["lng"]) or "",
            "detail": html_lib.escape(e["detail"])[:120], "ts": e["ts"],
            "url": e.get("url", ""), "level": e["level"],
            "pick": f"event:{e['kind']}|{e['lat']}|{e['lng']}"})

# ─── The layers that move ─────────────────────────────────────────────────────
# Aircraft, satellites, outbreaks and outages, each from a feed that needs no key. They are
# built one at a time rather than from a table because no two of them answer the same shape.

if "milair" in keys or "airemerg" in keys:
    craft, note = live.military_flights()
    if note:
        feed_notes.append(note)
    if "milair" in keys:
        counted["Military aircraft"] = len(craft)
        for a in craft:
            markers.append({
                "lat": a["lat"], "lng": a["lng"], "r": 0.13,
                "color": f'rgba({PALETTE["Military aircraft"]},0.9)', "pulse": False,
                "title": html_lib.escape(a["call"] or "unknown"), "kicker": "",
                "layer": "Military aircraft", "swatch": SWATCH["Military aircraft"], "icon": "milair",
                "cc": "", "detail": f"{html_lib.escape(a['kind'])} · {a['alt']:,} ft",
                "ts": "", "pick": ""})
    if "airemerg" in keys:
        urgent, _ = live.aircraft_emergencies()
        counted["Aircraft emergencies"] = len(urgent)
        for a in urgent:
            markers.append({
                "lat": a["lat"], "lng": a["lng"], "r": 0.3,
                "color": SWATCH["Aircraft emergencies"], "pulse": True,
                "title": html_lib.escape(a["call"] or "unknown"), "kicker": "",
                "layer": "Aircraft emergency", "swatch": SWATCH["Aircraft emergencies"], "icon": "airemerg",
                "cc": "", "detail": f"squawk {a['squawk']} · {html_lib.escape(a['reason'])}",
                "ts": "", "pick": ""})

if "sats" in keys:
    orbiting, note = live.satellites()
    crewed, _ = live.stations()
    if note:
        feed_notes.append(note)
    counted["Satellites"] = len(orbiting) + len(crewed)
    for s in orbiting + crewed:
        markers.append({
            "lat": s["lat"], "lng": s["lng"], "r": 0.14,
            "color": f'rgba({PALETTE["Satellites"]},0.9)', "pulse": False,
            "title": html_lib.escape(s["name"]), "kicker": "",
            "layer": "Satellite", "swatch": SWATCH["Satellites"], "icon": "sats",
            "cc": "", "detail": f"{s['altitude']:,} km up · NORAD {s['norad']}",
            "ts": "", "pick": ""})

if "outbreaks" in keys:
    notices, note = live.outbreaks()
    if note:
        feed_notes.append(note)
    placed = 0
    for notice in notices:
        # WHO names the country in the headline and nowhere else, and names more than one when
        # an outbreak has crossed a border: "… Democratic Republic of the Congo, Kenya and
        # Nigeria" wants three marks, not one.
        for code in countries.named_in(notice["title"]):
            seat = places.biggest_city(code)
            if not seat:
                continue
            placed += 1
            markers.append({
                "lat": seat[1], "lng": seat[2], "r": 0.24,
                "color": f'rgba({PALETTE["Disease outbreaks"]},0.9)', "pulse": False,
                "title": html_lib.escape(notice["title"][:60]),
                "kicker": countries.NAMES.get(code, code),
                "layer": "Disease outbreak · WHO", "swatch": SWATCH["Disease outbreaks"], "icon": "outbreaks",
                "cc": code, "detail": html_lib.escape(notice["title"][:120]),
                "ts": notice["ts"], "url": notice["url"], "pick": ""})
    counted["Disease outbreaks"] = placed

if "outages" in keys:
    down, note = live.outages()
    if note:
        feed_notes.append(note)
    counted["Internet outages"] = len(down)
    for country in down:
        seat = places.biggest_city(country["cc"])
        if not seat:
            continue
        markers.append({
            "lat": seat[1], "lng": seat[2], "r": 0.26,
            "color": f'rgba({PALETTE["Internet outages"]},0.9)', "pulse": country["level"] == "critical",
            "title": html_lib.escape(country["name"]), "kicker": "",
            "layer": "Internet outage · IODA", "swatch": SWATCH["Internet outages"], "icon": "outages",
            "cc": country["cc"], "detail": f"{html_lib.escape(country['level'])} · {country['ts']}",
            "ts": "", "pick": ""})

if "happiness" in keys:
    # A country-level score with no single place to put it: the biggest city stands in for the
    # country, which is the same stand-in the weather and the exchange rate already use.
    scores = static_layers.items("happiness")
    counted["World happiness"] = len(scores)
    for row in scores:
        code = next(iter(countries.named_in(row["name"])), None)
        seat = places.biggest_city(code) if code else None
        if not seat:
            continue
        warmth = max(0.0, min(1.0, (row["score"] - 3.0) / 5.0))     # 3 is grim, 8 is Nordic
        markers.append({
            "lat": seat[1], "lng": seat[2], "r": round(0.14 + 0.2 * warmth, 3),
            "color": f"rgba({int(255 - 90 * warmth)},{int(120 + 110 * warmth)},102,0.9)",
            "pulse": False,
            "title": html_lib.escape(row["name"]), "kicker": "",
            "layer": "World happiness", "swatch": SWATCH["World happiness"], "icon": "happiness",
            "cc": code, "detail": f"{row['score']:.2f} / 10 · {row['year']}",
            "ts": "", "pick": ""})

sun = feeds.subsolar_point() if "sun" in keys else None

# ─── How the world is drawn ───────────────────────────────────────────────────
# The 3D/2D and MAP/SAT buttons live inside the map, where the eye already is. They switch
# instantly in the browser and tell us afterwards, so the choice survives a rerun — and so the
# satellite imagery is only fetched once somebody actually asks for it.
view = st.session_state.get("globe_view", "3d")
surface = st.session_state.get("globe_surface", "map")

# The weather the globe is wrapped in: cloud, radar, wind and the night side, composed
# server-side into one transparent texture per step and laid *over* the surface. The surface
# itself is the globe's own business, so switching a weather layer on no longer costs you the
# satellite imagery you were zooming into.
weather_frames: list[dict] = []
if {"clouds", "rain", "wind", "sun"} & keys:
    with st.spinner("Fetching the weather…"):
        weather_frames, weather_note = weather_tiles.frames(
            count=6, with_clouds="clouds" in keys, with_rain="rain" in keys,
            with_wind="wind" in keys, night=sun)
    if weather_note:
        feed_notes.append(weather_note)
    if weather_frames:
        counted["Weather steps"] = len(weather_frames)

# The panel lists every layer there is, on or off, with what each one found. A layer that is on
# and came back empty says so rather than vanishing — which is what made a six-line key look
# like a three-line one the morning NASA's feed was down.
# The surface layers are all one texture, so they count the steps that were composed, not dots.
steps = {"clouds": len(weather_frames), "rain": len(weather_frames),
         "wind": len(weather_frames), "sun": 1 if sun else 0}
layer_panel = [{"key": key, "name": name, "swatch": SWATCH[name], "on": key in keys,
                "count": steps.get(key, counted.get(name, 0))}
               for name, key in LAYERS.items()]

# ─── Globe + the country panel ────────────────────────────────────────────────
picked = st.session_state.get("globe_pick")

# Both panels stand the same height, and the one with more to say scrolls inside itself —
# a column twice as tall as its neighbour makes the page look broken rather than full.
PANEL_HEIGHT = 820
GLOBE_HEIGHT = PANEL_HEIGHT - 112        # the head line and the source caption take the rest

left, right = st.columns([1.9, 1])

with left.container(border=True, height=PANEL_HEIGHT):
    st.markdown('<div class="axn-head"><span class="axn-label">Where the news is</span>'
                f'<span class="axn-label right">{len(located):,} located · {range_name.lower()}</span></div>',
                unsafe_allow_html=True)
    try:
        clicked = globe(ranked[["iso2", "name", "total", "negative", "share"]].to_dict("records"),
                        selected=picked, height=GLOBE_HEIGHT,
                        markers=markers, sun=sun, dim="news" in keys,
                        weather=weather_frames, paths=paths,
                        view=view, surface=surface, layers=layer_panel)
    except TypeError:
        # Streamlit Cloud re-reads this file on deploy but keeps modules it has already
        # imported, so for one run the new page can be talking to the old osint.globe. A
        # restart is the only cure, and saying so beats a traceback.
        st.error("This build is half-loaded: the page is newer than the component it calls. "
                 "Open **Manage app** (bottom right) and press **Reboot app**.")
        st.stop()
    if counted:
        st.caption(" · ".join(f"{name}: {n:,}" for name, n in counted.items())
                   + " — cities from GeoNames; events from USGS, GDACS (UN/EC) and NASA EONET.")
    for note in feed_notes:
        st.caption(f"⚠ {note}")

# The map speaks back in three ways: a place was picked, or one of its own buttons was pressed
if clicked and str(clicked).startswith("layers:"):
    wanted = [k for k in str(clicked)[7:].split(",") if k]
    if set(wanted) != keys:
        st.session_state["globe_layers"] = wanted
        st.rerun()
    clicked = None

if clicked and str(clicked).startswith(("view:", "surface:")):
    kind, _, value = str(clicked).partition(":")
    key = "globe_view" if kind == "view" else "globe_surface"
    if st.session_state.get(key) != value:
        st.session_state[key] = value
        st.rerun()
    clicked = None

# A click on the map arrives after it was drawn, so acting on it means going round again. The
# component hands back its last value on every rerun, so only a value we have not already acted
# on counts as a click — otherwise an old click keeps re-selecting itself.
if clicked and clicked != st.session_state.get("last_click"):
    st.session_state["last_click"] = clicked
    st.session_state["globe_pick"] = clicked
    st.rerun()


def headline_rows(frame: pd.DataFrame, limit: int = 8) -> str:
    out = []
    for _, it in frame.sort_values("ts", ascending=False).head(limit).iterrows():
        title = it["title"] if pd.notna(it["title"]) else (it["text"] if pd.notna(it["text"]) else "")
        kind = {"negative": "neg", "positive": "pos"}.get(it["sentiment"], "neu")
        badge = str(it["sentiment"])[:3].upper()
        if kind == "neg" and pd.notna(it["severity"]):
            badge += f" {int(it['severity'])}"
        link = html_lib.escape(str(it["url"])) if pd.notna(it["url"]) else ""
        head = html_lib.escape(str(title)[:150]) or "—"
        out.append(
            f'<div class="row"><div class="meta"><span>{it["ts"].strftime("%d %b %H:%M")}</span>'
            f'<span class="chip">{html_lib.escape(str(it["source"]))[:28]}</span>'
            f'<span class="score {kind}">{badge}</span></div>'
            f'<div class="head">{f"<a href=\'{link}\' target=\'_blank\'>{head}</a>" if link else head}</div>'
            f'{panels.tags(panels.provenance(it))}</div>')
    return '<div class="axn-stream">' + "".join(out) + "</div>"



def context_strip(country_code: str, point: tuple | None = None) -> str:
    """Weather where the story is, and what the local money is worth — two public feeds.

    Drawn with the same weight as the numbers above it: as a pair of pills it read as a
    footnote and went unnoticed. Both are optional — if a feed is unreachable its block is
    simply left out.
    """
    blocks = []
    place = point or places.biggest_city(country_code)
    if place:
        name, lat, lng = place
        now = feeds.weather(lat, lng)
        if now:
            blocks.append(
                f'<div><span class="axn-label">weather · {html_lib.escape(name)}</span>'
                f'<span class="v">{now["temp"]}°C <em>{now["sky"]}</em></span>'
                f'<span class="sub">{now["humidity"]}% humidity · {now["wind"]} km/h wind</span></div>')
    money = places.currencies().get(country_code)
    table, as_of = feeds.rates()
    if money and table.get(money[0]):
        rate = table[money[0]]
        shown = f"{rate:,.2f}" if rate >= 1 else f"{rate:.4f}"
        blocks.append(
            f'<div><span class="axn-label">1 USD buys</span>'
            f'<span class="v">{shown} <em>{money[0]}</em></span>'
            f'<span class="sub">{html_lib.escape(money[1])} · {as_of}</span></div>')
    return f'<div class="axn-context">{"".join(blocks)}</div>' if blocks else ""


with right.container(border=True, height=PANEL_HEIGHT):
    if picked and str(picked).startswith("event:"):
        # The key carries the kind and the coordinates, so it still finds its event after a rerun
        hit = next((m for m in markers if str(m.get("pick", "")) == str(picked)), None)
        if not hit:
            st.markdown('<div class="axn-head"><span class="axn-label">Event</span></div>',
                        unsafe_allow_html=True)
            st.caption("That event is no longer in the feed — it refreshes every few minutes.")
        else:
            st.markdown(
                f'<div class="axn-head"><span class="axn-label">{hit["title"]}</span>'
                f'<span class="axn-label right">{hit["layer"].split(" · ")[-1]}</span></div>'
                f'<div class="axn-stats">'
                f'<div><span class="axn-label">layer</span><span class="v" style="font-size:1rem">'
                f'{hit["layer"].split(" · ")[0]}</span></div>'
                f'<div><span class="axn-label">level</span><span class="v" style="font-size:1rem">'
                f'{hit.get("level", "—")}</span></div>'
                f'<div><span class="axn-label">country</span><span class="v" style="font-size:1rem">'
                f'{countries.NAMES.get(hit.get("cc", ""), hit.get("cc") or "at sea")}</span></div>'
                f'</div>'
                f'<p style="color:#c6ccd4;margin:.2rem 0 .4rem">{hit["detail"]}</p>'
                f'<p class="axn-label">{hit["ts"]}</p>', unsafe_allow_html=True)
            if hit.get("url"):
                st.markdown(f'[Open the source report]({hit["url"]})')
            # what our own collection is saying in the same country
            if hit.get("cc"):
                alongside = located[located["country"] == hit["cc"]]
                if not alongside.empty:
                    st.markdown('<div class="axn-head" style="margin-top:.9rem">'
                                '<span class="axn-label">Our news from this country</span>'
                                f'<span class="axn-label right">{len(alongside):,} items</span></div>'
                                + headline_rows(alongside, 5), unsafe_allow_html=True)
        if st.button("Clear selection"):
            st.session_state.pop("globe_pick", None)
            st.rerun()

    elif picked and str(picked).startswith("city:"):
        # A city dot was clicked: show that city's stories rather than the whole country's
        city_name, _, city_cc = str(picked)[5:].partition("|")
        st.markdown(panels.head(city_name, city_cc, status=("live", "live"), dense=False,
                                tip="Stories this monitor pinned to this city."),
                    unsafe_allow_html=True)
        here = [pin for pin in (pins if "news" in keys else [])
                if pin["city"] == city_name and pin["country"] == city_cc]
        if not here:
            st.caption("Nothing from this city in the selected range.")
        else:
            negative_here = [pin for pin in here if pin["sentiment"] == "negative"]
            severities = [pin["severity"] for pin in negative_here if pin["severity"]]
            stats = [("items", f"{len(here):,}"),
                     ("negative", f"{len(negative_here) / len(here):.0%}"),
                     ("worst severity", f"{max(severities):.0f}" if severities else "—")]
            st.markdown('<div class="axn-stats">' + "".join(
                f'<div><span class="axn-label">{label}</span><span class="v">{value}</span></div>'
                for label, value in stats) + "</div>", unsafe_allow_html=True)
            st.markdown(context_strip(city_cc, (city_name, here[0]["lat"], here[0]["lng"])),
                        unsafe_allow_html=True)
            rows = []
            for pin in sorted(here, key=lambda x: x["ts"], reverse=True)[:10]:
                kind = {"negative": "neg", "positive": "pos"}.get(pin["sentiment"], "neu")
                head = html_lib.escape(pin["title"][:150]) or "—"
                link = html_lib.escape(pin["url"])
                rows.append(
                    f'<div class="row"><div class="meta"><span>{html_lib.escape(pin["ts"])}</span>'
                    f'<span class="chip">{html_lib.escape(pin["source"])[:26]}</span>'
                    f'<span class="score {kind}">{pin["sentiment"][:3].upper()}</span></div>'
                    f'<div class="head">{f"<a href=\'{link}\' target=\'_blank\'>{head}</a>" if link else head}</div></div>')
            st.markdown('<div class="axn-head" style="margin-top:.6rem">'
                        '<span class="axn-label">Latest</span></div>'
                        '<div class="axn-stream">' + "".join(rows) + "</div>", unsafe_allow_html=True)
        if st.button("Clear selection"):
            st.session_state.pop("globe_pick", None)
            st.rerun()
    elif not picked:
        # Nothing picked yet, so the panel answers for the whole world. An empty panel beside a
        # full globe reads as something broken; this reads as the global view it is.
        world_negative = df[df["sentiment"] == "negative"]
        st.markdown(panels.head("Worldwide", range_name.lower(), status=("live", "live"),
                                dense=False,
                                tip="This monitor's own collection in the chosen range. Click "
                                    "a country on the globe to narrow everything to it.")
                    + '<div class="axn-stats">' + "".join(
                        f'<div><span class="axn-label">{label}</span><span class="v">{value}</span></div>'
                        for label, value in (
                            ("items", f"{len(df):,}"),
                            ("negative", f"{len(world_negative) / len(df):.0%}"),
                            ("avg severity",
                             f"{world_negative['severity'].mean():.1f}" if len(world_negative) else "—"),
                        )) + "</div>", unsafe_allow_html=True)

        split = df["sentiment"].value_counts().reindex(SENTIMENTS).fillna(0).reset_index()
        split.columns = ["sentiment", "count"]
        st.altair_chart(
            alt.Chart(split).mark_bar(cornerRadius=4, height={"band": 0.6}).encode(
                x=alt.X("count:Q", title=None, axis=alt.Axis(tickMinStep=1)),
                y=alt.Y("sentiment:N", title=None, sort=SENTIMENTS),
                color=alt.Color("sentiment:N", legend=None,
                                scale=alt.Scale(domain=SENTIMENTS, range=[NEG, NEU, POS])),
                tooltip=["sentiment:N", "count:Q"],
            ).properties(height=112), width="stretch")

        world_categories = world_negative["category_label"].dropna().value_counts().head(5)
        if not world_categories.empty:
            bars = "".join(
                f'<div class="bar-row"><span class="name">{html_lib.escape(str(cat))}</span>'
                f'<span class="track"><span class="fill" '
                f'style="width:{n / world_categories.max():.0%}"></span></span>'
                f'<span class="n">{n}</span></div>' for cat, n in world_categories.items())
            st.markdown('<div class="axn-head"><span class="axn-label">What the negative news '
                        f'is about</span></div><div class="axn-bars">{bars}</div>',
                        unsafe_allow_html=True)

        if not ranked.empty:
            busiest = ranked.head(6)
            rows = "".join(
                f'<div class="bar-row"><span class="name">'
                f'{html_lib.escape(countries.NAMES.get(r.iso2, r.iso2))}</span>'
                f'<span class="track"><span class="fill" '
                f'style="width:{r.total / busiest["total"].max():.0%}"></span></span>'
                f'<span class="n">{int(r.total)}</span></div>'
                for r in busiest.itertuples())
            st.markdown('<div class="axn-head" style="margin-top:.9rem">'
                        '<span class="axn-label">Busiest countries</span>'
                        f'<span class="axn-label right">{len(ranked)} with news</span></div>'
                        f'<div class="axn-bars">{rows}</div>', unsafe_allow_html=True)

        st.markdown('<div class="axn-head" style="margin-top:.9rem">'
                    '<span class="axn-label">Latest</span>'
                    '<span class="axn-label right">click the map for one place</span></div>'
                    + headline_rows(df, 6), unsafe_allow_html=True)
    else:
        one = located[located["country"] == picked]
        name = countries.NAMES.get(picked, picked)
        st.markdown(panels.head(name, picked, status=("live", "live"), dense=False,
                                tip="This monitor's own collection from this country, in the "
                                    "chosen range."), unsafe_allow_html=True)
        if one.empty:
            st.caption("No items from this country in the selected range.")
        else:
            negative = one[one["sentiment"] == "negative"]
            stats = [("items", f"{len(one):,}"),
                     ("negative", f"{len(negative) / len(one):.0%}"),
                     ("avg severity", f"{negative['severity'].mean():.1f}" if len(negative) else "—")]
            st.markdown(
                '<div class="axn-stats">' + "".join(
                    f'<div><span class="axn-label">{label}</span><span class="v">{value}</span></div>'
                    for label, value in stats) + "</div>", unsafe_allow_html=True)
            st.markdown(context_strip(picked), unsafe_allow_html=True)

            split = one["sentiment"].value_counts().reindex(SENTIMENTS).fillna(0).reset_index()
            split.columns = ["sentiment", "count"]
            st.altair_chart(
                alt.Chart(split).mark_bar(cornerRadius=4, height={"band": 0.6}).encode(
                    x=alt.X("count:Q", title=None, axis=alt.Axis(tickMinStep=1)),
                    y=alt.Y("sentiment:N", title=None, sort=SENTIMENTS),
                    color=alt.Color("sentiment:N", legend=None,
                                    scale=alt.Scale(domain=SENTIMENTS, range=[NEG, NEU, POS])),
                    tooltip=["sentiment:N", "count:Q"],
                ).properties(height=112), width="stretch")

            cats = negative["category_label"].dropna().value_counts().head(5)
            if not cats.empty:
                bars = "".join(
                    f'<div class="bar-row"><span class="name">{html_lib.escape(str(cat))}</span>'
                    f'<span class="track"><span class="fill" style="width:{n / cats.max():.0%}"></span></span>'
                    f'<span class="n">{n}</span></div>' for cat, n in cats.items())
                st.markdown('<div class="axn-head"><span class="axn-label">What the negative news '
                            f'is about</span></div><div class="axn-bars">{bars}</div>',
                            unsafe_allow_html=True)

            # Everything the globe is showing inside this country: its city dots and any live
            # event that fell within its outline.
            here = [m for m in markers if m.get("cc") == picked]
            if here:
                cities = [m for m in here if str(m.get("pick", "")).startswith("city:")]
                events_here = [m for m in here if str(m.get("pick", "")).startswith("event:")]
                lines = []
                for m in sorted(cities, key=lambda m: -m["r"])[:6]:
                    count = m["detail"].split(" items")[0]
                    lines.append(f'<div class="bar-row"><span class="name">'
                                 f'<i style="display:inline-block;width:8px;height:8px;border-radius:50%;'
                                 f'background:{m["color"]};margin-right:.45rem"></i>{m["title"]}</span>'
                                 f'<span class="n">{count}</span></div>')
                for m in events_here[:6]:
                    lines.append(f'<div class="bar-row"><span class="name">'
                                 f'<i style="display:inline-block;width:8px;height:8px;border-radius:50%;'
                                 f'background:{m["color"]};margin-right:.45rem"></i>{m["title"]}</span>'
                                 f'<span class="n" style="width:auto">{m["layer"].split(" · ")[0]}</span></div>')
                st.markdown('<div class="axn-head" style="margin-top:.9rem">'
                            '<span class="axn-label">On the map here</span>'
                            f'<span class="axn-label right">{len(cities)} cities · '
                            f'{len(events_here)} events</span></div>'
                            f'<div class="axn-bars">{"".join(lines)}</div>', unsafe_allow_html=True)

            st.markdown('<div class="axn-head" style="margin-top:.9rem">'
                        '<span class="axn-label">Latest</span></div>' + headline_rows(one),
                        unsafe_allow_html=True)
            if st.button("Clear selection"):
                st.session_state.pop("globe_pick", None)
                st.rerun()

# ─── The panels under the globe ───────────────────────────────────────────────
# Everything below answers for whatever is selected above: the world, or one country. The
# selection is worked out once, here, so no panel has to know that a click can arrive as a
# country, a city or an event.
def _scope_country() -> str | None:
    pick = str(picked or "")
    if pick.startswith("city:"):
        return pick[5:].partition("|")[2] or None
    if pick.startswith("event:"):
        hit = next((m for m in markers if str(m.get("pick", "")) == pick), None)
        return (hit or {}).get("cc") or None
    return pick if len(pick) == 2 else None


scope_cc = _scope_country()
scope = panels.Scope(
    cc=scope_cc,
    name=countries.NAMES.get(scope_cc, scope_cc) if scope_cc else "Worldwide",
    news=located[located["country"] == scope_cc] if scope_cc else df,
    world_news=df,
    markers=markers,
    range_name=range_name,
)

PANEL_LABELS = {f"{p.group} · {p.title}": key for key, p in panels.PANELS.items()}
OPENING = ["News · Live intelligence", "News · Threat timeline", "Risk · Instability",
           "Money · News ↔ markets", "Live · Live news TV", "Live · Live webcams",
           "Money · Markets", "Money · Metals & materials", "Money · Predictions",
           "Risk · Live hazards", "News · What it is about"]
st.session_state.setdefault("globe_panels", OPENING)

# A set of panels travels in the link — ?panels=markets,macro, ?panels=all, ?panels=none — so
# a view worth showing someone can be sent as a URL, and so a panel's own × has something to
# link to. The link only speaks when it changes; after that the box on the page decides.
asked = str(st.query_params.get("panels", "")).strip()
if asked and asked != st.session_state.get("panels_from_link"):
    st.session_state["panels_from_link"] = asked
    if asked == "all":
        st.session_state["globe_panels"] = list(PANEL_LABELS)
    elif asked == "none":
        st.session_state["globe_panels"] = []
    else:
        # Walk the registry rather than the link, so a set of panels always comes out in the
        # same order whichever order the link happened to list them in.
        wanted = {name.strip() for name in asked.split(",")}
        st.session_state["globe_panels"] = [label for label, key in PANEL_LABELS.items()
                                            if key in wanted]

shown = [label for label in st.session_state["globe_panels"] if label in PANEL_LABELS]


def _without(key: str) -> str:
    """The link to this page with one panel closed, keeping whatever else is in the address."""
    rest = [PANEL_LABELS[label] for label in shown if PANEL_LABELS[label] != key]
    carried = {name: value for name, value in st.query_params.items() if name != "panels"}
    carried["panels"] = ",".join(rest) or "none"
    return "?" + urlencode(carried)


scope.close_url = _without

# Two to a row, with the way to add one standing in the last slot. A panel that throws takes
# itself down and says so — every one of these reads someone else's server, and one bad
# morning at NASA must not blank the page.
cells = shown + ["+"]
for pair in [cells[i:i + 2] for i in range(0, len(cells), 2)]:
    # Both cards in a row stand to the taller one's height. Letting each keep its own left a
    # ragged hole beside every short panel, which reads as a panel that failed to load.
    tall = max((panels.PANELS[PANEL_LABELS[label]].height
                for label in pair if label != "+"), default=300)
    columns = st.columns(2)
    for column, label in zip(columns, pair):
        if label == "+":
            with column.container(border=False):
                with st.popover("＋ add a panel", use_container_width=True):
                    st.multiselect(
                        "Panels", list(PANEL_LABELS), key="globe_panels",
                        label_visibility="collapsed",
                        help="Each one answers for the country selected on the globe, or for "
                             "the whole world.")
            continue
        chosen = panels.PANELS[PANEL_LABELS[label]]
        scope.panel_key = PANEL_LABELS[label]
        with column.container(border=True, height=tall):
            try:
                chosen.draw(scope)
            except Exception as problem:            # noqa: BLE001 — a panel is never fatal
                st.markdown(panels.head(chosen.title, shut=scope.shut()), unsafe_allow_html=True)
                st.caption(f"This panel could not be drawn: {type(problem).__name__}.")

# ─── The ranking, and whatever the map cannot draw ────────────────────────────
with st.container(border=True):
    st.subheader("Countries by volume")
    table = ranked.head(25).assign(
        country=lambda d: d["iso2"].map(countries.label),
        share=lambda d: d["share"],
        severity=lambda d: d["severity"].round(1))
    st.dataframe(
        table[["country", "total", "negative", "share", "severity"]],
        hide_index=True, width="stretch",
        column_config={
            "country": "Country",
            "total": st.column_config.NumberColumn("Items"),
            "negative": st.column_config.NumberColumn("Negative"),
            "share": st.column_config.ProgressColumn("Negative share", min_value=0, max_value=1,
                                                     format="percent"),
            "severity": st.column_config.NumberColumn("Avg severity", format="%.1f"),
        })
    if not off_map.empty:
        names = ", ".join(f"{r['name']} ({int(r.total)})" for _, r in off_map.head(8).iterrows())
        st.caption(f"Not drawn on the globe — too small for the 110m outlines, or not a country: "
                   f"{names}. They are {int(off_map['total'].sum()):,} of {len(located):,} located items.")

st.caption(f"{len(df) - len(located):,} items in this range have no country — world or tech stories "
           "the analyzer could not pin to one.")
ui.motion()
