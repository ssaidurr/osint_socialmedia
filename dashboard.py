"""Negative-news analytics dashboard.   Run:  streamlit run dashboard.py"""
from __future__ import annotations

import difflib
import html as html_lib
import math
import re
from urllib.parse import urlencode

import altair as alt
import pandas as pd
import streamlit as st

from osint import (countries, feeds, gate, geo, layers as static_layers, live, panels, themes,
                   places, weather_tiles)
from osint.config import TICKETS_DIR, load_config, to_local
from osint.globe import drawable_countries, globe
from osint import ui
from osint.ui import (BRAND, HEAT, MUTED, NEG, NEU, POS, RANGES, REMOTE_REPO, SENTIMENTS,
                      data_version, fetch_remote, load)

SENTIMENT_SCALE = alt.Scale(domain=SENTIMENTS, range=[NEG, NEU, POS])

st.set_page_config(page_title="News Monitor", page_icon="📡", layout="wide")
cfg = load_config()
TZ = cfg.get("timezone", "Asia/Dhaka")
alert_cfg = cfg["alert"]





st.markdown(BRAND, unsafe_allow_html=True)
st.title("News Monitor")

# Nobody sees the data without leaving a way to be contacted
demo_user = gate.require_access(cfg)

items, tickets, collected, briefs = load(data_version())

if items.empty:
    # Say exactly why there's nothing to show — the fix differs for each case
    status = fetch_remote("data/osint.db")[1] if REMOTE_REPO else None
    if REMOTE_REPO and status != 200:
        hint = {
            0: "Network error reaching GitHub.",
            401: "GITHUB_TOKEN is invalid or expired.",
            403: "GITHUB_TOKEN lacks access, or the GitHub API rate limit was hit (add a token if you haven't).",
            404: "Either the `data` branch has no data/osint.db yet, or (private repo) GITHUB_TOKEN can't see this "
                 "repo — it needs access to this repository with Contents: Read-only.",
        }.get(status, "Unexpected response from GitHub.")
        st.error(f"Could not read data from GitHub `{REMOTE_REPO}` (HTTP {status}). {hint}")
    elif collected:
        st.warning(f"{collected} items were collected but none are analyzed yet. Check the latest GitHub Actions run log.")
    elif REMOTE_REPO:
        st.info("The database on GitHub is empty. Wait for the next workflow run, then press Refresh.")
    else:
        st.info("No data yet. Locally: run `python main.py run`, then refresh. On Streamlit Cloud: add the "
                "GITHUB_DATA_REPO and GITHUB_TOKEN secrets (app Settings → Secrets), then reboot the app.")
    if st.button("↻ Refresh", key="refresh_empty"):
        st.cache_data.clear()
        st.rerun()
    st.stop()
# How long since the monitor last reported, shown as a beating dot rather than a number
last_seen = pd.to_datetime(items["collected_at"].max(), utc=True, format="ISO8601")
hours_since = (pd.Timestamp.now(tz="UTC") - last_seen).total_seconds() / 3600
st.markdown(
    f'<div class="live"><span class="live-dot{"" if hours_since < 2 else " stale"}"></span>'
    f'Last collection: {to_local(items["collected_at"].max(), cfg)}'
    f'{"" if hours_since < 2 else f" ({hours_since:.0f}h ago)"}</div>',
    unsafe_allow_html=True,
)
gate.sidebar_note(demo_user)

# ─── Filters (one row, above the charts) ──────────────────────────────────────
# f4 is filled far below, by the globe: a column can be written to whenever, and the
# reader should find every control in one row rather than hunting for one of them.
f1, f2, f3, f4, f5 = st.columns([1, 1.8, 1.4, 1.2, 0.6], vertical_alignment="bottom")

# Open on a range that actually holds something. A monitor that has not reported for a day
# would otherwise land every visitor on an empty page — and now that the globe lives here too,
# an empty range takes the map down with it rather than just the charts.
newest = items["ts"].max()
stale_hours = (pd.Timestamp.now(tz=TZ).tz_localize(None) - newest).total_seconds() / 3600
default_range = next((i for i, h in enumerate(RANGES.values()) if h >= stale_hours + 1),
                     len(RANGES) - 1)
range_name = f1.selectbox("Time range", list(RANGES), index=max(1, default_range))
all_types = sorted(items["source_type"].unique())
types = f2.multiselect("Source type", all_types, default=all_types)
country_options = sorted(items["country"].dropna().unique(), key=countries.label)
picked_countries = f3.multiselect("Country", country_options, disabled=not country_options,
                                  format_func=countries.label,
                                  help="The country an item is mainly about, as identified by the analyzer")
