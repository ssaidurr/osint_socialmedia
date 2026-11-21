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

from osint import (correlate, countries, feeds, forecast, gate, geo, hierarchy,
                   layers as static_layers, live, panels, shapes, themes, places,
                   weather_tiles)
from osint.config import TICKETS_DIR, load_config, to_local
from osint.globe import drawable_countries, globe
from osint import ui
from osint.ui import (BRAND, HEAT, MUTED, NEG, NEU, POS, RANGES, REMOTE_REPO, SENTIMENTS,
                      data_version, fetch_remote, load)

SENTIMENT_SCALE = alt.Scale(domain=SENTIMENTS, range=[NEG, NEU, POS])
CUSTOM = "Custom range…"

st.set_page_config(page_title="News Monitor", page_icon="📡", layout="wide")
cfg = load_config()
TZ = cfg.get("timezone", "Asia/Dhaka")
alert_cfg = cfg["alert"]





st.markdown(BRAND, unsafe_allow_html=True)

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
# The console bar. What this is, how much it holds, and whether it is still breathing — one
# row, directly under the mark. The beating dot says live at a glance and goes amber when the
# monitor has not reported in a while, which is a thing you want to notice before you start
# reading numbers that stopped moving two days ago.
last_seen = pd.to_datetime(items["collected_at"].max(), utc=True, format="ISO8601")
hours_since = (pd.Timestamp.now(tz="UTC") - last_seen).total_seconds() / 3600
st.markdown(
    '<div class="axn-topbar">'
    '<span class="t-left">'
    '<svg width="20" height="20" viewBox="0 0 32 32" fill="none" aria-hidden="true">'
    '<circle cx="16" cy="16" r="14.5" stroke="#eceef1" stroke-opacity="0.35"></circle>'
    '<path d="M6 22 L16 6 L26 22" stroke="#eceef1" stroke-width="2" stroke-linejoin="round"></path>'
    '<path d="M10 26 L22 14" stroke="#5ee6ff" stroke-width="2" stroke-linecap="round"></path>'
    '<circle cx="16" cy="16" r="2.2" fill="#5ee6ff"></circle></svg>'
    '<span class="t-title">News Monitor<b>AXN Pulse</b></span></span>'
    '<span class="t-right">'
    f'<span class="t-stat">items<em>{len(items):,}</em></span>'
    f'<span class="t-stat">countries<em>{items["country"].nunique():,}</em></span>'
    f'<span class="t-stat">sources<em>{items["source"].nunique():,}</em></span>'
    f'<span class="live"><span class="live-dot{"" if hours_since < 2 else " stale"}"></span>'
    f'{to_local(items["collected_at"].max(), cfg)}'
    f'{"" if hours_since < 2 else f" · {hours_since:.0f}h ago"}</span>'
    '</span></div>',
    unsafe_allow_html=True,
)
gate.sidebar_note(demo_user)

# ─── Which view is on ─────────────────────────────────────────────────────────
# The page used to be every section at once, about fifteen of them, stacked down a scroll
# roughly a kilometre long. Nothing was wrong with any one of them; it was the pile that was
# unreadable, because a reader had no way to tell where they were or what was below.
#
# Five views, one at a time. Two things follow from that and both matter more than the look:
# every panel in a view can be given a fixed height, so their tops and bottoms line up into a
# grid instead of a ragged column — and only the chosen view runs, so the slow feeds behind
# the map are not paid for by someone who came to read the tables.
VIEWS = ["Overview", "Map", "Analysis", "Correlation", "Coefficient", "Root cause",
         "Tracking", "Benchmark", "SITREP", "Events", "Tickets"]
page = st.segmented_control("View", VIEWS, default="Overview", key="view",
                            label_visibility="collapsed") or "Overview"
ui.motion()

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
range_name = f1.selectbox("Time range", list(RANGES) + [CUSTOM], index=max(1, default_range))
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
# Search, and the two ways an item is classified. They sit on their own row rather than
# squeezing a seventh control into the one above, where every box would be too narrow to
# read the chips inside it.
s1, s2, s3 = st.columns([2.3, 1.3, 0.9])
search = s1.text_input("Search", placeholder="keywords in the headline, text or summary — e.g. election, flood")
category_options = sorted(items["category_label"].dropna().unique())
picked_categories = s2.multiselect(
    "Event category", category_options, disabled=not category_options,
    help="What the analyzer decided the item is about. It is only set on negative items, so "
         "choosing one here also drops everything neutral and positive.")
event_type = s3.selectbox(
    "Event type", ("All", "Negative", "Neutral", "Positive"),
    help="The analyzer's reading of the item's tone.")

now = pd.Timestamp.now(tz=TZ).tz_localize(None)

