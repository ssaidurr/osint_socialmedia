"""The globe: where the news came from, and what one country is saying.

Spin it, hover a country for its totals, click one to read it. The country outlines live in
components/globe/countries.geojson — generated once from Natural Earth, so nothing is fetched
from anyone else's CDN at render time.
"""
from __future__ import annotations

import html as html_lib
import math
import altair as alt
import pandas as pd
import streamlit as st

from osint import countries, feeds, gate, geo, places, ui, weather_tiles
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
pick_from = ["— none —"] + [f"{r.iso2} — {r['name']}" for _, r in ranked.iterrows()]
chosen = f3.selectbox("Or pick a country", pick_from, index=0,
                      help="The same as clicking it on the globe.")

# ─── The layers laid over the countries ───────────────────────────────────────
# Our own stories go on as one dot per city; the rest are public feeds that need no key.
# Colour and size are decided here, so the globe only has to draw what it is handed.
LAYERS = {"News by city": "news", "Earthquakes": "quakes",
          "Disaster alerts": "gdacs", "Wildfires & storms": "eonet",
          "Clouds": "clouds", "Rain radar": "rain", "Wind": "wind", "Day & night": "sun"}
# Day & night is off to begin with: it is the one layer that makes half the map harder to read.
picked_layers = st.multiselect("Layers", list(LAYERS), default=[n for n in LAYERS if n not in ("Day & night", "Clouds", "Rain radar", "Wind")],
                               help="Our own collection, plus USGS, GDACS (UN/EC) and NASA EONET.")
keys = {LAYERS[name] for name in picked_layers}

# One hue per layer, far enough apart to read on a dark globe. Our own city dots keep the
# sentiment ramp (cyan → violet → red), so the feeds avoid that stretch of the spectrum.
FEED_STYLE = {
    "quake": ("Earthquake · USGS", "255,224,102"),        # pale yellow
    "alert": ("Disaster alert · GDACS", "238,244,255"),   # white
    "fire":  ("Wildfire · NASA EONET", "255,159,28"),     # orange, clear of our negative red
    "storm": ("Storm · NASA EONET", "77,166,255"),        # blue
    "ice":   ("Sea ice · NASA EONET", "159,232,255"),     # pale blue
}

markers, counted, feed_notes, pins = [], {}, [], []

if "news" in keys:
    # One dot per city, not per story: a hundred pins on Delhi says nothing a single sized dot
    # does not say better.
    pins[:] = places.locate(tuple(df[["id", "title", "summary", "country", "sentiment",
                                   "severity", "source", "url", "ts"]]
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
                "layer": "City news · size = items", "swatch": "rgba(94,230,255,0.95)",
                "cc": c["country"],
                "detail": f"{int(c['items'])} items · {share:.0%} negative<br>{html_lib.escape(str(c['headline'])[:90])}",
                "ts": "", "pick": f"city:{c['city']}|{c['country']}"})
        counted["Cities"] = len(grouped)

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
            "layer": layer_name, "swatch": f"rgba({rgb},0.95)",
            # the feeds give coordinates, not countries: the outlines answer that
            "cc": geo.country_of(e["lat"], e["lng"]) or "",
            "detail": html_lib.escape(e["detail"])[:120], "ts": e["ts"],
            "url": e.get("url", ""), "level": e["level"],
            "pick": f"event:{e['kind']}|{e['lat']}|{e['lng']}"})

sun = feeds.subsolar_point() if "sun" in keys else None

# The weather the globe is wrapped in: a cloud mosaic, and the radar loop over it. Each step is
# composed into one texture server-side — the globe has a single texture slot to give it.
weather_frames: list[dict] = []
if {"clouds", "rain", "wind"} & keys:
    with st.spinner("Fetching the weather…"):
        weather_frames, weather_note = weather_tiles.frames(
            count=6, with_clouds="clouds" in keys, with_rain="rain" in keys,
            with_wind="wind" in keys)
    if weather_note:
        feed_notes.append(weather_note)
    if weather_frames:
        counted["Weather steps"] = len(weather_frames)

# ─── How the world is drawn ───────────────────────────────────────────────────
# The 3D/2D and MAP/SAT buttons live inside the map, where the eye already is. They switch
# instantly in the browser and tell us afterwards, so the choice survives a rerun — and so the
# satellite imagery is only fetched once somebody actually asks for it.
view = st.session_state.get("globe_view", "3d")
surface = st.session_state.get("globe_surface", "map")

basemap = None
if surface == "sat" and not weather_frames:
    with st.spinner("Fetching the satellite imagery…"):
        still, still_note = weather_tiles.frames(count=1, with_clouds=True, with_rain=False,
                                                 with_wind=False)
    if still:
        basemap = still[0]["url"]
    elif still_note:
        feed_notes.append(still_note)

# ─── Globe + the country panel ────────────────────────────────────────────────
left, right = st.columns([1.9, 1])

with left.container(border=True):
    st.markdown('<div class="axn-head"><span class="axn-label">Where the news is</span>'
                f'<span class="axn-label right">{len(located):,} located · {range_name.lower()}</span></div>',
                unsafe_allow_html=True)
    clicked = globe(ranked[["iso2", "name", "total", "negative", "share"]].to_dict("records"),
                    selected=st.session_state.get("globe_pick"), height=760,
                    markers=markers, sun=sun, dim="news" in keys,
                    weather=weather_frames,
                    view=view, surface=surface, basemap=basemap)
    if counted:
        st.caption(" · ".join(f"{name}: {n:,}" for name, n in counted.items())
                   + " — cities from GeoNames; events from USGS, GDACS (UN/EC) and NASA EONET.")
    for note in feed_notes:
        st.caption(f"⚠ {note}")

# The map speaks back in three ways: a place was picked, or one of its own buttons was pressed
if clicked and str(clicked).startswith(("view:", "surface:")):
    kind, _, value = str(clicked).partition(":")
    key = "globe_view" if kind == "view" else "globe_surface"
    if st.session_state.get(key) != value:
        st.session_state[key] = value
        st.rerun()
    clicked = None

# A click on the globe wins; the selectbox is the way in without a mouse
if chosen != "— none —":
    st.session_state["globe_pick"] = chosen.split(" — ")[0]
elif clicked:
    st.session_state["globe_pick"] = clicked
picked = st.session_state.get("globe_pick")


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
            f'<div class="head">{f"<a href=\'{link}\' target=\'_blank\'>{head}</a>" if link else head}</div></div>')
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


with right.container(border=True):
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
        st.markdown(f'<div class="axn-head"><span class="axn-label">{html_lib.escape(city_name)}</span>'
                    f'<span class="axn-label right">{html_lib.escape(city_cc)}</span></div>',
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
        st.markdown('<div class="axn-head"><span class="axn-label">Country</span></div>'
                    '<p style="color:#8a919c">Click a city dot for that city, or a country for the '
                    'whole of it — the items, what they are about and the latest headlines land here.</p>',
                    unsafe_allow_html=True)
    else:
        one = located[located["country"] == picked]
        name = countries.NAMES.get(picked, picked)
        st.markdown(f'<div class="axn-head"><span class="axn-label">{html_lib.escape(name)}</span>'
                    f'<span class="axn-label right">{picked}</span></div>', unsafe_allow_html=True)
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
