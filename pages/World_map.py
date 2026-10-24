"""The globe: where the news came from, and what one country is saying.

Spin it, hover a country for its totals, click one to read it. The country outlines live in
components/globe/countries.geojson — generated once from Natural Earth, so nothing is fetched
from anyone else's CDN at render time.
"""
from __future__ import annotations

import html as html_lib
import altair as alt
import pandas as pd
import streamlit as st

from osint import countries, gate, ui
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
f1, f2, f3 = st.columns([1, 1.4, 1])
range_name = f1.selectbox("Time range", list(RANGES), index=1)
hours = RANGES[range_name]
all_types = sorted(items["source_type"].unique())
types = f2.multiselect("Source type", all_types, default=all_types)

now = pd.Timestamp.now(tz=TZ).tz_localize(None)
df = items[(items["ts"] >= now - pd.Timedelta(hours=hours)) & items["source_type"].isin(types)]
if df.empty:
    st.warning("Nothing in this range. Try a longer one.")
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

# ─── Globe + the country panel ────────────────────────────────────────────────
left, right = st.columns([1.9, 1])

with left.container(border=True):
    st.markdown('<div class="axn-head"><span class="axn-label">Where the news is</span>'
                f'<span class="axn-label right">{len(located):,} located · {range_name.lower()}</span></div>',
                unsafe_allow_html=True)
    clicked = globe(ranked[["iso2", "name", "total", "negative", "share"]].to_dict("records"),
                    selected=st.session_state.get("globe_pick"), height=760)

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


with right.container(border=True):
    if not picked:
        st.markdown('<div class="axn-head"><span class="axn-label">Country</span></div>'
                    '<p style="color:#8a919c">Click a country on the globe — its items, what they '
                    'are about and the latest headlines appear here.</p>', unsafe_allow_html=True)
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
