"""Negative-news analytics dashboard.   Run:  streamlit run dashboard.py"""
from __future__ import annotations

import difflib
import html as html_lib
import re

import altair as alt
import pandas as pd
import streamlit as st

from osint import countries, gate
from osint.config import TICKETS_DIR, load_config, to_local
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
f1, f2, f3, f4 = st.columns([1, 2, 1.4, 0.6], vertical_alignment="bottom")
range_name = f1.selectbox("Time range", list(RANGES), index=1)
all_types = sorted(items["source_type"].unique())
types = f2.multiselect("Source type", all_types, default=all_types)
country_options = sorted(items["country"].dropna().unique(), key=countries.label)
picked_countries = f3.multiselect("Country", country_options, disabled=not country_options,
                                  format_func=countries.label,
                                  help="The country an item is mainly about, as identified by the analyzer")
if not country_options:
    f3.caption("No country data yet — it is filled on the next monitor run.")
if f4.button("↻ Refresh", width="stretch"):
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
