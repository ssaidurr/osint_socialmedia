"""Negative-news analytics dashboard.   Run:  streamlit run dashboard.py"""
from __future__ import annotations

import os

import altair as alt
import pandas as pd
import requests
import streamlit as st

from osint import db
from osint.analyzer import CATEGORIES
from osint.config import TICKETS_DIR, load_config, to_local

# Sentiment is polar: red and blue poles with a gray midpoint. Single-series bars use the red pole.
NEG, NEU, POS = "#e34948", "#a8a7a0", "#2a78d6"
MUTED = "#898781"
SENTIMENTS = ["negative", "neutral", "positive"]
SENTIMENT_SCALE = alt.Scale(domain=SENTIMENTS, range=[NEG, NEU, POS])
RANGES = {"Last 6 hours": 6, "Last 24 hours": 24, "Last 3 days": 72, "Last 7 days": 168, "Last 30 days": 720}

st.set_page_config(page_title="OSINT Negative News Monitor", page_icon="📡", layout="wide")
cfg = load_config()
TZ = cfg.get("timezone", "Asia/Dhaka")
alert_cfg = cfg["alert"]


def _secret(name: str) -> str | None:
    if os.getenv(name):
        return os.getenv(name)
    try:
        return st.secrets.get(name)
    except Exception:  # no secrets.toml when running locally
        return None


# On Streamlit Cloud: read the data the GitHub Actions workflow pushes to the repo's `data` branch.
# Locally (no GITHUB_DATA_REPO set) the dashboard reads data/osint.db directly.
REMOTE_REPO = _secret("GITHUB_DATA_REPO")  # "owner/repo"


@st.cache_data(ttl=300)
def fetch_remote(path: str) -> tuple[bytes | None, int]:
    """Returns (content, HTTP status); content is None unless the file was found. Status 0 = network error."""
    headers = {"Accept": "application/vnd.github.raw+json"}
    if token := _secret("GITHUB_TOKEN"):  # required for private repos
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.get(f"https://api.github.com/repos/{REMOTE_REPO}/contents/{path}", headers=headers,
                         params={"ref": _secret("GITHUB_DATA_BRANCH") or "data"}, timeout=30)
    except requests.RequestException:
        return None, 0
    return (r.content if r.status_code == 200 else None), r.status_code


@st.cache_data(ttl=60)
def load() -> tuple[pd.DataFrame, pd.DataFrame, int]:
    if REMOTE_REPO:
        content, _ = fetch_remote("data/osint.db")
        if content:
            db.DB_PATH.parent.mkdir(exist_ok=True)
            db.DB_PATH.write_bytes(content)
    conn = db.connect()
    collected = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    items = pd.read_sql_query("SELECT * FROM items WHERE sentiment IS NOT NULL", conn)
    tickets = pd.read_sql_query("SELECT * FROM tickets ORDER BY created_at DESC", conn)
    conn.close()
    # Charts get naive local time so the axis reads in Dhaka time regardless of the browser
    # A database written by an older version of the monitor has no credibility columns yet
    for column in ("country", "corroboration", "credibility", "cred_reason", "factcheck_rating",
                   "factcheck_publisher", "factcheck_url"):
        if column not in items.columns:
            items[column] = None
    items["ts"] = pd.to_datetime(items["ts"], utc=True, format="ISO8601").dt.tz_convert(TZ).dt.tz_localize(None)
    items["category_label"] = items["category"].map(CATEGORIES)
    tickets["created"] = pd.to_datetime(tickets["created_at"], utc=True, format="ISO8601").dt.tz_convert(TZ).dt.tz_localize(None)
    return items, tickets, collected


items, tickets, collected = load()
st.title("📡 OSINT Negative News Monitor")

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
st.caption(f"Last collection: {to_local(items['collected_at'].max(), cfg)}"
           + (f" · data from GitHub `{REMOTE_REPO}`" if REMOTE_REPO else ""))

# ─── Filters (one row, above the charts) ──────────────────────────────────────
f1, f2, f3, f4 = st.columns([1, 2, 1.4, 0.6], vertical_alignment="bottom")
range_name = f1.selectbox("Time range", list(RANGES), index=1)
all_types = sorted(items["source_type"].unique())
types = f2.multiselect("Source type", all_types, default=all_types)
countries = f3.multiselect("Country", sorted(items["country"].dropna().unique()),
                           help="The country an item is mainly about, as identified by the analyzer")
if f4.button("↻ Refresh", width="stretch"):
    st.cache_data.clear()
    st.rerun()
search = st.text_input("Search", placeholder="keyword in the headline, text or summary — e.g. election, ডেঙ্গু")

hours = RANGES[range_name]
now = pd.Timestamp.now(tz=TZ).tz_localize(None)
df = items[(items["ts"] >= now - pd.Timedelta(hours=hours)) & items["source_type"].isin(types)]
if countries:
    df = df[df["country"].isin(countries)]
if search.strip():
    haystack = (df["title"].fillna("") + " " + df["text"].fillna("") + " " + df["summary"].fillna("")).str.lower()
    df = df[haystack.str.contains(search.strip().lower(), regex=False)]