if not country_options:
    f3.caption("No country data yet — it is filled on the next monitor run.")
if f5.button("↻ Refresh", width="stretch"):
    st.cache_data.clear()
    st.rerun()
search = st.text_input("Search", placeholder="keywords in the headline, text or summary — e.g. election, flood")

hours = RANGES[range_name]
now = pd.Timestamp.now(tz=TZ).tz_localize(None)

# ─── The brief, for whichever range is selected ───────────────────────────────
with st.container(border=True):
    st.markdown(f"#### 📰 At a glance — {range_name.lower()}")
    brief = briefs.get(hours)
    if brief:
        st.markdown(brief["text"])
        st.caption(f"Written {to_local(brief['created_at'], cfg)}")
    else:
        st.caption("No brief for this range yet — one is written on the next monitor run.")
df = items[(items["ts"] >= now - pd.Timedelta(hours=hours)) & items["source_type"].isin(types)]
if picked_countries:
    df = df[df["country"].isin(picked_countries)]
haystack = None
if search.strip():
    # Every word must appear somewhere in the item, in any order
    haystack = (df["title"].fillna("") + " " + df["text"].fillna("") + " " + df["summary"].fillna("")).str.lower()
    mask = pd.Series(True, index=df.index)
    for word in search.lower().split():
        mask &= haystack.str.contains(word, regex=False)
    df, haystack = df[mask], haystack
neg = df[df["sentiment"] == "negative"]
if df.empty:
    message = "Nothing matches these filters. Try a longer time range, or clear the country/search filter."
    if haystack is not None and len(haystack):
        # A typo shouldn't look like "no data" — offer the closest words that do appear
        vocabulary = set()
        for blob in haystack.head(800):
            vocabulary.update(w for w in re.findall(r"[\w\u0980-\u09FF]{4,}", blob))
        hints = []
        for word in search.lower().split():
            hints += difflib.get_close_matches(word, vocabulary, n=3, cutoff=0.75)
        if hints:
            message += "  Did you mean: " + ", ".join(list(dict.fromkeys(hints))[:5]) + "?"
    st.warning(message)
    st.stop()

# ─── Alert status (same rule as the ticket check) ─────────────────────────────
window_start = now - pd.Timedelta(hours=alert_cfg["window_hours"])
win = items[items["ts"] >= window_start]
win_share = (win["sentiment"] == "negative").mean() if len(win) else 0.0
# Same rule as the ticket check: the bar is the floor or "recent normal + delta", whichever is higher
thr = alert_cfg["negative_ratio_threshold"]
baseline_days, baseline_delta = alert_cfg.get("baseline_days", 7), alert_cfg.get("baseline_delta", 0)
past = items[(items["ts"] >= now - pd.Timedelta(days=baseline_days)) & (items["ts"] < window_start)]
baseline = (past["sentiment"] == "negative").mean() if (
    baseline_delta and len(past) >= alert_cfg.get("baseline_min_items", 200)) else None
bar = max(thr, baseline + baseline_delta) if baseline is not None else thr
status = (f"Last {alert_cfg['window_hours']}h: **{win_share:.0%}** negative "
          f"({(win['sentiment'] == 'negative').sum()}/{len(win)}), alert bar {bar:.0%}"
          + (f" (normal {baseline:.0%} over {baseline_days}d)" if baseline is not None else ""))
if len(win) >= alert_cfg["min_items"] and win_share >= bar:
    st.error(f"Alert level: {status}", icon="🚨")
else:
    st.success(f"Normal: {status}", icon="✅")

# ─── KPIs ─────────────────────────────────────────────────────────────────────
k1, k2, k3, k4 = st.columns(4)
k1.container(border=True).metric("Items analyzed", f"{len(df):,}")
k2.container(border=True).metric("Negative share", f"{len(neg) / len(df):.0%}", help=f"{len(neg)} negative items")
k3.container(border=True).metric("Avg severity (negative)", f"{neg['severity'].mean():.1f} / 5" if len(neg) else "—")
k4.container(border=True).metric("Tickets in range", int((tickets["created"] >= now - pd.Timedelta(hours=hours)).sum()) if len(tickets) else 0)