# ─── The window ───────────────────────────────────────────────────────────────
# Every preset ends at the present, which is what a monitor is usually asked for. A named
# window cannot ask "what did the week of the election look like", so there is also a pair of
# dates, and from here down the page knows only `window_end` and `span` — not whether a human
# picked them from a list or a calendar.
if range_name == CUSTOM:
    oldest, latest = items["ts"].min().date(), items["ts"].max().date()
    d1, d2, _rest = st.columns([1, 1, 3])
    opens = max(oldest, latest - pd.Timedelta(days=6).to_pytimedelta())
    first = d1.date_input("From", value=opens, min_value=oldest, max_value=latest)
    last = d2.date_input("To", value=latest, min_value=oldest, max_value=latest)
    if first > last:                      # a reversed pair is a slip, not an empty window
        first, last = last, first
    # `last` is a day, and a day means all of it, so the window closes at the following midnight.
    window_end = pd.Timestamp(last) + pd.Timedelta(days=1)
    span = window_end - pd.Timestamp(first)
    hours = max(1, int(span.total_seconds() // 3600))
    range_name = f"{first:%d %b} to {last:%d %b}"
else:
    hours = RANGES[range_name]
    span = pd.Timedelta(hours=hours)
    window_end = now

def blob_of(frame: pd.DataFrame) -> pd.Series:
    """Everything in an item that the search looks through, as one lowercase string."""
    return (frame["title"].fillna("") + " " + frame["text"].fillna("") + " "
            + frame["summary"].fillna("")).str.lower()


def narrow(frame: pd.DataFrame) -> pd.DataFrame:
    """The filters above, applied to any slice of time rather than only to the current one.

    Pulled out of the one place it used to live so that the same question can be asked of last
    week: "how many of *these* items were there seven days ago?" is only worth answering if the
    word "these" means the same thing in both windows.
    """
    out = frame[frame["source_type"].isin(types)]
    if picked_countries:
        out = out[out["country"].isin(picked_countries)]
    if picked_categories:
        out = out[out["category_label"].isin(picked_categories)]
    if event_type != "All":
        out = out[out["sentiment"] == event_type.lower()]
    if search.strip():
        # Every word must appear somewhere in the item, in any order
        haystack = blob_of(out)
        mask = pd.Series(True, index=out.index)
        for word in search.lower().split():
            mask &= haystack.str.contains(word, regex=False)
        out = out[mask]
    return out


def since(start: pd.Timestamp, end: pd.Timestamp | None = None) -> pd.DataFrame:
    frame = items[items["ts"] >= start]
    return narrow(frame if end is None else frame[frame["ts"] < end])


# How finely the time axes are cut. Every view that draws time uses the same bucket, so two
# charts seen one after the other are always on the same footing.
freq = "h" if hours <= 72 else "D"
time_fmt = "%d %b %H:%M" if freq == "h" else "%d %b"
df = since(window_end - span, window_end)
neg = df[df["sentiment"] == "negative"]
if df.empty:
    message = "Nothing matches these filters. Try a longer time range, or clear the country/search filter."
    if search.strip():
        # A typo shouldn't look like "no data" — offer the closest words that do appear in the
        # window the search was run over, filters and all but the words themselves.
        loose = items[(items["ts"] >= window_end - span) & (items["ts"] < window_end)]
        loose = loose[loose["source_type"].isin(types)]
        if picked_countries:
            loose = loose[loose["country"].isin(picked_countries)]
        vocabulary = set()
        for blob in blob_of(loose).head(800):
            vocabulary.update(w for w in re.findall(r"[\w\u0980-\u09FF]{4,}", blob))
        hints = []
        for word in search.lower().split():
            hints += difflib.get_close_matches(word, vocabulary, n=3, cutoff=0.75)
        if hints:
            message += "  Did you mean: " + ", ".join(list(dict.fromkeys(hints))[:5]) + "?"
    st.warning(message)
    st.stop()

# ─── Alert status (same rule as the ticket check) ─────────────────────────────
# Worked out whatever view is on, because the Analysis view draws this same threshold as a
# rule across its chart — one number, computed once, so the line and the banner can never
# disagree about where the bar is.
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

# ─── The same window, seven days ago ──────────────────────────────────────────
# The count is the one number here that means nothing on its own: four thousand items is busy
# or quiet depending entirely on what four thousand usually is. So it carries the same window
# from a week ago beside it — same length, same filters, same source types — and says how much
# it has moved. A week rather than yesterday, because the news week has a shape: Sunday is
# quiet everywhere, and comparing a Monday with a Sunday reports a surge that is nothing but
# the calendar.
#
# Worked out for every view: the Overview puts it under the count and on the dials, and the
# Analysis view draws last week as the second polygon on its radar.
week_ago = window_end - pd.Timedelta(days=7)
prior = since(week_ago - span, week_ago)
prior_neg = prior[prior["sentiment"] == "negative"]
# Only if the monitor was actually running then. An empty week-ago window is "we were not
# collecting", which would read as a 100% rise, and that is a lie told with real arithmetic.
#
# Measured on collection rather than on `ts`. `ts` is when a story was published, and one
# backfilled archive piece carries a 2013 date, so a guard built on it reports a full year of
# history on the monitor's first afternoon — and then divides this window by the four stray
# items that happen to carry an old publication date. Collection is the only column that says
# where the record really begins.
record_start = items["collected"].min()
comparable = len(prior) > 0 and record_start <= week_ago - span
movement = (len(df) - len(prior)) / len(prior) if comparable else None

if page == "Overview":
    # ─── The first screen ─────────────────────────────────────────────────────────
    # Numbers, then status, then the two things worth reading side by side. The brief and the
    # dials are given the same fixed height so their tops and bottoms line up: a row of panels
    # that each stop wherever their content happens to stop is the single thing that made the
    # old page look thrown together, and a height is all it takes to fix.
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.container(border=True).metric(
        "Events", f"{len(df):,}",
        delta=f"{movement:+.0%} vs last week" if movement is not None else None,
        help=f"{len(prior):,} in the same window seven days ago" if comparable else
             "No week-ago window to compare against yet — the monitor's record does not reach back that far.")
    k2.container(border=True).metric(
        "Locations tracked", f"{df['country'].nunique():,}",
        help="Distinct countries the analyzer identified in this window. An item it could not "
             "place counts towards no country at all.")
    k3.container(border=True).metric("Negative share", f"{len(neg) / len(df):.0%}", help=f"{len(neg)} negative items")
    k4.container(border=True).metric("Avg severity (negative)", f"{neg['severity'].mean():.1f} / 5" if len(neg) else "—")
    k5.container(border=True).metric(
        "Tickets in range",
        int(((tickets["created"] >= window_end - span) & (tickets["created"] < window_end)).sum())
        if len(tickets) else 0)

    # ─── The four dials ───────────────────────────────────────────────────────────
    # One per standing concern, rather than one per category: eleven dials would be a second,
    # worse version of the bar chart further down. Each shows two numbers because either alone
    # misleads — the count says how loud a category is and nothing about how bad, the average
    # severity says how bad and nothing about how much of it there is, and a single vicious item
    # would peg a dial that only read severity.
    DIALS = (("security", "Terrorism & security"), ("crime", "Crime & law"),
             ("politics_unrest", "Political unrest"), ("disaster", "Crisis & disaster"))
    dials = []
    for key, title in DIALS:
        here = neg[neg["category"] == key]
        before = len(prior_neg[prior_neg["category"] == key])
        shift = (len(here) - before) / before if comparable and before else None
        dials.append(shapes.gauge(
            title, here["severity"].mean() if len(here) else 0.0, 5,
            count=len(here), colour=NEG if len(here) else MUTED,
            note=f"{shift:+.0%} vs last week" if shift is not None else ""))

    if len(win) >= alert_cfg["min_items"] and win_share >= bar:
        st.error(f"Alert level: {status}", icon="🚨")
    else:
        st.success(f"Normal: {status}", icon="✅")

    # ─── Two rows of three ────────────────────────────────────────────────────────
    # Every panel in a row is given the same height, so the row reads as a row: tops aligned,
    # bottoms aligned, nothing stopping halfway down beside a neighbour that runs on. Panels
    # whose content outgrows the box scroll inside it rather than stretching the grid, which
    # is what keeps the whole view on one screen whatever the news is doing.
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

    # The lower row is taller than it looks like it needs to be: two of its three panels are
    # drawings rather than lists, and both were being squeezed — the sparkline read as a flat
    # line with a third of its panel empty under it, and the donut's legend was landing on top
    # of the ring instead of under it.
    TOP_H, LOW_H = 452, 404
    brief_col, stream_col, dials_col = st.columns([1.5, 1.25, 1])
    with brief_col.container(border=True, height=TOP_H):
        # ─── The brief and the odds, two tabs of one box ──────────────────────────────
        # Two readings of the same moment: what has happened, written by the analyzer, and what people
        # are betting will happen next. They answer each other, so they share one box at the top of the
        # page rather than sitting a screen apart — the prediction panel used to be twenty panels down,
        # where nobody scrolled to find it.
        #
        # The odds tab follows whatever country is selected, but it is drawn *above* the globe, so it
        # reads the selection left over from the last run rather than this run's answer. That is the
        # only honest thing available this early in the page, and it costs nothing: picking a country
        # reruns the page, so the tab is right by the time anyone reads it.
        def _early_country() -> str | None:
            """Whichever country the globe or the picker had selected when the page last drew."""
            pick = st.session_state.get("globe_pick")
            if not isinstance(pick, str):
                return None
            if pick.startswith("city:"):
                return pick[5:].partition("|")[2] or None
            return pick if len(pick) == 2 else None


        with st.container(border=True):
            brief_tab, odds_tab = st.tabs(["📰 AI brief", "🎲 Predictions"])
            with brief_tab:
                st.markdown(f"#### 📰 At a glance — {range_name.lower()}")
                brief = briefs.get(hours)
                if brief:
                    st.markdown(brief["text"])
                    st.caption(f"Written {to_local(brief['created_at'], cfg)}")
                else:
                    st.caption("No brief for this range yet — one is written on the next monitor run.")
            with odds_tab:
                # The filters live here rather than inside the panel, so that the same panel can also
                # be put under the globe without Streamlit refusing the second copy for reusing a
                # widget key. The panel reads what these two boxes leave in session state.
                from osint import predict as prediction

                subjects, markets_in = prediction.choices()
                counts = dict(subjects)
                o1, o2 = st.columns(2)
                o1.selectbox(
                    "Subject", [""] + [name for name, _ in subjects], key=panels.FILTER_CATEGORY,
                    format_func=lambda name: f"{name} ({counts[name]})" if name else "All subjects",
                    help="Polymarket's own tag for the market — not our guess at what it is about.")
                o2.selectbox(
                    "Country", [panels.FOLLOW, panels.WORLD] + list(markets_in), key=panels.FILTER_COUNTRY,
                    format_func=lambda code: {panels.FOLLOW: "↪ Follow the globe",
                                              panels.WORLD: "Worldwide"}.get(code, countries.label(code)),
                    help="Every country Polymarket keeps a tag for. A country with nothing open today "
                         "says so rather than showing someone else's questions.")
                # The panel resolves the country itself; the scope only matters while the box above is
                # following the globe. `news` goes unused by it, and is passed because a Scope is a
                # Scope.
                early_cc = _early_country()
                panels.PANELS["predict"].draw(panels.Scope(
                    cc=early_cc,
                    name=countries.NAMES.get(early_cc, early_cc) if early_cc else "Worldwide",
                    news=items, world_news=items, range_name=range_name,
                ))
    with stream_col.container(border=True, height=TOP_H):
        dot = f'<span class="live-dot{"" if hours_since < 2 else " stale"}"></span>'
        st.markdown(panel_head("Live event stream", f"{len(df):,} items · {range_name.lower()}", dot)
                    + stream_html(df), unsafe_allow_html=True)
    with dials_col.container(border=True, height=TOP_H):
        st.markdown(panels.head("Standing concerns", "vs last week"), unsafe_allow_html=True)
        st.markdown(f'<div class="axn-gauges pair">{"".join(dials)}</div>', unsafe_allow_html=True)

    index_col, narrative_col, split_col = st.columns(3)
    with index_col.container(border=True, height=LOW_H):
        # Index = share positive minus share negative, so it runs from -1 (all bad news) to +1
        positives = int((df["sentiment"] == "positive").sum())
        index = (positives - len(neg)) / len(df)
        st.markdown(
            panel_head("Global sentiment index", f"{len(df):,} articles")
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
        ).properties(height=272)
        last = alt.Chart(spark.tail(1)).mark_point(filled=True, size=90, color=POS, opacity=1).encode(
            x="bucket:T", y="index:Q")
        st.altair_chart(area + last, width="stretch")
    with narrative_col.container(border=True, height=LOW_H):
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
    with split_col.container(border=True, height=LOW_H):
        st.subheader("Top 5 events by type")
        if neg.empty:
            st.caption("No negative items in this range.")
        else:
            counts = neg["category_label"].value_counts()
            # Five slices and a sixth for the rest. A pie of eleven categories is a colour-matching
            # exercise; five is the number a reader can hold, and "Other" keeps the total honest
            # rather than quietly dropping the tail.
            top = counts.head(5)
            rest = int(counts[5:].sum())
            slices = pd.DataFrame({
                "type": list(top.index) + (["Other types"] if rest else []),
                "count": list(top.values) + ([rest] if rest else []),
            })
            slices["share"] = slices["count"] / slices["count"].sum()
            # Vega's own legend is laid inside the same box as the arc, so at this size it was
            # printed across the ring. The key is drawn under the chart instead, where it also
            # has room to carry each slice's share.
            SLICE_COLOURS = ["#f87171", "#fb923c", "#8b7cff", "#5ee6ff", "#46d39a", "#4a515a"]
            st.altair_chart(
                alt.Chart(slices).mark_arc(innerRadius=54, outerRadius=104, cornerRadius=3,
                                           stroke="#0f1215", strokeWidth=2).encode(
                    theta=alt.Theta("count:Q", stack=True),
                    color=alt.Color("type:N", title=None, sort=list(slices["type"]),
                                    scale=alt.Scale(range=SLICE_COLOURS), legend=None),
                    order=alt.Order("count:Q", sort="descending"),
                    tooltip=[alt.Tooltip("type:N", title="Type"), alt.Tooltip("count:Q", title="Items"),
                             alt.Tooltip("share:Q", title="Share", format=".1%")],
                ).properties(height=232),
                width="stretch",
            )
            st.markdown(
                '<div class="axn-legend dots">' + "".join(
                    f'<span><i style="background:{SLICE_COLOURS[i % len(SLICE_COLOURS)]}"></i>'
                    f'{html_lib.escape(str(row.type))} {row.share:.0%}</span>'
                    for i, row in enumerate(slices.itertuples())) + "</div>",
                unsafe_allow_html=True)
            st.caption(f"{int(slices['count'].sum()):,} negative items.")


if page == "Map":
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

    # What a dot's size is allowed to mean. Read here rather than where the control is drawn,
    # because the control sits inside the map's own panel, hundreds of lines below this, and
    # the dots have to be built before the map can be handed them. Changing it reruns the
    # page, and the next pass reads the new value here.
    #
    # Two modes rather than one number combining them, because the two answer opposite
    # questions. Volume finds the place everything is happening; significance finds the town
    # with one atrocity in it, which volume buries. A product of the two would hide both.
    # Named in words a reader can act on rather than in the words a dashboard would use.
    # "Volume / Significance" is exactly right and told nobody anything: the control has to
    # say what the dot will *do*, because nothing else on the screen can say it for it.
    BUSY, BAD = "How much news", "How serious"
    SIZE_MODES = [BUSY, BAD]
    size_by = st.session_state.get("dot_size") or BUSY
    if size_by not in SIZE_MODES:
        size_by = BUSY
    size_tag = "how serious" if size_by == BAD else "how much news"
    # The key printed on the map. Three circles getting bigger told a reader that size means
    # *something* and left them to guess what; two circles with the window's own smallest and
    # largest value beside them say it outright, in the numbers they are actually looking at.
    # Filled in once the city dots are grouped — until then it has nothing true to say.
    size_key = {"word": size_tag, "low": "", "high": ""}

    def _weight(items: int, peak: int, rating: float, worst: float) -> float:
        """Nought to one, by whichever of the two the reader asked for.

        Both are measured against the loudest and the harshest thing *in this window* rather
        than against an absolute ceiling. Severity out of a fixed 5 put almost every city at
        the floor — most news averages two-point-something, so the map came out flat and the
        switch looked broken. Against the window's own worst, the range is used, and the key
        printed on the map names the two ends so the relative scale cannot mislead.
        """
        if size_by == BAD:
            return min(max(rating, 0.0) / worst, 1.0) if worst > 0 else 0.0
        # Normalised across the window's own smallest and largest, not from zero. A plain
        # log against the peak put a one-item city at 0.13 of the way up and a ten-item city
        # at 0.45, so two thirds of the map sat in the bottom eighth of the size range and
        # the difference between a quiet town and a busy one was a pixel. Anchoring the
        # bottom of the scale at the quietest city spends the whole range on real variation.
        floor, ceiling = math.log10(2), math.log10(max(peak, 1) + 1)
        if ceiling <= floor:
            return 0.0
        return min(max((math.log10(items + 1) - floor) / (ceiling - floor), 0.0), 1.0)

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
                rating=("severity", "mean"),
                lat=("lat", "first"), lng=("lng", "first"), headline=("title", "first"))
            biggest = grouped["items"].max()
            worst_rating = float(grouped["rating"].max() or 0)
            if size_by == BAD:
                # Only cities that actually carry a rating. A city of nothing but neutral news
                # averages 0.0, and "rated 0.0" at the small end of the key reads as "graded,
                # and harmless" when what it means is "never graded at all".
                graded_cities = grouped["rating"].dropna()
                graded_cities = graded_cities[graded_cities > 0]
                if len(graded_cities):
                    size_key["low"] = f"rated {graded_cities.min():.1f}"
                    size_key["high"] = f"rated {graded_cities.max():.1f}"
                else:
                    size_key["low"], size_key["high"] = "nothing", "graded yet"
            else:
                size_key["low"] = f"{int(grouped['items'].min())} item"
                size_key["high"] = f"{int(biggest):,} items"
            # What each city's items are *about*, for the bars in its popup. One pass over the
            # frame rather than a filter per city: two hundred cities each scanning the whole
            # frame is the sort of thing that turns a fast panel into a slow one for no gain.
            spread: dict[tuple, list[tuple[str, int]]] = {}
            if "category" in frame:
                for (city, cc, kind), n in (frame[frame["category"].notna()]
                                            .groupby(["city", "country", "category"])
                                            .size().items()):
                    spread.setdefault((city, cc), []).append((str(kind), int(n)))
            for _, c in grouped.iterrows():
                share = c["negative"] / c["items"]
                rating = 0.0 if pd.isna(c["rating"]) else float(c["rating"])
                rgb = "248,113,113" if share >= 0.6 else "139,124,255" if share >= 0.3 else "94,230,255"
                bars = sorted(spread.get((c["city"], c["country"]), []), key=lambda t: -t[1])[:6]
                # A reading of 0.0 is "nothing here was graded", not "nothing here was bad" —
                # severity is only filed on negative items. The popup says which it is rather
                # than printing a zero that reads as an all-clear.
                markers.append({
                    "lat": float(c["lat"]), "lng": float(c["lng"]),
                    # Radius is in degrees of the globe's surface, and that is the whole
                    # reason the size switch looked broken: at 0.16–0.50 the largest disc
                    # came out about two pixels across, under an icon sprite four pixels
                    # wide, so every dot was the icon and nothing else. These reach about
                    # thirteen pixels at the top end — a halo the glyph sits inside, which
                    # is the first version of this a reader can actually see.
                    "r": round(0.30 + 1.70 * _weight(int(c["items"]), int(biggest), rating,
                                                     worst_rating), 3),
                    "color": f"rgba({rgb},0.9)",
                    "pulse": bool(c["worst"] and c["worst"] >= 4),
                    "title": f"{c['city']}", "kicker": c["country"],
                    "layer": f"City news · dot = {size_tag}",
                    "swatch": SWATCH["News by city"], "icon": "news",
                    "cc": c["country"],
                    "detail": (
                        (f'<span class="sig">{rating:.1f}</span>'
                         f'<span class="cap">avg significance, of 5</span>'
                         if rating else
                         '<span class="sig cool">—</span>'
                         '<span class="cap">nothing graded here</span>')
                        + (shapes.spark([n for _, n in bars]) if len(bars) >= 2 else "")
                        + f'<span class="cap">{int(c["items"])} '
                        + ("item" if c["items"] == 1 else "items") + f' · {share:.0%} negative'
                        + (f' · mostly {html_lib.escape(bars[0][0].lower())}' if bars else "")
                        + "</span>"
                        + f'<span class="cut">{html_lib.escape(str(c["headline"])[:110])}</span>'),
                    # `detail` is the popup's markup now, which is the globe's business alone.
                    # Panels on the Streamlit side of the iframe have none of its styling, so
                    # they read these instead of unpicking HTML for a number.
                    "sized": True,
                    "items": int(c["items"]), "rating": round(rating, 1),
                    "summary": (f'{int(c["items"])} items · {share:.0%} negative'
                                + (f' · avg severity {rating:.1f} / 5' if rating else "")),
                    "ts": "", "pick": f"city:{c['city']}|{c['country']}"})
            counted["News by city"] = len(grouped)

    # Our own stories, split out by what they are about: the same pins, asked a narrower question.
    for key, label, wanted, rgb in (("protests", "Protests", "Political unrest", "255,122,200"),
                                    ("conflict", "Armed conflict", "Security & terrorism", "255,99,71")):
        if key not in keys:
            continue
        subset = [pin for pin in pins if pin.get("category") == wanted]
        if not subset:
            counted[label] = 0
            continue
        # Clustered by city, like the news layer above it. One dot per story stacked twelve
        # deep on Gaza draws twelve identical circles, hides eleven of them, and makes the
        # twelfth no bigger than a town with one — which is the opposite of what a map of
        # where something is concentrated should do.
        clustered = pd.DataFrame(subset).groupby(["city", "country"], as_index=False).agg(
            items=("title", "size"), worst=("severity", "max"), rating=("severity", "mean"),
            lat=("lat", "first"), lng=("lng", "first"), headline=("title", "first"),
            when=("ts", "max"))
        peak = int(clustered["items"].max())
        worst_here = float(clustered["rating"].max() or 0)
        # Dots, not stories, the same as the layer above: the number beside a layer's name in
        # the panel should be the number of things the reader can see and click on it.
        counted[label] = len(clustered)
        for _, c in clustered.iterrows():
            rating = 0.0 if pd.isna(c["rating"]) else float(c["rating"])
            markers.append({
                "lat": float(c["lat"]), "lng": float(c["lng"]),
                "r": round(0.28 + 1.35 * _weight(int(c["items"]), peak, rating, worst_here), 3),
                "color": f"rgba({rgb},0.9)", "pulse": bool(c["worst"] and c["worst"] >= 4),
                "title": html_lib.escape(str(c["city"])), "kicker": str(c["country"]),
                "layer": f"{label} · dot = {size_tag}", "swatch": f"rgba({rgb},0.95)",
                "icon": key, "cc": str(c["country"]),
                "detail": (
                    (f'<span class="sig">{rating:.1f}</span>'
                     f'<span class="cap">avg significance, of 5</span>'
                     if rating else
                     '<span class="sig cool">—</span><span class="cap">nothing graded here</span>')
                    + f'<span class="cap">{int(c["items"])} '
                    + ("item" if c["items"] == 1 else "items") + f' · {label.lower()}</span>'
                    + f'<span class="cut">{html_lib.escape(str(c["headline"])[:110])}</span>'),
                "sized": True,
                "items": int(c["items"]), "rating": round(rating, 1),
                "summary": (f'{int(c["items"])} {"item" if c["items"] == 1 else "items"} · '
                            + label.lower() + (f' · avg severity {rating:.1f} / 5' if rating else "")),
                "ts": str(c["when"]), "pick": f"city:{c['city']}|{c['country']}"})

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
    GLOBE_HEIGHT = PANEL_HEIGHT - 134        # the head row and the source caption take the rest

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
        hl, hr = st.columns([1.5, 1], vertical_alignment="center")
        hl.markdown('<div class="axn-head"><span class="axn-label">Where the news is</span>'
                    f'<span class="axn-label right">{len(located):,} located</span></div>',
                    unsafe_allow_html=True)
        hr.segmented_control(
            "Big dots show", SIZE_MODES, default=BUSY, key="dot_size",
            help="What a dot's size means. By news it is how many items came from that city, "
                 "on a log scale so one enormous city does not flatten the rest. By severity "
                 "it is the average rating the analyzer gave them, which finds the small town "
                 "with one atrocity in it that the busy cities bury.")
        # The control names the choice; this names the consequence, and it is the line that
        # makes the map readable without anyone being told how to read it.
        hr.caption("Big dot = **a lot of news** from that city. A quiet town stays small even "
                   "if what happened there was terrible."
                   if size_by == BUSY else
                   "Big dot = **serious news** there, by the analyzer's 0–5 rating. One grave "
                   "story makes a small town as big as a capital.")
        try:
            clicked = globe(ranked[["iso2", "name", "total", "negative", "share"]].to_dict("records"),
                            selected=picked, height=GLOBE_HEIGHT,
                            markers=markers, sun=sun, dim="news" in keys,
                            weather=weather_frames, paths=paths,
                            view=view, surface=surface, layers=layer_panel, sizing=size_key)
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
                    f'<p style="color:#c6ccd4;margin:.2rem 0 .4rem">'
                    f'{hit.get("summary") or hit["detail"]}</p>'
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
                        count = m.get("items", m["detail"].split(" items")[0])
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
    # Predictions is deliberately absent: it now has a tab of its own at the top of the page, and
    # a panel that draws the same seven questions a second time further down is just repetition.
    # The picker still offers it, for anyone who wants it beside the globe.
    OPENING = ["News · Live intelligence", "News · Threat timeline", "Risk · Instability",
               "Money · News ↔ markets", "Live · Live news TV", "Live · Live webcams",
               "Money · Markets", "Money · Metals & materials",
               "Risk · Live hazards", "News · What it is about",
               "News · Countries by volume"]
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


    # Three to a row, with the way to add one standing in the last slot. A panel that throws
    # takes itself down and says so — every one of these reads someone else's server, and one
    # bad morning at NASA must not blank the page.
    #
    # They share a height now. Letting each card be as tall as its contents, which is what this
    # did before, left the row ragged: a webcam strip beside a two-line market panel, their
    # bottoms nowhere near each other. A fixed height makes the row a row, and a panel with
    # more to say scrolls inside its own box rather than stretching the grid around it.
    PANEL_GRID_HEIGHT = 430
    cells = shown + ["+"]
    for triple in [cells[i:i + 3] for i in range(0, len(cells), 3)]:
        columns = st.columns(3)
        for column, label in zip(columns, triple):
            if label == "+":
                with column.container(border=False):
                    with st.popover("＋ add a panel", use_container_width=True, width=620,
                                    help="Each one answers for the country selected on the "
                                         "globe, or for the whole world."):
                        _picker()
                continue
            chosen = panels.PANELS[PANEL_LABELS[label]]
            scope.panel_key = PANEL_LABELS[label]
            with column.container(border=True, height=PANEL_GRID_HEIGHT):
                try:
                    chosen.draw(scope)
                except Exception as problem:            # noqa: BLE001 — a panel is never fatal
                    st.markdown(panels.head(chosen.title, shut=scope.shut()), unsafe_allow_html=True)
                    st.caption(f"This panel could not be drawn: {type(problem).__name__}.")

if page == "Analysis":
    # ─── Three to a row ───────────────────────────────────────────────────────────
    # Panels in a row share a height, so the row reads as a row. The last pair is the
    # exception and deliberately so: a treemap of a hundred tiles and a heatmap with a time
    # axis both stop being readable at a third of the width, for the same reason the globe
    # does — they need the room to say anything at all.
    ROW_A, ROW_B, ROW_WIDE = 420, 420, 470

    a1, a2, a3 = st.columns(3)
    with a1.container(border=True, height=ROW_A):
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
    with a2.container(border=True, height=ROW_A):
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
    with a3.container(border=True, height=ROW_A):
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

    b1, b2, b3 = st.columns(3)
    with b1.container(border=True, height=ROW_B):
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
    with b2.container(border=True, height=ROW_B):
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
    with b3.container(border=True, height=ROW_B):
        st.subheader("This range against last week")
        # The radar answers a question no other chart here does: not "what is big" but "what has
        # changed shape". Two polygons on one scale — the same window seven days ago drawn under
        # this one — so a category that has doubled shows as a spike rather than as a number the
        # reader has to hold in their head while looking at a second chart.
        if neg.empty:
            st.caption("No negative items in this range.")
        elif not comparable:
            st.caption("No week-ago window to compare against yet — the monitor's record does not "
                       "reach back far enough.")
        else:
            spokes = neg["category_label"].value_counts().head(6).index.tolist()
            before = prior_neg["category_label"].value_counts()
            st.markdown(
                '<div class="axn-radar">' + shapes.radar(
                    spokes,
                    [("Last week", MUTED, [int(before.get(name, 0)) for name in spokes]),
                     ("This range", NEG, [int((neg["category_label"] == name).sum()) for name in spokes])],
                ) + "</div>"
                '<div class="axn-legend">'
                f'<span><i style="background:{NEG}"></i>this range</span>'
                f'<span><i style="background:{MUTED}"></i>same window last week</span>'
                "</div>",
                unsafe_allow_html=True)
            st.caption(f"{len(neg):,} negative items now against {len(prior_neg):,} seven days ago.")

    w1, w2 = st.columns(2)
    with w1.container(border=True, height=ROW_WIDE):
        st.subheader("Volume by type and publisher")
        # The analyzer gives one flat category and no sub-category, so the second level here is
        # the publisher: a block is a type, and the tiles inside it are the outlets carrying
        # that type. That answers "who is driving this" in the same picture as "how much is
        # there", which the two separate bar charts below cannot do.
        TOP_SOURCES = 6
        CANVAS_W, CANVAS_H = 1000.0, 520.0
        groups = neg["category_label"].value_counts()
        outer = shapes.squarify(list(groups.values), CANVAS_W, CANVAS_H)
        tiles, frames = [], []
        shades = ["#f87171", "#fb923c", "#8b7cff", "#5ee6ff", "#46d39a", "#e879a6",
                  "#facc15", "#38bdf8", "#a3e635", "#fb7185", "#4a515a"]
        for rank, (label, total) in enumerate(groups.items()):
            x0, y0, x1, y1 = outer[rank]
            if x1 - x0 < 1 or y1 - y0 < 1:
                continue
            frames.append({"type": label, "x": x0, "x2": x1, "y": y0, "y2": y1, "total": int(total)})
            publishers = neg[neg["category_label"] == label]["source"].value_counts()
            head = publishers.head(TOP_SOURCES)
            tail = int(publishers[TOP_SOURCES:].sum())
            names = list(head.index) + (["other publishers"] if tail else [])
            sizes = list(head.values) + ([tail] if tail else [])
            for (ix0, iy0, ix1, iy1), name, n in zip(
                    shapes.squarify(sizes, x1 - x0, y1 - y0), names, sizes):
                tiles.append({
                    "type": label, "source": name, "count": int(n),
                    "share": n / total,
                    "x": x0 + ix0, "x2": x0 + ix1, "y": y0 + iy0, "y2": y0 + iy1,
                    "shade": shades[rank % len(shades)],
                })
        tile_df, frame_df = pd.DataFrame(tiles), pd.DataFrame(frames)
        axis = alt.Axis(labels=False, ticks=False, domain=False, grid=False, title=None)
        # The layout numbers y downwards, the way a screen does; Vega numbers it upwards. Without
        # the flipped domain the rectangles land mirrored and every label sits at the foot of its
        # block instead of its head, which is how the first version put six names off the canvas.
        down = alt.Scale(domain=[CANVAS_H, 0], nice=False)
        cells = alt.Chart(tile_df).mark_rect(stroke="#0f1215", strokeWidth=1).encode(
            x=alt.X("x:Q", axis=axis, scale=alt.Scale(domain=[0, CANVAS_W], nice=False)),
            x2="x2:Q",
            y=alt.Y("y:Q", axis=axis, scale=down), y2="y2:Q",
            color=alt.Color("shade:N", scale=None, legend=None),
            opacity=alt.Opacity("share:Q", scale=alt.Scale(range=[0.35, 1]), legend=None),
            tooltip=[alt.Tooltip("type:N", title="Type"), alt.Tooltip("source:N", title="Publisher"),
                     alt.Tooltip("count:Q", title="Items"),
                     alt.Tooltip("share:Q", title="Share of type", format=".0%")],
        )
        borders = alt.Chart(frame_df).mark_rect(fill=None, stroke="#0b0d0f", strokeWidth=2).encode(
            x=alt.X("x:Q", axis=axis, scale=alt.Scale(domain=[0, CANVAS_W], nice=False)), x2="x2:Q",
            y=alt.Y("y:Q", axis=axis, scale=down), y2="y2:Q",
        )
        # Only name a block that has room for its name; a label clipped by its own rectangle is
        # worse than none, because it reads as a different word.
        roomy = frame_df[(frame_df["x2"] - frame_df["x"] > 104) & (frame_df["y2"] - frame_df["y"] > 34)]
        # Dark ink, no outline: a label sits on the top-left corner of its block, which is its
        # largest and most opaque tile, so there is always a bright ground under the words.
        names = alt.Chart(roomy).mark_text(
            align="left", baseline="top", dx=8, dy=7, fontSize=11.5, fontWeight=700,
            color="#0b0d0f",
        ).encode(
            x=alt.X("x:Q", axis=axis, scale=alt.Scale(domain=[0, CANVAS_W], nice=False)),
            y=alt.Y("y:Q", axis=axis, scale=down),
            text=alt.Text("type:N"),
        )
        st.altair_chart((cells + borders + names).properties(height=420), width="stretch")
        st.caption("Each block is a type, sized by how many negative items it holds; the tiles "
                   "inside it are the publishers carrying that type. Hover for the numbers.")
    with w2.container(border=True, height=ROW_WIDE):
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

if page == "Correlation":
    # ─── What kind of event drags the rating up ───────────────────────────────────
    # The charts in Analysis answer "how much of each is there". This view answers a different
    # question: given that an item is of a kind, how much harsher is it rated than everything
    # else in the same window? That is a difference of two means, so every number here comes
    # with the interval around it, and nothing is called a finding unless the interval clears
    # zero. The arithmetic lives in osint/correlate.py, away from the drawing.
    impact = correlate.influence(neg)
    CORR_H = 430

    if impact.empty:
        st.info("Nothing in this window carries a severity rating yet, so there is nothing to "
                "compare. Severity is set by the analyzer, and only on negative items.")
    else:
        sure = impact[impact["certain"]]
        k1, k2, k3, k4 = st.columns(4)
        k1.container(border=True).metric(
            "Event codes compared", f"{len(impact):,}",
            help="Every category with at least two rated items in this window.")
        k2.container(border=True).metric(
            "Rated items", f"{int(impact['items'].sum()):,}",
            help="Negative items the analyzer gave a severity to. Nothing else can be scored.")
        k3.container(border=True).metric(
            "Statistically clear", f"{len(sure):,}",
            help="Codes whose 95% interval does not straddle zero — the ones worth quoting.")
        k4.container(border=True).metric(
            "Strongest impact",
            f"{impact.iloc[0]['impact']:+.2f}" if len(impact) else "—",
            help=f"{impact.iloc[0]['code']} against every other category." if len(impact) else "")

        c1, c2, c3 = st.columns(3)

        # ── Key influencers ──────────────────────────────────────────────────────
        with c1.container(border=True, height=CORR_H):
            st.markdown(panels.head("Key influencers", "impact on rating",
                                    tip="Each code's mean severity minus the mean severity of "
                                        "every other code in the window. Bars are drawn from "
                                        "zero: right is harsher than the rest, left is milder. "
                                        "A hollow bar is one the interval cannot separate from "
                                        "zero."), unsafe_allow_html=True)
            reach = max(impact["impact"].abs().max(), 0.01)
            rows_html = []
            for row in impact.itertuples():
                width = abs(row.impact) / reach * 50          # half the track each way
                left = 50 if row.impact >= 0 else 50 - width
                tone = NEG if row.impact >= 0 else POS
                rows_html.append(
                    f'<div class="infl" title="{html_lib.escape(row.code)} — {row.items:,} items, '
                    f'{row.rating:.2f} against {row.others:.2f}">'
                    f'<span class="who">{html_lib.escape(row.code)}</span>'
                    f'<span class="axis"><i class="zero"></i>'
                    f'<i class="bar" style="left:{left:.1f}%;width:{width:.1f}%;'
                    f'background:{tone};opacity:{"1" if row.certain else ".32"}"></i></span>'
                    f'<span class="score">{row.impact:+.2f}</span></div>')
            st.markdown('<div class="axn-infl">' + "".join(rows_html) + "</div>",
                        unsafe_allow_html=True)

        # ── Correlation chart ────────────────────────────────────────────────────
        with c2.container(border=True, height=CORR_H):
            st.subheader("Average rating by event code")
            baseline = float(neg["severity"].mean())
            chart = alt.Chart(impact).encode(
                y=alt.Y("code:N", sort="-x", title=None),
                x=alt.X("rating:Q", title="Average severity",
                        scale=alt.Scale(domain=[0, 5], nice=False)),
                tooltip=[alt.Tooltip("code:N", title="Event code"),
                         alt.Tooltip("items:Q", title="Rated items"),
                         alt.Tooltip("rating:Q", title="Average", format=".2f"),
                         alt.Tooltip("impact:Q", title="Against the rest", format="+.2f")],
            )
            mark = chart.mark_bar(cornerRadiusEnd=4, height={"band": 0.72}).encode(
                color=alt.condition(alt.datum.rating >= baseline, alt.value(NEG), alt.value(MUTED)))
            label = chart.mark_text(align="left", dx=4, color=MUTED, fontSize=10).encode(
                text=alt.Text("rating:Q", format=".2f"))
            rule = alt.Chart(pd.DataFrame({"x": [baseline]})).mark_rule(
                color="#8b7cff", strokeDash=[4, 3]).encode(x="x:Q")
            st.altair_chart((mark + label + rule).properties(height=max(200, 26 * len(impact))),
                            width="stretch")
            st.caption(f"Violet line is the window's own average, {baseline:.2f} / 5.")

        # ── Contextual insights ──────────────────────────────────────────────────
        with c3.container(border=True, height=CORR_H):
            st.markdown(panels.head("What this says", range_name.lower()), unsafe_allow_html=True)
            for line in correlate.insight(impact, window=range_name.lower()):
                st.markdown(f"- {line}")
            st.caption("95% intervals, normal approximation. A difference whose interval "
                       "touches zero is not reported as a finding.")

    # ── Impact scores, in full ───────────────────────────────────────────────────
    if not impact.empty:
        with st.container(border=True):
            st.subheader("Impact scores")
            table = impact.assign(
                interval=lambda d: d.apply(lambda r: f"{r['low']:+.2f} to {r['high']:+.2f}", axis=1),
                verdict=lambda d: d["certain"].map({True: "clear", False: "not separable"}),
            )
            st.dataframe(
                table[["code", "items", "share", "rating", "others", "impact", "interval", "verdict"]],
                hide_index=True, width="stretch",
                column_config={
                    "code": "Event code",
                    "items": st.column_config.NumberColumn("Rated items"),
                    "share": st.column_config.ProgressColumn("Share of window", min_value=0,
                                                             max_value=1, format="percent"),
                    "rating": st.column_config.NumberColumn("Average rating", format="%.2f"),
                    "others": st.column_config.NumberColumn("Every other code", format="%.2f"),
                    "impact": st.column_config.NumberColumn(
                        "Impact", format="%+.2f",
                        help="Average rating minus the average of every other code."),
                    "interval": st.column_config.TextColumn(
                        "95% interval", help="Where the true difference plausibly sits."),
                    "verdict": "Verdict",
                })
            st.caption("Impact is a difference of means, not a cause. A code can be rated "
                       "harshly because the events really are worse, or because the outlets "
                       "that cover it write harder — this cannot tell the two apart.")

if page == "Coefficient":
    # ─── Do two kinds of event move together ──────────────────────────────────────
    # The Correlation view next door asks whether one kind of event is rated harsher than the
    # rest. This asks something the impact score cannot: whether two kinds of event rise and
    # fall *at the same time*. They are independent questions — two subjects can move in
    # perfect step while one is rated twice as badly as the other — and running them together
    # in one view produced a panel that answered neither.
    #
    # What is correlated is two count series: how many items of each kind landed in each hour
    # (or each day, on a long range) of the window. A correlation needs paired observations
    # and single news items are paired with nothing.
    PAIR_COUNTS, PAIR_SHARES = "Counts per hour", "Share of the hour"
    if freq == "D":
        PAIR_COUNTS, PAIR_SHARES = "Counts per day", "Share of the day"

    coded = neg[neg["category_label"].notna()]
    ranked_codes = coded["category_label"].value_counts()
    choices = [name for name, n in ranked_codes.items() if n >= 10]
    if len(choices) < 2:
        st.info("Two event types with at least ten items each are needed before anything can "
                "be correlated, and this range does not have them. Try a longer range.")
    else:
        x1, x2, x3 = st.columns([1.3, 1.3, 1.4])
        var_x = x1.selectbox("Independent variable (X)", choices, index=0,
                             help="The event type on the horizontal axis. Which of the two is "
                                  "called independent makes no difference to the coefficient — "
                                  "it is symmetric — but it does set the axes and the fitted line.")
        var_y = x2.selectbox("Dependent variable (Y)", choices,
                             index=1 if len(choices) > 1 else 0,
                             help="The event type on the vertical axis.")
        measure = x3.radio("Measure", (PAIR_COUNTS, PAIR_SHARES), horizontal=True,
                           help="Counts correlate the raw number of items in each bucket. "
                                "Share divides by everything collected in that bucket first, "
                                "which removes the one thing that otherwise drives every pair: "
                                "a busy hour is busy for every subject at once.")

        start = window_end - span
        whole = correlate.buckets(coded, freq=freq, start=start, end=window_end)
        raw_x = correlate.buckets(coded[coded["category_label"] == var_x],
                                  freq=freq, start=start, end=window_end)
        raw_y = correlate.buckets(coded[coded["category_label"] == var_y],
                                  freq=freq, start=start, end=window_end)
        if measure == PAIR_SHARES:
            series_x = correlate.as_share(raw_x, whole)
            series_y = correlate.as_share(raw_y, whole)
            axis_fmt, axis_unit = ".0%", "share of the bucket"
        else:
            series_x, series_y = raw_x, raw_y
            axis_fmt, axis_unit = "d", "items in the bucket"
        fit = correlate.pearson(series_x, series_y)
        same = var_x == var_y
        r = fit["r"]
        strength = ("no usable reading" if r != r else
                    "very strong" if abs(r) >= 0.8 else "strong" if abs(r) >= 0.6 else
                    "moderate" if abs(r) >= 0.4 else "weak" if abs(r) >= 0.2 else "almost none")

        best = correlate.strongest(coded, freq=freq, start=start, end=window_end)

        m1, m2, m3, m4 = st.columns(4)
        m1.container(border=True).metric(
            "Coefficient r", "—" if r != r else f"{r:+.2f}",
            help="Pearson's r between the two series. +1 is lockstep, −1 is exact opposition, "
                 "0 is no linear relationship at all.")
        m2.container(border=True).metric(
            "95% interval",
            "—" if fit["low"] is None else f"{fit['low']:+.2f} to {fit['high']:+.2f}",
            help="Fisher's z transform. An interval that straddles zero is a relationship we "
                 "cannot tell from chance.")
        m3.container(border=True).metric(
            "Buckets compared", f"{fit['n']:,}",
            help=f"One point per {'day' if freq == 'D' else 'hour'} of the range, empty ones "
                 "included — an hour with no news of either kind is a real observation.")
        m4.container(border=True).metric(
            "Strongest pair here",
            f"{best.iloc[0]['r']:+.2f}" if len(best) else "—",
            help=f"{best.iloc[0]['a']} with {best.iloc[0]['b']}" if len(best) else
                 "Not enough types with twenty items each.")

        GAUGE_H = 420
        g1, g2, g3 = st.columns(3)

        with g1.container(border=True, height=GAUGE_H):
            st.markdown(panels.head("Correlation coefficient",
                                    f"{var_x[:14]} × {var_y[:14]}"), unsafe_allow_html=True)
            if same:
                st.caption("X and Y are the same event type, so the coefficient is 1 by "
                           "definition. Pick two different ones.")
            elif r != r:
                st.caption("Not enough buckets with any movement in them to fit a coefficient. "
                           "A longer range, or a type with more items, will give one.")
            else:
                # The arc carries |r|, because an arc has one end and a sign has two. The
                # colour and the printed number carry the direction — a dial that mapped −1 to
                # an empty arc would show "moves exactly opposite" as "no relationship", which
                # is the one reading it must never give.
                tone = POS if r >= 0 else "#8b7cff"
                st.markdown(
                    '<div class="axn-gauges solo">' + shapes.gauge(
                        f"{strength} {'positive' if r >= 0 else 'negative'}",
                        abs(r), 1.0, count=0, colour=tone if fit["clear"] else MUTED,
                        big=f"{r:+.2f}", foot="strength, 0 to 1",
                        note=(f"95% {fit['low']:+.2f} to {fit['high']:+.2f}"
                              if fit["low"] is not None else "")) + "</div>",
                    unsafe_allow_html=True)
                st.caption(
                    (f"When **{var_x.lower()}** rises, **{var_y.lower()}** "
                     + ("rises too" if r >= 0 else "falls") + f" — {strength}ly. ")
                    + ("The interval clears zero, so the direction is not chance."
                       if fit["clear"] else
                       "The interval straddles zero: this is not separable from chance."))

        with g2.container(border=True, height=GAUGE_H):
            st.markdown(panels.head("Scatter and trendline", axis_unit),
                        unsafe_allow_html=True)
            cloud = pd.DataFrame({"when": series_x.index, "x": series_x.to_numpy(),
                                  "y": series_y.to_numpy()})
            dots = alt.Chart(cloud).mark_circle(size=52, color=POS, opacity=0.55).encode(
                x=alt.X("x:Q", title=var_x, axis=alt.Axis(format=axis_fmt)),
                y=alt.Y("y:Q", title=var_y, axis=alt.Axis(format=axis_fmt)),
                tooltip=[alt.Tooltip("when:T", title="Bucket", format=time_fmt),
                         alt.Tooltip("x:Q", title=var_x, format=axis_fmt),
                         alt.Tooltip("y:Q", title=var_y, format=axis_fmt)])
            if r == r and not same:
                reach = pd.DataFrame({"x": [float(series_x.min()), float(series_x.max())]})
                reach["y"] = fit["intercept"] + fit["slope"] * reach["x"]
                dots = dots + alt.Chart(reach).mark_line(
                    color=NEG, strokeWidth=2, strokeDash=[5, 3]).encode(x="x:Q", y="y:Q")
            st.altair_chart(dots.properties(height=286), width="stretch")
            st.caption(f"One dot per {'day' if freq == 'D' else 'hour'}. The dashed line is "
                       "the least-squares fit, drawn only to show the direction.")

        with g3.container(border=True, height=GAUGE_H):
            st.markdown(panels.head(f"Where {var_y.lower()} happens", "dependent variable"),
                        unsafe_allow_html=True)
            where = coded[coded["category_label"] == var_y]
            placed = where[where["country"].notna()]
            if placed.empty:
                st.caption("Nothing of this type could be placed in a country in this range.")
            else:
                counts = placed["country"].map(countries.label).value_counts()
                top = counts.head(5)
                rest = int(counts[5:].sum())
                slices = pd.DataFrame({
                    "place": list(top.index) + (["Everywhere else"] if rest else []),
                    "count": list(top.values) + ([rest] if rest else [])})
                slices["share"] = slices["count"] / slices["count"].sum()
                SLICE_COLOURS = ["#f87171", "#fb923c", "#8b7cff", "#5ee6ff", "#46d39a", "#4a515a"]
                st.altair_chart(
                    alt.Chart(slices).mark_arc(innerRadius=48, outerRadius=92, cornerRadius=3,
                                               stroke="#0f1215", strokeWidth=2).encode(
                        theta=alt.Theta("count:Q", stack=True),
                        color=alt.Color("place:N", title=None, sort=list(slices["place"]),
                                        scale=alt.Scale(range=SLICE_COLOURS), legend=None),
                        order=alt.Order("count:Q", sort="descending"),
                        tooltip=[alt.Tooltip("place:N", title="Country"),
                                 alt.Tooltip("count:Q", title="Items"),
                                 alt.Tooltip("share:Q", title="Share", format=".1%")],
                    ).properties(height=208), width="stretch")
                st.markdown(
                    '<div class="axn-legend dots">' + "".join(
                        f'<span><i style="background:{SLICE_COLOURS[i % len(SLICE_COLOURS)]}"></i>'
                        f'{html_lib.escape(str(row.place))} {row.share:.0%}</span>'
                        for i, row in enumerate(slices.itertuples())) + "</div>",
                    unsafe_allow_html=True)
                st.caption(f"{len(placed):,} of {len(where):,} items of this type carry a "
                           "country. The rest the analyzer could not place.")

        LIST_H = 432
        e1, e2, e3 = st.columns(3)

        def _related(column, label: str, which: str) -> None:
            rows = coded[coded["category_label"] == which]
            with column.container(border=True, height=LIST_H):
                st.markdown(panels.head(f"Events related to {label}", which.lower(),
                                        count=len(rows) or None), unsafe_allow_html=True)
                if rows.empty:
                    st.caption("Nothing of this type in the range.")
                    return
                st.dataframe(
                    rows.sort_values("ts", ascending=False)
                        .head(30)[["title", "severity", "source", "url"]],
                    hide_index=True, width="stretch", height=288,
                    column_config={
                        "title": st.column_config.TextColumn("Headline", width="medium"),
                        "severity": st.column_config.ProgressColumn("Rating", min_value=0,
                                                                    max_value=5, format="%d"),
                        "source": st.column_config.TextColumn("Publisher", width="small"),
                        "url": st.column_config.LinkColumn("Source", display_text="open",
                                                           width="small")})

        _related(e1, "X", var_x)
        _related(e2, "Y", var_y)

        with e3.container(border=True, height=LIST_H):
            st.markdown(panels.head("Strongest pairs", "ranked on |r|",
                                    tip="Every pair of event types with at least twenty items, "
                                        "ranked by the strength of the relationship regardless "
                                        "of its direction."), unsafe_allow_html=True)
            if best.empty:
                st.caption("Not enough event types with twenty items each in this range.")
            else:
                rows_html = []
                for row in best.itertuples():
                    width = min(abs(row.r), 1.0) * 50
                    left = 50 if row.r >= 0 else 50 - width
                    tone = POS if row.r >= 0 else "#8b7cff"
                    rows_html.append(
                        f'<div class="infl" title="{html_lib.escape(row.a)} with '
                        f'{html_lib.escape(row.b)}">'
                        f'<span class="who">{html_lib.escape(row.a[:15])} × '
                        f'{html_lib.escape(row.b[:15])}</span>'
                        f'<span class="axis"><i class="zero"></i>'
                        f'<i class="bar" style="left:{left:.1f}%;width:{width:.1f}%;'
                        f'background:{tone};opacity:{"1" if row.clear else ".32"}"></i></span>'
                        f'<span class="score">{row.r:+.2f}</span></div>')
                st.markdown('<div class="axn-infl pairs">' + "".join(rows_html) + "</div>",
                            unsafe_allow_html=True)
            # The two things that stop this view being read as more than it is. Both are real
            # limits of the arithmetic, not hedging — and both kept to one paragraph, because
            # a caveat that runs past the bottom of its panel is a caveat nobody finishes.
            st.caption(
                "**Correlation is not cause.** Two subjects that rise together usually share "
                "a driver — one big news day lifts everything. "
                + (f"**{PAIR_SHARES}** divides that driver out."
                   if measure == PAIR_COUNTS else
                   "Shares remove the volume driver, but a third thing can still move both.")
                + " The interval also treats each bucket as independent, and news is not: a "
                  "busy hour stays busy, so the true interval is wider than the one above.")

if page == "Root cause":
    # ─── Drilling down one attribute at a time ───────────────────────────────────
    # A decomposition tree. Each column is a level; clicking a node opens the column to its
    # right. The number on a node is the total severity of everything under it, which means a
    # parent is exactly the sum of its children — so "this is 43% of Western Asia" is a real
    # share and not a figure of speech. The arithmetic is in osint/hierarchy.py.
    chain_name = st.selectbox(
        "Chain", list(hierarchy.CHAINS), key="rc_chain",
        help="Which attributes to walk, in order. Geography is the usual one; the event chain "
             "breaks the same items down by what happened, when, where and who carried it.")
    chain = hierarchy.CHAINS[chain_name]
    keys = [key for key, _column, _label in chain]

    # Switching chains starts the walk again: a path made of regions means nothing to a chain
    # that begins with event codes.
    if st.session_state.get("rc_chain_was") != chain_name:
        st.session_state["rc_path"], st.session_state["rc_chain_was"] = {}, chain_name
    path = st.session_state.setdefault("rc_path", {})


    @st.cache_data(ttl=600, show_spinner=False)
    def _enriched(stamp: str, frame: pd.DataFrame) -> pd.DataFrame:
        """Region, state and city for every row. `stamp` keys the cache to the window."""
        return hierarchy.enrich(frame)


    rich = _enriched(f"{range_name}|{len(df)}|{window_end}", df)
    whole = float(rich["score"].sum())

    def _pick(depth: int, value: str) -> None:
        """Open a node: keep the path above it, set this level, drop everything below."""
        chosen = {k: path.get(k) for k in keys[:depth] if path.get(k) is not None}
        chosen[keys[depth]] = value
        st.session_state["rc_path"] = chosen

    def _reset() -> None:
        st.session_state["rc_path"] = {}

    here = hierarchy.narrow(rich, chain, path)
    crumbs = hierarchy.trail(chain, path)

    s1, s2, s3, s4 = st.columns(4)
    s1.container(border=True).metric(
        "Significance here", f"{float(here['score'].sum()):,.0f}",
        delta=f"{float(here['score'].sum()) / whole:.0%} of the window" if whole else None,
        delta_color="off",
        help="Total severity of every item under the path you have opened. Severity is set "
             "by the analyzer on negative items, so a quiet branch scores low because it is "
             "quiet, not because it is missing.")
    s2.container(border=True).metric("Items here", f"{len(here):,}")
    s3.container(border=True).metric("Depth", f"{len(crumbs)} / {len(chain)}")
    s4.container(border=True).metric(
        "Window total", f"{whole:,.0f}",
        help="Every level sums to this, which is the point of using a total rather than an "
             "average: the parts add up to the whole.")

    st.markdown(
        '<div class="axn-crumbs"><span class="step">All</span>'
        + "".join(f'<span class="sep">›</span><span class="step on">'
                  f'<em>{html_lib.escape(label)}</em>{html_lib.escape(value)}</span>'
                  for label, value in crumbs)
        + "</div>", unsafe_allow_html=True)
    if crumbs:
        st.button("↩ Start again", on_click=_reset, key="rc_reset")

    # ── The tree: one column per level, opened as far as the path goes ───────────
    opened = min(len(chain), len(path) + 1)
    TREE_H = 432
    columns = st.columns(opened)
    for depth in range(opened):
        _key, _column, label = chain[depth]
        nodes = hierarchy.level(rich, chain, {k: path.get(k) for k in keys[:depth]}, depth)
        with columns[depth].container(border=True, height=TREE_H):
            parent = float(nodes["score"].sum())
            st.markdown(panels.head(label, f"{parent:,.0f}" if parent else "nothing"),
                        unsafe_allow_html=True)
            if nodes.empty:
                st.caption("Nothing below this.")
                continue
            for row in nodes.itertuples():
                chosen = path.get(keys[depth]) == row.value
                st.button(
                    f"{row.value}  ·  {row.score:,.0f}  ·  {row.share:.0%}",
                    key=f"rc_{depth}_{row.value}", on_click=_pick, args=(depth, row.value),
                    width="stretch", type="primary" if chosen else "secondary",
                    help=f"{row.items:,} items, {row.score:,.0f} of this level's "
                         f"{parent:,.0f}")

    # ── The bottom of the drill: the items themselves ────────────────────────────
    with st.container(border=True):
        st.subheader("What is under this node" if crumbs else "The heaviest items in the window")
        rows = hierarchy.detail(rich, chain, path, limit=25)
        if rows.empty:
            st.caption("No items under this path.")
        else:
            rows = rows.assign(contribution=rows["score"] / whole if whole else 0.0)
            st.dataframe(
                rows[[c for c in ("ts", "title", "country_name", "city", "source", "score",
                                  "contribution", "url") if c in rows]],
                hide_index=True, width="stretch",
                column_config={
                    "ts": st.column_config.DatetimeColumn("Time", format="D MMM, h:mm a"),
                    "title": "Event title",
                    "country_name": "Country", "city": "City", "source": "Publisher",
                    "score": st.column_config.ProgressColumn(
                        "Severity", min_value=0, max_value=5, format="%d",
                        help="What this one item contributes to every node above it."),
                    "contribution": st.column_config.NumberColumn(
                        "Share of window", format="%.3f%%",
                        help="This item's severity as a share of the whole window's total."),
                    "url": st.column_config.LinkColumn("Source URL", display_text="open"),
                })
            st.caption("A chain links attributes that arrived together. It shows what went "
                       "with what — not what caused what; nothing here can tell those apart.")

if page == "Tracking":
    # ─── The line, and where it is heading ────────────────────────────────────────
    # This view ignores the time range on purpose and reads the whole collection history
    # instead: a forecast fitted to the fourteen days you happen to be looking at would be a
    # forecast of the window, not of the series. Every other filter — category, type, country,
    # source, search — still applies, so "where is crime in Bangladesh heading" is a question
    # this can take.
    tracked = narrow(items)
    series = forecast.daily(tracked)
    projection = forecast.project(series, horizon=7)
    flagged = forecast.spikes(series)

    t1, t2, t3, t4 = st.columns(4)
    t1.container(border=True).metric(
        "Whole days of history", f"{len(series):,}",
        help="The day in progress is left out — at nine in the morning it holds a ninth of "
             "its eventual count, and a model fitted through it learns a fall that is not there.")
    t2.container(border=True).metric(
        "Last whole day", f"{int(series.iloc[-1]):,}" if len(series) else "—",
        delta=f"{(series.iloc[-1] / series.iloc[-2] - 1):+.0%} on the day before"
        if len(series) > 1 and series.iloc[-2] else None)
    ahead = projection[projection["kind"] == "forecast"]
    t3.container(border=True).metric(
        "Next 7 days, projected", f"{ahead['value'].sum():,.0f}" if not ahead.empty else "—",
        help="The sum of the seven daily point forecasts. The band on the chart is what that "
             "number is actually worth.")
    t4.container(border=True).metric(
        "Days that stand out", f"{int(flagged.sum()) if len(flagged) else 0:,}",
        help="Days more than two scaled deviations from the seven-day rolling median.")

    with st.container(border=True):
        st.subheader("Event trend and forecast")
        if projection.empty:
            st.caption(forecast.describe(series))
        else:
            actual = projection[projection["kind"] == "actual"]
            band = alt.Chart(ahead).mark_area(opacity=0.17, color="#8b7cff").encode(
                x=alt.X("day:T", title=None, axis=alt.Axis(format="%d %b")),
                y=alt.Y("low:Q", title="Items collected"), y2="high:Q",
                tooltip=[alt.Tooltip("day:T", title="Day", format="%d %b"),
                         alt.Tooltip("low:Q", title="Lower bound", format=",.0f"),
                         alt.Tooltip("high:Q", title="Upper bound", format=",.0f")])
            history = alt.Chart(actual).mark_line(color=POS, strokeWidth=2).encode(
                x="day:T", y=alt.Y("value:Q", title="Items collected"),
                tooltip=[alt.Tooltip("day:T", title="Day", format="%d %b"),
                         alt.Tooltip("value:Q", title="Collected", format=",.0f")])
            # The forecast is joined to the last real point, or the two lines float apart and
            # the eye reads a gap that is not in the data.
            joined = pd.concat([actual.tail(1), ahead], ignore_index=True)
            ahead_line = alt.Chart(joined).mark_line(
                color="#8b7cff", strokeWidth=2, strokeDash=[5, 4]).encode(
                x="day:T", y="value:Q",
                tooltip=[alt.Tooltip("day:T", title="Day", format="%d %b"),
                         alt.Tooltip("value:Q", title="Forecast", format=",.0f")])
            layers = [band, history, ahead_line]
            if len(flagged) and flagged.any():
                marks = pd.DataFrame({"day": series.index[flagged.fillna(False)],
                                      "value": series[flagged.fillna(False)].values})
                layers.append(alt.Chart(marks).mark_point(
                    color=NEG, size=90, filled=True, opacity=1).encode(
                    x="day:T", y="value:Q",
                    tooltip=[alt.Tooltip("day:T", title="Stands out", format="%d %b"),
                             alt.Tooltip("value:Q", title="Collected", format=",.0f")]))
            st.altair_chart(alt.layer(*layers).properties(height=330), width="stretch")
            st.markdown(
                f'<div class="axn-legend">'
                f'<span><i style="background:{POS}"></i>collected</span>'
                f'<span><i style="background:#8b7cff"></i>forecast, 7 days</span>'
                f'<span><i style="background:#8b7cff;opacity:.4"></i>95% band</span>'
                f'<span><i style="background:{NEG};border-radius:50%;width:8px;height:8px">'
                f'</i>stands out</span></div>', unsafe_allow_html=True)
            st.caption(forecast.describe(series))

    with st.container(border=True):
        st.subheader("Tracked events")
        if df.empty:
            st.caption("Nothing matches these filters in the selected range.")
        else:
            log = df.sort_values("ts", ascending=False).head(400).assign(
                code=lambda d: "SAXN-" + d["ts"].dt.strftime("%y%m%d") + "-"
                               + d["id"].str[:6].str.upper(),
                country_name=lambda d: d["country"].map(countries.label),
                # The analyzer only files a category on negative items, so the rest would
                # print the word "None" down the column. An em dash says the same thing
                # without looking like a bug.
                category_label=lambda d: d["category_label"].fillna("—"))
            st.dataframe(
                log[["code", "ts", "category_label", "country_name", "severity", "title",
                     "source", "url"]],
                hide_index=True, width="stretch", height=380,
                column_config={
                    "code": st.column_config.TextColumn("Event code"),
                    "ts": st.column_config.DatetimeColumn("Event datetime", format="D MMM, h:mm a"),
                    "category_label": "Event category", "country_name": "Country",
                    "severity": st.column_config.ProgressColumn("Rating", min_value=0,
                                                                max_value=5, format="%d"),
                    "title": "Title", "source": "Publisher",
                    "url": st.column_config.LinkColumn("Source URL", display_text="open"),
                })
            st.caption(f"The {min(len(df), 400):,} most recent of {len(df):,} items in "
                       f"{range_name.lower()}. The chart above reads the whole collection "
                       "history instead, because a forecast needs more than one window.")

if page == "Benchmark":
    # ─── This window against the same window, a week and four weeks back ──────────
    # The Overview already says how this range compares with last week, in one number under
    # the count. That number answers "busier or quieter" and nothing else, and the question it
    # cannot answer is the one a reader actually has: *busier at what*, and *is this a real
    # shift or an ordinary swing*. One comparison cannot tell those apart — a week-on-week jump
    # looks identical whether it is news or noise. Two comparisons can: a range that is up on
    # last week and up on four weeks ago has moved, and a range that is up on last week while
    # sitting exactly where it was four weeks ago has merely come back.
    #
    # Four weeks rather than a calendar month, deliberately. A month shifts the weekday —
    # comparing a Tuesday with a Saturday reports a collapse that is only the calendar — and
    # the weekday is the single largest regular swing in news volume. Twenty-eight days lands
    # on the same day of the week, so the comparison is of like with like.
    BENCH = "#8b7cff"        # the four-week series: clear of both the red and the cyan
    month_ago = window_end - pd.Timedelta(days=28)
    prior_month = since(month_ago - span, month_ago)
    prior_month_neg = prior_month[prior_month["sentiment"] == "negative"]
    # Same guard as the week comparison, and it matters more here: four weeks ago is before
    # this monitor was collecting at all, and an empty window would read as a 100% rise. That
    # is a lie told with real arithmetic, so the panels say so instead of printing it.
    comparable_month = len(prior_month) > 0 and record_start <= month_ago - span

    def _pct(now_n: int, then_n: int) -> str:
        """A signed percentage against a window we have, and a dash against one we do not."""
        return "—" if not then_n else f"{(now_n - then_n) / then_n:+.0%}"

    def _key(*entries: tuple[str, str]) -> str:
        return ('<div class="axn-legend">' + "".join(
            f'<span><i style="background:{colour}"></i>{html_lib.escape(text)}</span>'
            for text, colour in entries) + "</div>")

    # ─── The change indicators ────────────────────────────────────────────────────
    # Volume is only one of the things that can move. A range with the same count as last week
    # but half of it newly negative has changed a great deal, and a row that reported only the
    # count would call it flat.
    prior_share = len(prior[prior["sentiment"] == "negative"]) / len(prior) if len(prior) else None
    here_share = len(neg) / len(df)
    prior_sev = prior_neg["severity"].mean() if len(prior_neg) else None
    here_sev = neg["severity"].mean() if len(neg) else None
    m1, m2, m3, m4 = st.columns(4)
    m1.container(border=True).metric(
        "Events", f"{len(df):,}",
        delta=f"{movement:+.0%} vs last week" if movement is not None else None,
        help=f"{len(prior):,} in the same window seven days ago" if comparable else
             "No week-ago window to compare against yet.")
    m2.container(border=True).metric(
        "Against four weeks ago", _pct(len(df), len(prior_month)) if comparable_month else "—",
        help=f"{len(prior_month):,} in the same window 28 days ago" if comparable_month else
             f"The record starts {record_start:%d %b %Y}, which is not four weeks back "
             "for this window. The comparison fills in on its own as the monitor runs.")
    m3.container(border=True).metric(
        "Negative share", f"{here_share:.0%}",
        delta=f"{(here_share - prior_share) * 100:+.0f} pts vs last week"
              if prior_share is not None and comparable else None,
        delta_color="inverse",
        help="Percentage points, not percent: a share that moves from 20% to 30% has risen "
             "ten points and by half, and only one of those two numbers is worth printing.")
    m4.container(border=True).metric(
        "Avg severity (negative)", f"{here_sev:.1f} / 5" if here_sev is not None else "—",
        delta=f"{here_sev - prior_sev:+.2f} vs last week"
              if here_sev is not None and prior_sev is not None and comparable else None,
        delta_color="inverse")

    # ─── Three dials on one scale ─────────────────────────────────────────────────
    # All three arcs are drawn against the largest of the three counts, so the lengths can be
    # read against each other directly. Three dials each filled to their own maximum would be
    # three identical pictures of three different numbers.
    GAUGE_H = 324
    ceiling = max(len(df), len(prior), len(prior_month)) or 1
    g1, g2, g3 = st.columns(3)

    with g1.container(border=True, height=GAUGE_H):
        st.markdown(panels.head("Same window, four weeks ago", "28 days back"),
                    unsafe_allow_html=True)
        if comparable_month:
            st.markdown(
                '<div class="axn-gauges solo">' + shapes.gauge(
                    f"{month_ago - span:%d %b} – {month_ago:%d %b}",
                    len(prior_month), ceiling, count=len(prior_month), colour=BENCH,
                    foot="events", note=f"this range is {_pct(len(df), len(prior_month))}")
                + "</div>", unsafe_allow_html=True)
        else:
            st.markdown(
                '<div class="axn-gauges solo">' + shapes.gauge(
                    "no window to read", 0, ceiling, count=0, colour=MUTED, foot="events")
                + "</div>", unsafe_allow_html=True)
            st.caption(f"Collection starts {record_start:%d %b %Y}, so there is no window "
                       "four weeks before this one to compare against. Nothing is estimated in "
                       "its place; the dial fills once the record is long enough.")

    with g2.container(border=True, height=GAUGE_H):
        st.markdown(panels.head("This range", range_name.lower()), unsafe_allow_html=True)
        st.markdown(shapes.ring(
            len(df), len(df) / ceiling, caption="events in this range", colour=POS,
            note=f"{_pct(len(df), len(prior))} on last week · "
                 f"{_pct(len(df), len(prior_month)) if comparable_month else '—'} on four weeks"),
            unsafe_allow_html=True)

    with g3.container(border=True, height=GAUGE_H):
        st.markdown(panels.head("Same window, last week", "7 days back"), unsafe_allow_html=True)
        if comparable:
            st.markdown(
                '<div class="axn-gauges solo">' + shapes.gauge(
                    f"{week_ago - span:%d %b} – {week_ago:%d %b}",
                    len(prior), ceiling, count=len(prior), colour=MUTED,
                    foot="events", note=f"this range is {_pct(len(df), len(prior))}")
                + "</div>", unsafe_allow_html=True)
        else:
            st.markdown(
                '<div class="axn-gauges solo">' + shapes.gauge(
                    "no window to read", 0, ceiling, count=0, colour=MUTED, foot="events")
                + "</div>", unsafe_allow_html=True)
            st.caption("The monitor's record does not reach a week before this window yet.")

    # ─── Shape, trend, and what moved ─────────────────────────────────────────────
    ROW_B = 448
    b1, b2, b3 = st.columns(3)

    with b1.container(border=True, height=ROW_B):
        st.markdown(panels.head("Category benchmark", "three windows",
                                tip="One polygon per window over the same six event types, all "
                                    "on one scale. A type that has grown shows as a spike "
                                    "rather than as a number to hold in your head."),
                    unsafe_allow_html=True)
        if neg.empty:
            st.caption("No negative items in this range, and the event type is only filed on "
                       "negative items.")
        else:
            spokes = neg["category_label"].value_counts().head(6).index.tolist()
            was_week = prior_neg["category_label"].value_counts()
            was_month = prior_month_neg["category_label"].value_counts()
            series, legend = [], []
            if comparable_month:
                series.append(("Four weeks ago", BENCH,
                               [int(was_month.get(name, 0)) for name in spokes]))
                legend.append(("four weeks ago", BENCH))
            if comparable:
                series.append(("Last week", MUTED, [int(was_week.get(name, 0)) for name in spokes]))
                legend.append(("same window last week", MUTED))
            series.append(("This range", NEG,
                           [int((neg["category_label"] == name).sum()) for name in spokes]))
            legend.append(("this range", NEG))
            st.markdown('<div class="axn-radar">' + shapes.radar(spokes, series) + "</div>"
                        + _key(*legend), unsafe_allow_html=True)
            if not comparable:
                st.caption("Only this range is drawn — there is no earlier window to lay under it.")

    with b2.container(border=True, height=ROW_B):
        st.markdown(panels.head("Trend comparison", "aligned on elapsed time",
                                tip="The three windows are laid over each other from their own "
                                    "starts, so hour 3 of this range sits above hour 3 of last "
                                    "week's — not above the same clock time, which would "
                                    "compare a morning with an evening."),
                    unsafe_allow_html=True)
        step = pd.Timedelta(hours=1) if hours <= 72 else pd.Timedelta(days=1)
        unit = "hours" if hours <= 72 else "days"
        # Only buckets that have actually finished. A custom range can close at a midnight that
        # has not happened, and its empty tail beside two complete windows would read as a
        # collapse rather than as a clock that has not got there yet.
        total = max(1, int(span // step))
        elapsed = total if window_end <= now else max(1, int((now - (window_end - span)) // step))
        PERIODS = (("This range", NEG, df, window_end - span, True),
                   ("Last week", MUTED, prior, week_ago - span, comparable),
                   ("Four weeks ago", BENCH, prior_month, month_ago - span, comparable_month))
        curves, tone = [], {}
        for name, colour, frame, start, usable in PERIODS:
            if not usable or frame.empty:
                continue
            counted = (((frame["ts"] - start) // step).astype(int).value_counts().sort_index()
                       .reindex(range(elapsed), fill_value=0))
            curves.append(pd.DataFrame({"step": list(counted.index),
                                        "count": counted.to_numpy(), "series": name}))
            tone[name] = colour
        if not curves:
            st.caption("Nothing to lay side by side in this range.")
        else:
            curve = pd.concat(curves, ignore_index=True)
            order = [name for name, _, _, _, _ in PERIODS if name in tone]
            st.altair_chart(
                alt.Chart(curve).mark_line(strokeWidth=2).encode(
                    x=alt.X("step:Q", title=f"{unit} into the window",
                            scale=alt.Scale(nice=False, domainMin=0)),
                    y=alt.Y("count:Q", title="Items"),
                    color=alt.Color("series:N", legend=None,
                                    scale=alt.Scale(domain=order,
                                                    range=[tone[name] for name in order])),
                    tooltip=[alt.Tooltip("series:N", title="Window"),
                             alt.Tooltip("step:Q", title=unit.capitalize()),
                             alt.Tooltip("count:Q", title="Items")],
                ).properties(height=272), width="stretch")
            st.markdown(_key(*[(name.lower(), tone[name]) for name in order]),
                        unsafe_allow_html=True)
            # The earlier windows are closed and complete; this one runs to the present, and
            # its last hours hold only what the collector has got to. A reader who does not
            # know that reads the tail as a collapse in the news rather than a gap in us.
            behind = (now - items["collected"].max()).total_seconds() / 3600
            if behind >= 1.5:
                st.caption(f"The last collection was {behind:.0f} hours ago, so the right-hand "
                           "end of this range is short by that much. The two earlier windows "
                           "are complete, which makes the gap look like a fall.")

    # ─── What moved, on three dimensions ──────────────────────────────────────────
    # The same widget three times rather than three different ones: bars drawn out from a
    # centre line, right for growth and left for decline, so the sign is read before the
    # number. Measured against last week rather than four weeks, because every row has a
    # week-ago figure and only some have a four-week one, and a column with holes in it
    # invites the reader to fill them in.
    def _movers(column: str, *, limit: int = 9, labeller=None) -> str:
        here = df[column].value_counts().head(limit)
        if here.empty:
            return ""
        was = prior[column].value_counts()
        pairs = []
        for name, n_now in here.items():
            n_then = int(was.get(name, 0))
            shown = labeller(name) if labeller else str(name)
            pairs.append((shown, int(n_now), n_then,
                          None if not n_then else (n_now - n_then) / n_then))
        # The bar is full at a move of 100% either way, rather than scaled to whatever the
        # largest move in the list happens to be. A single new feed goes up by twelve hundred
        # percent, and scaling to it squashes every real move in the list into a stub — which
        # is how a chart ends up showing one outlier and nine blank rows.
        out = []
        for shown, n_now, n_then, pct in pairs:
            if pct is None:
                bar, score = "", "new"
            else:
                width = min(abs(pct), 1.0) * 50               # half the track each way
                left = 50 if pct >= 0 else 50 - width
                # A move measured off three items is arithmetic, not evidence. It is still
                # shown — it may be the thing worth looking at — but hollow, the same way the
                # Correlation view marks a difference its interval cannot separate from zero.
                bar = (f'<i class="bar" style="left:{left:.1f}%;width:{width:.1f}%;'
                       f'background:{NEG if pct >= 0 else POS};'
                       f'opacity:{"1" if n_then >= 5 else ".32"}"></i>')
                # Past a tenfold move the percentage stops being readable — "+125500%" is five
                # characters of noise — so it is said as a multiple instead.
                score = f"×{1 + pct:.0f}" if abs(pct) >= 10 else f"{pct:+.0%}"
            out.append(
                f'<div class="infl" title="{html_lib.escape(shown)} — {n_now:,} in this range '
                f'against {n_then:,} the same window last week">'
                f'<span class="who">{html_lib.escape(shown)}</span>'
                f'<span class="axis"><i class="zero"></i>{bar}</span>'
                f'<span class="score">{score}</span></div>')
        return '<div class="axn-infl">' + "".join(out) + "</div>"

    with b3.container(border=True, height=ROW_B):
        st.markdown(panels.head("Change by event type", "vs last week"), unsafe_allow_html=True)
        if not comparable:
            st.caption("No week-ago window to measure movement against yet.")
        elif neg.empty:
            st.caption("No negative items in this range to break down.")
        else:
            st.markdown(_movers("category_label"), unsafe_allow_html=True)
            st.caption("Bars run from the centre: right is more than last week, left is less. "
                       "A type that was absent last week reads “new” rather than +∞.")

    ROW_C = 436
    t1, t2, t3 = st.columns(3)

    with t1.container(border=True, height=ROW_C):
        # Severity is only filed on negative items, so this is `neg` and not `df`: sorting the
        # whole window by a column that is null on two thirds of it puts unrated neutral
        # stories at the top of a list whose whole point is the rating.
        graded = neg[neg["severity"].notna()]
        st.markdown(panels.head("Top rated events", range_name.lower(),
                                count=len(graded) or None,
                                tip="Sorted by the analyzer's severity rating, hardest first, "
                                    "with the publisher and the original link beside each."),
                    unsafe_allow_html=True)
        if graded.empty:
            st.caption("Nothing graded in this range. The severity rating is only set on "
                       "negative items, and only once the analyzer has read them.")
        else:
            top = graded.sort_values(["severity", "ts"], ascending=[False, False]).head(25)
            # Four columns, not five. The event code belongs on the Events and Tracking
            # tables, where there is width for it; in a third of a row it pushes the rating —
            # the one column this list exists for — off the right-hand edge.
            st.dataframe(
                top[["title", "severity", "source", "url"]],
                hide_index=True, width="stretch", height=330,
                column_config={
                    "title": st.column_config.TextColumn("Event", width="medium"),
                    "severity": st.column_config.ProgressColumn("Rating", min_value=0,
                                                                max_value=5, format="%d"),
                    "source": st.column_config.TextColumn("Publisher", width="small"),
                    "url": st.column_config.LinkColumn("Source URL", display_text="open",
                                                       width="small"),
                })

    with t2.container(border=True, height=ROW_C):
        st.markdown(panels.head("Change by publisher", "vs last week"), unsafe_allow_html=True)
        if not comparable:
            st.caption("No week-ago window to measure movement against yet.")
        else:
            st.markdown(_movers("source"), unsafe_allow_html=True)
            st.caption("A publisher that has climbed is as often a feed we added as a newsroom "
                       "that got busier — this row is worth reading beside the collector's log.")

    with t3.container(border=True, height=ROW_C):
        st.markdown(panels.head("Change by country", "vs last week"), unsafe_allow_html=True)
        if not comparable:
            st.caption("No week-ago window to measure movement against yet.")
        elif df["country"].notna().sum() == 0:
            st.caption("No country was identified on anything in this range.")
        else:
            st.markdown(_movers("country", labeller=countries.label), unsafe_allow_html=True)
            st.caption("Only items the analyzer could place. An item it could not place counts "
                       "towards no country at all, so these do not sum to the total.")

if page == "SITREP":
    # ─── The situational report ───────────────────────────────────────────────────
    # Every other view answers a question you came with. This one is for the reader who came
    # with none: what is going on, how bad is it against a normal day, what is coming, and
    # where do I click to check any of it. That is a different job from analysis and it is
    # why it gets its own page rather than another chart on an existing one.
    #
    # Three sources, kept visibly apart, because they are worth different amounts:
    #   * the counts, which are ours and exact;
    #   * the brief, which a language model wrote from those counts and may phrase badly;
    #   * the outlook, which is arithmetic on the past and is a projection, not knowledge.
    # Printing them in one voice would make the weakest of the three sound like the strongest.
    DOMAINS = {
        "Security & conflict": ("security", "politics_unrest", "crime"),
        "Financial": ("economy", "corruption"),
        "Civil & social": ("social", "health"),
        "Natural & accident": ("disaster", "accident"),
        "Other": ("sports", "other"),
    }
    DOMAIN_OF = {code: name for name, codes in DOMAINS.items() for code in codes}
    LEVELS = {"Red": "#f87171", "Orange": "#fb923c", "Yellow": "#facc15", "Normal": "#46d39a"}

    def _grade(share: float, middle: float, spread: float) -> str:
        """A day's negative share, graded against our own normal and against the alert bar.

        The alert bar alone will not do. It is set at 50% because that is where a ticket is
        worth an email at three in the morning, and this collection runs at about 35% every
        single day — so grading on the bar painted twenty-six days green and said nothing. A
        timeline on which nothing ever varies is not a timeline.
        
        So the first test is against the collection's own middle, measured in robust standard
        deviations (median absolute deviation, scaled). That answers the question a
        situational report is actually asked: is today unlike our ordinary day. The absolute
        bar is kept as an override, so a day that really would have raised a ticket can never
        come out quiet because the rest of the month was worse.

        What this costs, and the panel says so: a red day here means "far above our normal",
        not "dangerous". The two readings are different and only the second sends an email.
        """
        if spread <= 0:
            return "Normal"
        z = (share - middle) / spread
        if z >= 2.0 or share >= bar + 0.08:
            return "Red"
        if z >= 1.25 or share >= bar:
            return "Orange"
        if z >= 0.6:
            return "Yellow"
        return "Normal"

    d1, d2 = st.columns([2.4, 1.2])
    picked_domains = d1.multiselect(
        "Intelligence domain", list(DOMAINS), default=list(DOMAINS),
        help="A grouping of the analyzer's eleven event types. It files a cyber incident "
             "under security or crime, so there is no cyber domain to offer — that would "
             "need the analyzer's own categories changed and the archive re-read.")
    chosen = picked_domains or list(DOMAINS)
    wanted_codes = {code for name in chosen for code in DOMAINS[name]}
    scope_all = df[df["category"].isin(wanted_codes) | df["category"].isna()]
    scope_neg = neg[neg["category"].isin(wanted_codes)]
    d2.metric("In these domains", f"{len(scope_neg):,}",
              help="Negative items. The event type is only filed on negative items, so the "
                   "domain filter cannot reach the neutral and positive ones.")

    # ─── The day log, computed rather than archived ───────────────────────────────
    # There is no stored history of past reports: until now the brief table kept one row per
    # window and overwrote it hourly, so yesterday's words are gone and cannot be recovered.
    # What can be recovered is yesterday's *numbers*, because every item is still here. So the
    # timeline is graded from the items themselves — a real history, derived rather than
    # archived, and the panel says which it is.
    daily = items.assign(day=items["ts"].dt.floor("D"))
    log = daily.groupby("day").agg(total=("id", "size"),
                                   negative=("sentiment", lambda c: int((c == "negative").sum()))
                                   ).reset_index().sort_values("day")
    # Two hundred items, not the alert check's twenty. A day graded off a handful of items is
    # a coin toss dressed as a reading, and the day that is still happening always looks
    # strange because only part of it has arrived.
    log = log[log["total"] >= max(alert_cfg["min_items"], 200)]
    if len(log) > 1 and log.iloc[-1]["day"] == now.floor("D"):
        log = log.iloc[:-1]
    if len(log):
        log["share"] = log["negative"] / log["total"]
        middle = float(log["share"].median())
        spread = float((log["share"] - middle).abs().median()) * 1.4826
        log["level"] = log["share"].map(lambda v: _grade(v, middle, spread))
    else:
        middle, spread = 0.0, 0.0
    log = log.sort_values("day", ascending=False)

    k1, k2, k3, k4 = st.columns(4)
    # The live alert window is the last few hours, and when the collector is behind there is
    # nothing in it. An empty window has a negative share of zero, which printed as "0%
    # negative — normal" beside a panel saying 41%: not a quiet world, an empty window. When
    # it cannot be read, the level is taken from the range the reader actually chose, and
    # every line that reports it says which of the two it used.
    live_enough = len(win) >= alert_cfg["min_items"]
    range_share = len(neg) / len(df)
    behind_h = (now - items["collected"].max()).total_seconds() / 3600
    now_level = _grade(win_share if live_enough else range_share, middle, spread)
    k1.container(border=True).metric(
        "Current level", now_level,
        help=(f"Last {alert_cfg['window_hours']}h at {win_share:.0%} negative against a "
              f"{bar:.0%} bar." if live_enough else
              f"Nothing has arrived in the last {alert_cfg['window_hours']}h — collection is "
              f"{behind_h:.0f}h behind — so this is graded on {range_name.lower()} instead, "
              f"at {range_share:.0%} negative."))
    k2.container(border=True).metric(
        "Days logged", f"{len(log):,}",
        help="Whole days with enough items to grade. Graded from the stored items, not from "
             "archived reports.")
    k3.container(border=True).metric(
        "Days above our normal",
        f"{int((log['level'].isin(['Orange', 'Red'])).sum()):,}" if len(log) else "0",
        help="Days whose negative share sat well above this collection's own median. Not days "
             "that raised an alert — that bar is far higher and the next tile counts it.")
    k4.container(border=True).metric(
        "Tickets raised", f"{len(tickets):,}",
        help="Alert emails the monitor actually sent. These are the real log; the day grades "
             "beside them are this page's own reading of the same data.")

    TOP_H = 466
    s1, s2, s3 = st.columns(3)

    # ── Tomorrow ─────────────────────────────────────────────────────────────────
    with s1.container(border=True, height=TOP_H):
        st.markdown(panels.head("Tomorrow", "projected, not known"), unsafe_allow_html=True)
        history = forecast.daily(items)
        ahead = forecast.project(history, horizon=1)
        tomorrow = ahead[ahead["kind"] == "forecast"]
        if tomorrow.empty:
            st.caption(forecast.describe(history))
        else:
            row = tomorrow.iloc[0]
            yesterday = float(history.iloc[-1])
            move = (row["value"] - yesterday) / yesterday if yesterday else 0.0
            st.markdown(
                f'<div class="axn-banner"><span class="axn-label">expected volume</span>'
                f'<span class="n">{row["value"]:,.0f}</span>'
                f'<span class="sub">{row["low"]:,.0f} to {row["high"]:,.0f}, 95% of the time'
                f'</span></div>', unsafe_allow_html=True)
            # Which domain is climbing fastest, measured the same way the Benchmark view
            # measures everything else: this window against the same window a week back.
            climbing, before = [], prior[prior["sentiment"] == "negative"]
            for name, codes in DOMAINS.items():
                here = int(neg["category"].isin(codes).sum())
                then = int(before["category"].isin(codes).sum())
                if comparable and then >= 20:
                    climbing.append((name, (here - then) / then, here))
            lines = [
                f"Collection is heading **{'up' if move >= 0 else 'down'} {abs(move):.0%}** on "
                f"the last whole day.",
                (f"The last {alert_cfg['window_hours']}h is **{win_share:.0%} negative** "
                 f"against a **{bar:.0%}** alert bar, which reads **{now_level.lower()}**."
                 if live_enough else
                 f"Nothing has arrived in the last {alert_cfg['window_hours']}h — collection "
                 f"is **{behind_h:.0f}h behind**, so the live alert window cannot be read. "
                 f"Over {range_name.lower()} it is **{range_share:.0%} negative**, which "
                 f"reads **{now_level.lower()}**."),
            ]
            if climbing:
                # Ranked on the size of the move, not its sign. A domain that has halved is as
                # much the story of the week as one that has doubled, and sorting by the
                # signed figure would report the least-fallen as "fastest rising".
                climbing.sort(key=lambda row: -abs(row[1]))
                top = climbing[0]
                lines.append(f"**{top[0]}** has moved most — {top[1]:+.0%} on the same window "
                             f"last week, {top[2]:,} items.")
            for line in lines:
                st.markdown(f"- {line}")
            st.caption("Arithmetic on our own collection, not a forecast of the world: it "
                       "moves when a feed is added or a run fails. The written brief beside "
                       "this is the model's work; these numbers are not.")

    # ── The executive briefing ───────────────────────────────────────────────────
    with s2.container(border=True, height=TOP_H):
        brief = briefs.get(hours) or briefs.get(24)
        st.markdown(panels.head("Executive briefing",
                                (brief or {}).get("model", "no model") if brief else "none",
                                status=("cached", "written hourly") if brief else None),
                    unsafe_allow_html=True)
        if not brief:
            st.caption("No brief has been written for a window this length yet. The collector "
                       "writes one for 6h, 24h, 3d, 7d and 14d.")
        else:
            st.markdown(f'<div class="axn-stats sitrep">'
                        f'<div><span class="axn-label">items</span><span class="v">{len(df):,}</span></div>'
                        f'<div><span class="axn-label">negative</span><span class="v">{len(neg) / len(df):.0%}</span></div>'
                        f'<div><span class="axn-label">countries</span>'
                        f'<span class="v">{df["country"].nunique():,}</span></div>'
                        f'</div>', unsafe_allow_html=True)
            st.markdown(str(brief["text"]))
            written = to_local(brief["created_at"], cfg) if brief.get("created_at") else "—"
            st.caption(f"Written {written} by {brief.get('model') or 'a rule, with no model'} "
                       "from the counts above. A language model's phrasing — check anything "
                       "you intend to repeat against the headlines it came from.")

    # ── The timeline ─────────────────────────────────────────────────────────────
    with s3.container(border=True, height=TOP_H):
        st.markdown(panels.head("SITREP timeline", "one row per day",
                                count=len(log) or None,
                                tip="Each day graded on its negative share against this "
                                    "collection's own normal, in robust standard deviations, "
                                    "with the ticket bar as an override. Derived from the "
                                    "stored items, so it reaches back as far as they do."),
                    unsafe_allow_html=True)
        if log.empty:
            st.caption("No day in the archive has enough items to grade yet.")
        else:
            ticket_days = set()
            if len(tickets):
                ticket_days = set(tickets["created"].dt.floor("D"))
            rows_html = []
            for row in log.itertuples():
                colour = LEVELS[row.level]
                flag = ' <span class="chip">ticket</span>' if row.day in ticket_days else ""
                rows_html.append(
                    f'<div class="sit-row"><span class="when">{row.day:%d %b}</span>'
                    f'<span class="lvl" style="color:{colour};border-color:{colour}66">'
                    f'{row.level}</span>'
                    f'<span class="val">{row.share:.0%}</span>'
                    f'<span class="sub">{row.negative:,} of {row.total:,}{flag}</span></div>')
            st.markdown('<div class="axn-sitlog">' + "".join(rows_html) + "</div>",
                        unsafe_allow_html=True)
            st.caption(f"Our ordinary day is **{middle:.0%}** negative, give or take "
                       f"**{spread * 100:.1f}** points. Orange is well above that, not "
                       f"dangerous — the {bar:.0%} bar that actually sends an email is far "
                       "higher, and no day here has reached it.")

    LOW_H = 452
    t1, t2, t3 = st.columns(3)

    # ── Highlights, by domain ────────────────────────────────────────────────────
    with t1.container(border=True, height=LOW_H):
        st.markdown(panels.head("Highlights by domain", range_name.lower()),
                    unsafe_allow_html=True)
        if scope_neg.empty:
            st.caption("Nothing in the chosen domains in this range.")
        else:
            blocks = []
            for name in chosen:
                inside = scope_neg[scope_neg["category"].isin(DOMAINS[name])]
                if inside.empty:
                    continue
                worst = inside.sort_values(["severity", "ts"], ascending=[False, False]).head(3)
                items_html = []
                for it in worst.itertuples():
                    head = html_lib.escape(str(it.title or it.text or "")[:120])
                    link = html_lib.escape(str(it.url)) if pd.notna(it.url) else ""
                    rating = "—" if pd.isna(it.severity) else f"{it.severity:.0f}/5"
                    items_html.append(
                        f'<div class="row"><div class="meta">'
                        f'<span class="chip">{rating}</span>'
                        f'<span>{html_lib.escape(str(it.source or ""))}</span></div>'
                        f'<div class="head">'
                        + (f'<a href="{link}" target="_blank">{head}</a>' if link else head)
                        + "</div></div>")
                blocks.append(
                    f'<div class="axn-head dense" style="margin-top:.5rem">'
                    f'<span class="axn-label">{html_lib.escape(name)}</span>'
                    f'<span class="tools"><span class="count">{len(inside):,}</span></span></div>'
                    f'<div class="axn-stream">{"".join(items_html)}</div>')
            st.markdown("".join(blocks), unsafe_allow_html=True)
            st.caption("The three hardest-rated items in each domain, each linked to the "
                       "report it came from.")

    # ── The real alert log ───────────────────────────────────────────────────────
    with t2.container(border=True, height=LOW_H):
        st.markdown(panels.head("Alerts raised", "emails actually sent",
                                count=len(tickets) or None), unsafe_allow_html=True)
        if not len(tickets):
            st.caption("The monitor has not raised an alert yet. A ticket is written when the "
                       f"window passes the {bar:.0%} bar with at least "
                       f"{alert_cfg['min_items']} items in it.")
        else:
            st.dataframe(
                tickets.assign(share=tickets["ratio"] * 100)[
                    ["created", "level", "share", "negative", "total", "status"]],
                hide_index=True, width="stretch", height=300,
                column_config={
                    "created": st.column_config.DatetimeColumn("Raised", format="D MMM, h:mm a"),
                    "level": "Level",
                    # Out of a hundred, not out of one: the format string is applied to the
                    # stored value, so a ratio of 0.67 with a "%.0f%%" format prints "1%".
                    "share": st.column_config.ProgressColumn("Negative share", min_value=0.0,
                                                             max_value=100.0, format="%.0f%%"),
                    "negative": "Negative", "total": "Items", "status": "Email"})
            st.caption("These are the monitor's own records. The day grades in the timeline "
                       "are this page reading the same items again, and the two can disagree "
                       "where the bar has moved since.")

    # ── Where the weight sits ────────────────────────────────────────────────────
    with t3.container(border=True, height=LOW_H):
        st.markdown(panels.head("Domain mix", "share of this range"), unsafe_allow_html=True)
        if scope_neg.empty:
            st.caption("Nothing in the chosen domains in this range.")
        else:
            tally = []
            for name in chosen:
                inside = neg[neg["category"].isin(DOMAINS[name])]
                if not len(inside):
                    continue
                graded = inside[inside["severity"].notna()]
                tally.append((name, len(inside),
                              float(graded["severity"].mean()) if len(graded) else 0.0))
            tally.sort(key=lambda row: -row[1])
            top = max((n for _, n, _ in tally), default=1)
            st.markdown(
                '<div class="axn-bars">' + "".join(
                    f'<div class="bar-row"><span class="name">{html_lib.escape(name)}</span>'
                    f'<span class="track"><i class="fill{" hot" if rating >= 3 else ""}" '
                    f'style="width:{count / top * 100:.0f}%"></i></span>'
                    f'<span class="n">{count:,}</span></div>'
                    for name, count, rating in tally) + "</div>",
                unsafe_allow_html=True)
            st.caption("Bar length is how much, and a violet bar is a domain averaging 3 or "
                       "worse on severity — how much and how bad are different questions and "
                       "one bar cannot answer both.")

if page == "Events":
    # ─── An event's public code ───────────────────────────────────────────────────
    def event_code(frame: pd.DataFrame) -> pd.Series:
        """SAXN-261114-33C229 — the code a reader can quote back at us.

        The database's own id is a twenty-character hash: stable, unique, and useless to say out
        loud. This keeps the first six of it, which is a million to one inside a single day, and
        puts the day in front so that two codes sort the way the events happened. It is derived
        rather than stored, so it needs no migration and never disagrees with the row it names.
        """
        return "SAXN-" + frame["ts"].dt.strftime("%y%m%d") + "-" + frame["id"].str[:6].str.upper()


    # ─── Rating highlights ────────────────────────────────────────────────────────
    # The table below is sorted hardest-first, so its top is always visible and its bottom never
    # is. These two cards are the bottom — the window's mildest graded item next to its harshest —
    # because "what is the least of it" is a real question and scrolling to the end of a thousand
    # rows is not an answer.
    #
    # It reads the window the page is already on rather than a fixed twenty-four hours. Everything
    # else here obeys the range selector, and a block that quietly did not would sit empty every
    # time the monitor fell a day behind, which looks like a fault rather than like a quiet day.
    graded = neg[neg["severity"].notna()]
    HIGH_H = 300
    h1, h2, h3 = st.columns(3)
    if graded.empty:
        with h1.container(border=True, height=HIGH_H):
            st.subheader(f"Rating highlights — {range_name.lower()}")
            st.caption("Nothing graded in this range. The severity rating is only set on negative "
                       "items, and only once the analyzer has read them.")
    else:
        ordered = graded.sort_values(["severity", "ts"], ascending=[False, False])
        for column, row, label in ((h1, ordered.iloc[0], "Most severe"),
                                   (h2, ordered.iloc[-1], "Least severe")):
            headline = (row["title"] or "")[:200] or str(row["text"] or "")[:200]
            with column.container(border=True, height=HIGH_H):
                st.markdown(f'<div class="axn-head dense"><span class="axn-label">{label}</span>'
                            f'<span class="tools"><span class="count">{row["severity"]:.0f} / 5</span>'
                            f'</span></div>', unsafe_allow_html=True)
                st.markdown(f'**{html_lib.escape(headline)}**')
                # `ts` is already a local, tz-naive Timestamp — to_local() takes the ISO
                # strings the database stores, and would choke on this.
                st.caption(" · ".join(filter(None, (
                    event_code(ordered.loc[[row.name]]).iloc[0],
                    f'{row["ts"]:%d %b %Y, %I:%M %p}',
                    countries.label(row["country"]) if pd.notna(row["country"]) else "",
                    str(row["source"] or "")))))
                if row["url"]:
                    st.markdown(f'[open]({row["url"]})')

        # The third panel is what the two cards leave out: the shape of everything between
        # them. Two extremes with no distribution behind them invite the reader to assume the
        # middle is evenly filled, and it never is.
        with h3.container(border=True, height=HIGH_H):
            spread = graded["severity"].value_counts().sort_index(ascending=False)
            st.markdown(panels.head("How the window is graded",
                                    f"{ordered['severity'].mean():.1f} / 5 average"),
                        unsafe_allow_html=True)
            top = int(spread.max()) or 1
            st.markdown(
                '<div class="axn-bars">' + "".join(
                    f'<div class="bar-row"><span class="name">severity {int(level)}</span>'
                    f'<span class="track"><span class="fill{" hot" if level >= 4 else ""}" '
                    f'style="width:{n / top:.0%}"></span></span>'
                    f'<span class="n">{int(n)}</span></div>'
                    for level, n in spread.items()) + "</div>",
                unsafe_allow_html=True)
            st.caption(f"{len(graded):,} graded of {len(neg):,} negative items.")

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
            code=event_code(view),
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
            shown[["code", "ts", "severity", "category_label", "country", "headline", "summary", "credibility",
                   "cred_reason", "factcheck", "factcheck_url", "source", "source_type", "url"]],
            hide_index=True, width="stretch",
            column_config={
                "code": st.column_config.TextColumn(
                    "Code", help="This event's code — quote it in a ticket or an email and it "
                                 "names exactly this item."),
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

if page == "Tickets":
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