neg = df[df["sentiment"] == "negative"]
if df.empty:
    st.warning("Nothing matches these filters. Try a longer time range, or clear the country/search filter.")
    st.stop()

# ─── Alert status (same rule as the ticket check) ─────────────────────────────
win = items[items["ts"] >= now - pd.Timedelta(hours=alert_cfg["window_hours"])]
win_share = (win["sentiment"] == "negative").mean() if len(win) else 0.0
thr = alert_cfg["negative_ratio_threshold"]
status = (f"Last {alert_cfg['window_hours']}h: **{win_share:.0%}** negative "
          f"({(win['sentiment'] == 'negative').sum()}/{len(win)}), alert threshold {thr:.0%}")
if len(win) >= alert_cfg["min_items"] and win_share >= thr:
    st.error(f"Alert level: {status}", icon="🚨")
else:
    st.success(f"Normal: {status}", icon="✅")

# ─── KPIs ─────────────────────────────────────────────────────────────────────
k1, k2, k3, k4 = st.columns(4)
k1.metric("Items analyzed", f"{len(df):,}")
k2.metric("Negative share", f"{len(neg) / len(df):.0%}", help=f"{len(neg)} negative items")
k3.metric("Avg severity (negative)", f"{neg['severity'].mean():.1f} / 5" if len(neg) else "—")
k4.metric("Tickets in range", int((tickets["created"] >= now - pd.Timedelta(hours=hours)).sum()) if len(tickets) else 0)

freq = "h" if hours <= 72 else "D"
time_fmt = "%d %b %H:%M" if freq == "h" else "%d %b"

# ─── Negative news by type + negative share over time ─────────────────────────
c1, c2 = st.columns(2)
with c1:
    st.subheader("কী ধরনের নেগেটিভ নিউজ — by type")
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

with c2:
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
    st.subheader("Negative news type over time")
    heat = neg.assign(bucket=neg["ts"].dt.floor(freq)).groupby(["category_label", "bucket"]).size().reset_index(name="count")
    order = neg["category_label"].value_counts().index.tolist()
    st.altair_chart(
        alt.Chart(heat).mark_rect(cornerRadius=2, stroke=None).encode(
            x=alt.X("bucket:T", title=None, axis=alt.Axis(format=time_fmt)),
            y=alt.Y("category_label:N", sort=order, title=None),
            color=alt.Color("count:Q", title="Items", scale=alt.Scale(scheme="reds")),
            tooltip=[alt.Tooltip("category_label:N", title="Type"),
                     alt.Tooltip("bucket:T", title="Time", format=time_fmt), alt.Tooltip("count:Q", title="Items")],
        ).properties(height=max(160, 30 * len(order))),
        width="stretch",
    )

# ─── Sentiment volume + top negative sources ──────────────────────────────────
c3, c4 = st.columns(2)
with c3:
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
with c4:
    st.subheader("Negative items by country")
    by_country = neg["country"].dropna()
    if by_country.empty:
        st.caption("No country identified for these items yet — run `python main.py analyze` after an update.")
    else:
        cdf = by_country.value_counts().head(12).rename_axis("country").reset_index(name="count")
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

with st.container():
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
st.subheader("Negative items")
t1, t2 = st.columns([3, 1], vertical_alignment="bottom")
cat_filter = t1.multiselect("Filter by type", sorted(neg["category_label"].dropna().unique()))
only_doubtful = t2.checkbox("Only doubtful (credibility < 40)")
view = neg[neg["category_label"].isin(cat_filter)] if cat_filter else neg
if only_doubtful:
    view = view[view["credibility"] < 40]
view = view.assign(
    headline=view["title"].where(view["title"].fillna("") != "", view["text"].str[:160]),
    factcheck=(view["factcheck_rating"].fillna("") + " — " + view["factcheck_publisher"].fillna("")).str.strip(" —"),
)
st.dataframe(
    view.sort_values(["severity", "ts"], ascending=[False, False])[
        ["ts", "severity", "category_label", "country", "headline", "summary", "credibility", "cred_reason",
         "factcheck", "factcheck_url", "source", "source_type", "url", "analyzer"]],
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
        "analyzer": "Analyzer",
    },
)

# ─── Tickets ──────────────────────────────────────────────────────────────────
st.subheader("🎫 Tickets")
if tickets.empty:
    st.caption("No tickets yet. A ticket is raised when the negative share crosses the threshold.")
else:
    st.dataframe(
        tickets[["id", "created", "level", "ratio", "negative", "total", "status", "error"]],
        hide_index=True, width="stretch",
        column_config={"created": st.column_config.DatetimeColumn("Created", format="D MMM, h:mm a"),
                       "ratio": st.column_config.NumberColumn("Negative share", format="percent")},
    )
    pick = st.selectbox("Preview ticket email", tickets["id"])
    if REMOTE_REPO:
        raw, _ = fetch_remote(f"data/tickets/{pick}.html")
        report = raw.decode("utf-8") if raw else None
    else:
        local = TICKETS_DIR / f"{pick}.html"
        report = local.read_text(encoding="utf-8") if local.exists() else None
    if report:
        # st.html sanitizes; the report holds headlines and links scraped from the internet
        with st.container(border=True):
            st.html(report)