# ─── The globe, its layers, and the panels that answer for what is selected ───
# This was its own page until the two were merged. It reads the same `df` as everything above,
# so the country and search filters now narrow the map as well — which is the point of their
# being on one page at all.
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
f4.selectbox("Or pick a country", pick_from, key="country_box",
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
# ─── The four ways in from the website, and one more ──────────────────────────
# A link from synergyaxn.com says which question the reader arrived with, and the map opens
# already answering it: ?layers=security ticks the security layers and nothing else. Without
# one the map opens as it always has, on DEFAULT_LAYERS — a bare link must not be a surprise.
#
# The groups overlap on purpose. Armed conflict is news to one reader and security to another,
# and a layer belonging to two questions is not a mistake to be tidied away.
PRESETS = {
    "event":    ("Event", ["quakes", "gdacs", "eonet", "outbreaks", "airemerg", "outages"]),
    "news":     ("News", ["news", "protests", "conflict"]),
    "security": ("Security", ["conflict", "milair", "bases", "nuclear", "chokepoints",
                              "cables", "sats"]),
    "weather":  ("Weather", ["clouds", "rain", "wind", "sun"]),
    # The rest of what is built into the repo: none of it fits the four names above, and
    # leaving it reachable only one tick at a time made it effectively invisible.
    "infrastructure": ("Infrastructure", ["ports", "refineries", "mines", "datacenters",
                                          "exchanges", "banks", "renewables", "spaceports",
                                          "happiness"]),
}

# The link only speaks when it changes; after that the map's own ticks decide, so a reader who
# arrived on ?layers=news can turn one off without it springing back on the next rerun.
arrived = str(st.query_params.get("layers", "")).strip().lower()
if arrived and arrived != st.session_state.get("layers_from_link"):
    st.session_state["layers_from_link"] = arrived
    if arrived in PRESETS:
        st.session_state["globe_layers"] = list(PRESETS[arrived][1])

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

# The presets, as a row of buttons over the map. One lights up only while the ticks are
# exactly its own: hand-pick an extra layer and it goes out, because a lit chip that no longer
# describes the map would be worse than no chip at all.
#
# A lit chip also narrows the panel beside the map to its own stories (osint/themes.py), for
# the world or for the picked country. Pressing it again goes back to the opening layers and
# to every story, so there is always a way back without hunting through the ticks.
active_theme = None
chip_cols = st.columns(len(PRESETS))
for (slug, (label, wanted)), column in zip(PRESETS.items(), chip_cols):
    exact = set(wanted) == keys
    if exact:
        active_theme = slug
    if column.button(label, key=f"preset_{slug}", width="stretch",
                     type="primary" if exact else "secondary",
                     help=(f"Back to every story and the opening layers" if exact else
                           f"Show the {label.lower()} layers, and only {label.lower()} news "
                           f"beside the map — worldwide, or for the picked country")):
        st.session_state["globe_layers"] = ([LAYERS[n] for n in DEFAULT_LAYERS] if exact
                                            else list(wanted))
        st.rerun()

# What the panel beside the map reads: every story, or only the lit chip's
theme_label = PRESETS[active_theme][0] if active_theme else None
theme_df = themes.select(df, active_theme)
theme_located = theme_df[theme_df["country"].notna()]
theme_ranked = ranked if theme_df is df else (
    theme_located.groupby("country").size().rename("total").reset_index()
    .rename(columns={"country": "iso2"}).sort_values("total", ascending=False))


def themed(title: str) -> str:
    """A panel title that says which chip it is answering for."""
    return f"{title} · {theme_label}" if theme_label else title


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
                alongside = theme_located[theme_located["country"] == hit["cc"]]
                if not alongside.empty:
                    st.markdown('<div class="axn-head" style="margin-top:.9rem">'
                                f'<span class="axn-label">{themed("Our news from this country")}</span>'
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
        world = theme_df
        world_negative = world[world["sentiment"] == "negative"]
        st.markdown(panels.head(themed("Worldwide"), range_name.lower(), status=("live", "live"),
                                dense=False,
                                tip="This monitor's own collection in the chosen range. Click "
                                    "a country on the globe to narrow everything to it, or a "
                                    "button above the map to one kind of news.")
                    + '<div class="axn-stats">' + "".join(
                        f'<div><span class="axn-label">{label}</span><span class="v">{value}</span></div>'
                        for label, value in (
                            ("items", f"{len(world):,}"),
                            ("negative", f"{len(world_negative) / len(world):.0%}" if len(world) else "—"),
                            ("avg severity",
                             f"{world_negative['severity'].mean():.1f}" if len(world_negative) else "—"),
                        )) + "</div>", unsafe_allow_html=True)

        if world.empty:
            st.caption(f"No {theme_label.lower()} news in the selected range.")
        split = world["sentiment"].value_counts().reindex(SENTIMENTS).fillna(0).reset_index()
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

        if not theme_ranked.empty:
            busiest = theme_ranked.head(6)
            rows = "".join(
                f'<div class="bar-row"><span class="name">'
                f'{html_lib.escape(countries.NAMES.get(r.iso2, r.iso2))}</span>'
                f'<span class="track"><span class="fill" '
                f'style="width:{r.total / busiest["total"].max():.0%}"></span></span>'
                f'<span class="n">{int(r.total)}</span></div>'
                for r in busiest.itertuples())
            st.markdown('<div class="axn-head" style="margin-top:.9rem">'
                        '<span class="axn-label">Busiest countries</span>'
                        f'<span class="axn-label right">{len(theme_ranked)} with news</span></div>'
                        f'<div class="axn-bars">{rows}</div>', unsafe_allow_html=True)

        st.markdown('<div class="axn-head" style="margin-top:.9rem">'
                    '<span class="axn-label">Latest</span>'
                    '<span class="axn-label right">click the map for one place</span></div>'
                    + headline_rows(world, 6), unsafe_allow_html=True)
    else:
        one = theme_located[theme_located["country"] == picked]
        name = countries.NAMES.get(picked, picked)
        st.markdown(panels.head(themed(name), picked, status=("live", "live"), dense=False,
                                tip="This monitor's own collection from this country, in the "
                                    "chosen range."), unsafe_allow_html=True)
        if one.empty:
            st.caption(f"No {theme_label.lower() + ' ' if theme_label else ''}items from this "
                       "country in the selected range.")
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

def _picker() -> None:
    """Every panel at once: grouped, searchable, and with the ones already up ticked.

    A list of twenty-two names in a dropdown is a worse question than a wall of boxes, because
    it hides what is already on. The ticks are the page's current state, not a separate memory
    of it — the × on a panel and a box here say the same thing to the same list.

    The boxes sit in a form so that ticking four of them costs one redraw rather than four.
    Every widget outside a form reruns the whole page on each click, and this page is twenty
    panels of other people's servers; a chooser that reloaded between every tick would be
    slower to use than the dropdown it replaced.
    """
    groups = ["All"] + sorted({p.group for p in panels.PANELS.values()})
    group = st.radio("Group", groups, key="pick_group", horizontal=True,
                     label_visibility="collapsed")
    needle = st.text_input("Filter", key="pick_filter", placeholder="Filter panels…",
                           label_visibility="collapsed").strip().lower()

    on = set(st.session_state["globe_panels"])
    # A box takes its `value` only the first time its key is seen, so the keys carry a
    # generation that moves whenever the page's own list moves — a panel closed by its × then
    # comes back here unticked. Writing the state directly instead would be read on the
    # submitting run too, and would overwrite the very ticks being submitted.
    if st.session_state.get("pick_seen") != on:
        st.session_state["pick_seen"] = on
        st.session_state["pick_gen"] = st.session_state.get("pick_gen", 0) + 1
    gen = st.session_state.get("pick_gen", 0)

    wanted = [(label, key) for label, key in PANEL_LABELS.items()
              if (group == "All" or panels.PANELS[key].group == group)
              and (not needle or needle in label.lower())]
    if not wanted:
        return st.caption("No panel is called that.")

    with st.form("axn_pick", border=False):
        for row in [wanted[i:i + 3] for i in range(0, len(wanted), 3)]:
            for column, (label, key) in zip(st.columns(3), row):
                column.checkbox(panels.PANELS[key].title, value=label in on,
                                key=f"pick_{gen}_{key}")
        if not st.form_submit_button("Show these", use_container_width=True, type="primary"):
            return

    # Only the boxes on screen have a say: a panel filtered out of view keeps whatever it was,
    # so narrowing to "Risk" and pressing this cannot quietly switch off everything else.
    picks = set(st.session_state["globe_panels"])
    for label, key in wanted:
        picks.add(label) if st.session_state[f"pick_{gen}_{key}"] else picks.discard(label)
    # Rebuilt by walking the registry rather than appending, so a panel switched off and on
    # again comes back where it belongs instead of at the end — the grid never reshuffles.
    st.session_state["globe_panels"] = [name for name in PANEL_LABELS if name in picks]
    st.rerun()


# Two to a row, with the way to add one standing in the last slot. A panel that throws takes
# itself down and says so — every one of these reads someone else's server, and one bad
# morning at NASA must not blank the page.
cells = shown + ["+"]
for pair in [cells[i:i + 2] for i in range(0, len(cells), 2)]:
    # Each card is as tall as what is in it. Holding both to the taller one's height, which is
    # what this did before, meant a short panel carried the difference as blank space — and
    # when both were short the page grew a blank band the width of the screen.
    columns = st.columns(2)
    for column, label in zip(columns, pair):
        if label == "+":
            with column.container(border=False):
                with st.popover("＋ add a panel", use_container_width=True, width=620,
                                help="Each one answers for the country selected on the "
                                     "globe, or for the whole world."):
                    _picker()
            continue
        chosen = panels.PANELS[PANEL_LABELS[label]]
        scope.panel_key = PANEL_LABELS[label]
        with column.container(border=True):
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

freq = "h" if hours <= 72 else "D"
time_fmt = "%d %b %H:%M" if freq == "h" else "%d %b"

# ─── AXN Pulse: live stream, sentiment index, trending narratives ─────────────
# The three panels the SynergyAXN site shows under "News analytics" — here they run on this
# monitor's own collection rather than a simulated feed.
def _cell(value) -> str:
    """A dataframe cell as safe display text — NaN is a float, and a truthy one at that."""
    return "" if pd.isna(value) else html_lib.escape(str(value))


def stream_html(frame: pd.DataFrame, limit: int = 7) -> str:
    rows = []
    for n_row, (_, it) in enumerate(frame.sort_values("ts", ascending=False).head(limit).iterrows()):
        headline = _cell(it["title"]) or _cell(it["text"])[:150] or "—"
        kind = {"negative": "neg", "positive": "pos"}.get(it["sentiment"], "neu")
        # Our data has a sentiment and a 0–5 severity, so the badge says exactly that — no
        # invented decimal score.
        badge = str(it["sentiment"])[:3].upper()
        if kind == "neg" and pd.notna(it["severity"]):
            badge += f" {int(it['severity'])}"
        chips = "".join(f'<span class="chip">{_cell(v)}</span>'
                        for v in (it["country"], it["source_type"]) if pd.notna(v))
        rows.append(
            f'<div class="row" style="animation-delay:{n_row * 65}ms">'
            f'<div class="meta"><span>{it["ts"].strftime("%H:%M:%S")}</span>{chips}'
            f'<span>{_cell(it["category_label"])}</span>'
            f'<span class="score {kind}">{badge}</span></div>'
            f'<div class="head">{headline[:150]}</div></div>')
    return '<div class="axn-stream">' + "".join(rows) + "</div>"


def panel_head(label: str, right: str = "", dot: str = "") -> str:
    return (f'<div class="axn-head">{dot}<span class="axn-label">{label}</span>'
            f'<span class="axn-label right">{right}</span></div>')


p1, p2 = st.columns([1.35, 1])
with p1.container(border=True):
    dot = f'<span class="live-dot{"" if hours_since < 2 else " stale"}"></span>'
    st.markdown(panel_head("Live event stream", f"{len(df):,} items · {range_name.lower()}", dot)
                + stream_html(df), unsafe_allow_html=True)

with p2:
    with st.container(border=True):
        # Index = share positive minus share negative, so it runs from -1 (all bad news) to +1
        positives = int((df["sentiment"] == "positive").sum())
        index = (positives - len(neg)) / len(df)
        st.markdown(
            panel_head("Global sentiment index", f"{len(df):,} articles · {range_name.lower()}")
            + f'<div class="axn-index{" down" if index < 0 else ""}">{index:+.3f}</div>',
            unsafe_allow_html=True)
        spark = df.assign(bucket=df["ts"].dt.floor(freq)).groupby("bucket")["sentiment"].agg(
            index=lambda col: ((col == "positive").sum() - (col == "negative").sum()) / len(col),
            items="size").reset_index()
        floor = spark["index"].min() - .03
        spark["floor"] = floor
        area = alt.Chart(spark).mark_area(
            interpolate="monotone", line={"color": POS, "strokeWidth": 2},
            color=alt.Gradient(gradient="linear", x1=1, x2=1, y1=1, y2=0,
                               stops=[alt.GradientStop(color="#5ee6ff00", offset=0),
                                      alt.GradientStop(color="#5ee6ff4d", offset=1)]),
        ).encode(
            x=alt.X("bucket:T", axis=None),
            y=alt.Y("index:Q", axis=None, scale=alt.Scale(domain=[floor, spark["index"].max() + .03])),
            y2="floor:Q",
            tooltip=[alt.Tooltip("bucket:T", title="Time", format=time_fmt),
                     alt.Tooltip("index:Q", title="Index", format="+.2f"),
                     alt.Tooltip("items:Q", title="Items")],
        ).properties(height=120)
        last = alt.Chart(spark.tail(1)).mark_point(filled=True, size=90, color=POS, opacity=1).encode(
            x="bucket:T", y="index:Q")
        st.altair_chart(area + last, width="stretch")

    with st.container(border=True):
        # A category is only ever set on negative items, so colouring by negative share would
        # paint every bar the same. Severity is the one thing this panel can add: violet marks
        # the narratives running harsher than the window's own average.
        volume = df["category_label"].value_counts().head(8)
        sev = neg.groupby("category_label")["severity"].mean()
        avg_sev = neg["severity"].mean() if len(neg) else 0
        bars = "".join(
            f'<div class="bar-row" title="{_cell(cat)} — {n} items, '
            f'avg severity {sev.get(cat, 0):.1f}/5"><span class="name">{_cell(cat)}</span>'
            f'<span class="track"><span class="fill{" hot" if sev.get(cat, 0) >= avg_sev else ""}" '
            f'style="width:{n / volume.max():.0%};animation-delay:{i * 70}ms"></span></span>'
            f'<span class="n">{n}</span></div>'
            for i, (cat, n) in enumerate(volume.items()))
        st.markdown(panel_head("Trending narratives", f"violet = severity above {avg_sev:.1f}")
                    + f'<div class="axn-bars">{bars}</div>', unsafe_allow_html=True)

# ─── Negative news by type + negative share over time ─────────────────────────
c1, c2 = st.columns(2)
with c1.container(border=True):
    st.subheader("Negative news by type")
    if neg.empty:
        st.caption("No negative items in this range.")
    else:
        by_cat = neg.groupby("category_label").agg(count=("id", "size"), severity=("severity", "mean")).reset_index()
        by_cat["share"] = by_cat["count"] / by_cat["count"].sum()
        base = alt.Chart(by_cat).encode(
            y=alt.Y("category_label:N", sort="-x", title=None),
            x=alt.X("count:Q", title="Negative items", axis=alt.Axis(tickMinStep=1)),
            tooltip=[alt.Tooltip("category_label:N", title="Type"), alt.Tooltip("count:Q", title="Items"),
                     alt.Tooltip("share:Q", title="Share", format=".0%"),
                     alt.Tooltip("severity:Q", title="Avg severity", format=".1f")],
        )
        bars = base.mark_bar(color=NEG, cornerRadiusEnd=4, height={"band": 0.7})
        labels = base.mark_text(align="left", dx=4, color=MUTED).encode(
            text=alt.Text("share:Q", format=".0%"))
        st.altair_chart((bars + labels).properties(height=max(220, 34 * len(by_cat))), width="stretch")

with c2.container(border=True):
    st.subheader("Negative share over time")
    t = df.assign(bucket=df["ts"].dt.floor(freq), is_neg=df["sentiment"] == "negative")
    share = t.groupby("bucket").agg(total=("id", "size"), negative=("is_neg", "sum")).reset_index()
    share["share"] = share["negative"] / share["total"]
    line = alt.Chart(share).mark_line(color=NEG, strokeWidth=2, point=alt.OverlayMarkDef(color=NEG, size=64)).encode(
        x=alt.X("bucket:T", title=None, axis=alt.Axis(format=time_fmt)),
        y=alt.Y("share:Q", title="Negative share", axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0, 1])),
        tooltip=[alt.Tooltip("bucket:T", title="Time", format=time_fmt),
                 alt.Tooltip("share:Q", title="Negative", format=".0%"),
                 alt.Tooltip("negative:Q", title="Negative items"), alt.Tooltip("total:Q", title="All items")],
    )
    rule_df = pd.DataFrame({"y": [thr], "label": [f"Alert threshold {thr:.0%}"]})
    rule = alt.Chart(rule_df).mark_rule(color=MUTED, strokeDash=[4, 4]).encode(y="y:Q")
    rule_label = alt.Chart(rule_df).mark_text(align="left", dy=-6, x=4, color=MUTED).encode(y="y:Q", text="label:N")
    st.altair_chart((rule + rule_label + line).properties(height=320), width="stretch")

# ─── Category × time heatmap ──────────────────────────────────────────────────
if not neg.empty:
    with st.container(border=True):
        st.subheader("Negative news type over time")
        heat = neg.assign(bucket=neg["ts"].dt.floor(freq)).groupby(["category_label", "bucket"]).size().reset_index(name="count")
        order = neg["category_label"].value_counts().index.tolist()
        st.altair_chart(
            alt.Chart(heat).mark_rect(cornerRadius=2, stroke=None).encode(
                x=alt.X("bucket:T", title=None, axis=alt.Axis(format=time_fmt)),
                y=alt.Y("category_label:N", sort=order, title=None),
                color=alt.Color("count:Q", title="Items", scale=alt.Scale(range=HEAT)),
                tooltip=[alt.Tooltip("category_label:N", title="Type"),
                         alt.Tooltip("bucket:T", title="Time", format=time_fmt), alt.Tooltip("count:Q", title="Items")],
            ).properties(height=max(160, 30 * len(order))),
            width="stretch",
        )

# ─── Sentiment volume + top negative sources ──────────────────────────────────
c3, c4 = st.columns(2)
with c3.container(border=True):
    st.subheader("Sentiment volume")
    vol = df.assign(bucket=df["ts"].dt.floor(freq)).groupby(["bucket", "sentiment"]).size().reset_index(name="count")
    st.altair_chart(
        alt.Chart(vol).mark_bar().encode(
            x=alt.X("bucket:T", title=None, axis=alt.Axis(format=time_fmt)),
            y=alt.Y("count:Q", title="Items", stack="zero"),
            color=alt.Color("sentiment:N", scale=SENTIMENT_SCALE, sort=SENTIMENTS,
                            legend=alt.Legend(orient="top", title=None)),
            order=alt.Order("sentiment_order:Q"),
            tooltip=[alt.Tooltip("bucket:T", title="Time", format=time_fmt),
                     alt.Tooltip("sentiment:N", title="Sentiment"), alt.Tooltip("count:Q", title="Items")],
        ).transform_calculate(
            sentiment_order="indexof(['negative','neutral','positive'], datum.sentiment)"
        ).properties(height=300),
        width="stretch",
    )
with c4.container(border=True):
    st.subheader("Negative items by country")
    by_country = neg["country"].dropna()
    if by_country.empty:
        st.caption("No country identified for these items yet — run `python main.py analyze` after an update.")
    else:
        cdf = by_country.value_counts().head(12).rename_axis("country").reset_index(name="count")
        cdf["country"] = cdf["country"].map(countries.label)
        base = alt.Chart(cdf).encode(
            y=alt.Y("country:N", sort="-x", title=None),
            x=alt.X("count:Q", title="Negative items", axis=alt.Axis(tickMinStep=1)),
            tooltip=[alt.Tooltip("country:N", title="Country"), alt.Tooltip("count:Q", title="Negative items")],
        )
        st.altair_chart(
            (base.mark_bar(color=NEG, cornerRadiusEnd=4, height={"band": 0.7})
             + base.mark_text(align="left", dx=4, color=MUTED).encode(text="count:Q")).properties(height=300),
            width="stretch",
        )

with st.container(border=True):
    st.subheader("Top sources of negative content")
    if neg.empty:
        st.caption("No negative items in this range.")
    else:
        src = neg["source"].value_counts().head(10).rename_axis("source").reset_index(name="count")
        base = alt.Chart(src).encode(
            y=alt.Y("source:N", sort="-x", title=None),
            x=alt.X("count:Q", title="Negative items", axis=alt.Axis(tickMinStep=1)),
            tooltip=[alt.Tooltip("source:N", title="Source"), alt.Tooltip("count:Q", title="Negative items")],
        )
        st.altair_chart(
            (base.mark_bar(color=NEG, cornerRadiusEnd=4, height={"band": 0.7})
             + base.mark_text(align="left", dx=4, color=MUTED).encode(text="count:Q")).properties(height=300),
            width="stretch",
        )

# ─── Negative items table ─────────────────────────────────────────────────────
with st.container(border=True):
    st.subheader("Negative items")
    t1, t2 = st.columns([3, 1], vertical_alignment="bottom")
    cat_filter = t1.multiselect("Filter by type", sorted(neg["category_label"].dropna().unique()))
    only_doubtful = t2.checkbox("Only doubtful (credibility < 40)")
    view = neg[neg["category_label"].isin(cat_filter)] if cat_filter else neg
    if only_doubtful:
        view = view[view["credibility"] < 40]
    view = view.assign(
        country=view["country"].map(countries.label),
        headline=view["title"].where(view["title"].fillna("") != "", view["text"].str[:160]),
        factcheck=(view["factcheck_rating"].fillna("") + " — " + view["factcheck_publisher"].fillna("")).str.strip(" —"),
    )
    # Fourteen days of negatives is ~16,000 rows, and every one of them crosses the wire on
    # every rerun — 9 MB for a table nobody scrolls to the end of. The severe ones are the
    # point; the filters and the search reach the rest.
    TABLE_LIMIT = 1000
    shown = view.sort_values(["severity", "ts"], ascending=[False, False]).head(TABLE_LIMIT)
    st.dataframe(
        shown[["ts", "severity", "category_label", "country", "headline", "summary", "credibility", "cred_reason",
               "factcheck", "factcheck_url", "source", "source_type", "url"]],
        hide_index=True, width="stretch",
        column_config={
            "ts": st.column_config.DatetimeColumn("Time", format="D MMM, h:mm a"),
            "severity": st.column_config.ProgressColumn("Severity", min_value=0, max_value=5, format="%d"),
            "category_label": "Type", "country": "Country", "headline": "Headline / text", "summary": "Summary",
            "credibility": st.column_config.ProgressColumn(
                "Credibility", min_value=0, max_value=100, format="%d",
                help="Not a truth score. Combines published fact-checks, how many independent sources carry the "
                     "story, and what kind of source it is."),
            "cred_reason": "Why", "factcheck": "Fact check",
            "factcheck_url": st.column_config.LinkColumn("Fact-check link", display_text="open"),
            "source": "Source", "source_type": "Kind",
            "url": st.column_config.LinkColumn("Link", display_text="open"),
        },
    )
    if len(view) > TABLE_LIMIT:
        st.caption(f"Showing the {TABLE_LIMIT:,} most severe of {len(view):,} negative items — "
                   "narrow the time range, filter by type, or search to reach the rest.")

def ticket_label(ticket_id: str) -> str:
    """Tickets from before the rename keep their filename; only what people read changes."""
    return str(ticket_id).replace("OSINT-", "SAXN-").replace("AXN-", "SAXN-").replace("SSAXN-", "SAXN-")


# ─── Tickets ──────────────────────────────────────────────────────────────────
with st.container(border=True):
    st.subheader("🎫 Tickets")
    if tickets.empty:
        st.caption("No tickets yet. A ticket is raised when the negative share crosses the threshold.")
    else:
        st.dataframe(
            tickets.assign(id=tickets["id"].map(ticket_label))[
                ["id", "created", "level", "ratio", "negative", "total", "status", "error"]],
            hide_index=True, width="stretch",
            column_config={"created": st.column_config.DatetimeColumn("Created", format="D MMM, h:mm a"),
                           "ratio": st.column_config.NumberColumn("Negative share", format="percent")},
        )
        pick = st.selectbox("Preview ticket email", tickets["id"], format_func=ticket_label)
        report, status = None, None
        if REMOTE_REPO:
            raw, status = fetch_remote(f"data/tickets/{pick}.html")
            report = raw.decode("utf-8") if raw else None
        local = TICKETS_DIR / f"{pick}.html"
        if report is None and local.exists():
            # The monitor writes every ticket here too, so a rate-limited or missing remote
            # copy need not leave an empty panel
            report = local.read_text(encoding="utf-8")
        if report is None:
            st.caption({
                403: "GitHub is rate-limiting this app — without a GITHUB_TOKEN secret it gets "
                     "60 requests an hour. Add one, or try again in a few minutes.",
                404: f"No saved copy of {pick}: tickets raised before the reports were published "
                     "to the data branch have none.",
                401: "GITHUB_TOKEN is invalid or expired.",
                0: "Could not reach GitHub.",
            }.get(status, f"Could not load this ticket's email (HTTP {status})."))
        if report:
            # Reports written before the rename carry the old name and ticket id. The file stays
            # as it was sent; what the preview shows wears the current brand.
            report = (report.replace("OSINT Social Media Monitor", "AXN Pulse · News Monitor")
                            .replace("OSINT-", "SAXN-"))
            # st.html sanitizes; the report holds headlines and links scraped from the internet.
            # The email is written for a white inbox, so it gets a white sheet here too — on the
            # dark dashboard its own grey text would be unreadable.
            st.html(f'<div style="background:#ffffff;padding:18px;border-radius:10px">{report}</div>')


ui.motion()
