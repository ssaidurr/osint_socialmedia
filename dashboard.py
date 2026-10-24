"""Negative-news analytics dashboard.   Run:  streamlit run dashboard.py"""
from __future__ import annotations

import difflib
import gzip
import html as html_lib
import os
import re

import altair as alt
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

from osint import countries, db, gate
from osint.analyzer import CATEGORIES
from osint.config import TICKETS_DIR, load_config, to_local

# The SynergyAXN palette (see .streamlit/config.toml). Positive takes the brand accent rather
# than the brand green: a red/green pair is the one combination colour-blind readers lose, and
# cyan sits clearly apart from the negative red.
NEG, NEU, POS = "#f87171", "#8a919c", "#5ee6ff"
MUTED = "#8a919c"
SURFACE = "#0b0d10"
# A red ramp for "how much negative", bright enough to read on the near-black surface
HEAT = ["#6a3537", "#823f41", "#99494a", "#b15354", "#c95d5e", "#e06767", "#f87171"]
SENTIMENTS = ["negative", "neutral", "positive"]
SENTIMENT_SCALE = alt.Scale(domain=SENTIMENTS, range=[NEG, NEU, POS])
# The ranges stop where the data does — see retention_days in config.yaml
RANGES = {"Last 6 hours": 6, "Last 24 hours": 24, "Last 3 days": 72, "Last 7 days": 168, "Last 14 days": 336}

st.set_page_config(page_title="News Monitor", page_icon="📡", layout="wide")
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


@st.cache_data(ttl=300, show_spinner=False)
def fetch_remote(path: str, version: str = "") -> tuple[bytes | None, int]:
    """Returns (content, HTTP status); content is None unless the file was found. Status 0 = network error.

    `version` is never read — it is in the signature so that a new data version misses the cache.
    """
    headers = {"Accept": "application/vnd.github.raw+json"}
    if token := _secret("GITHUB_TOKEN"):  # required for private repos
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.get(f"https://api.github.com/repos/{REMOTE_REPO}/contents/{path}", headers=headers,
                         params={"ref": _secret("GITHUB_DATA_BRANCH") or "data"}, timeout=30)
    except requests.RequestException:
        return None, 0
    return (r.content if r.status_code == 200 else None), r.status_code


@st.cache_data(ttl=300, show_spinner=False)
def data_version() -> str:
    """A few hundred bytes that change only when the monitor publishes.

    The database is ~20 MB over the wire, and the monitor writes it once an hour — polling the
    branch head instead means a refresh costs one small request unless the data actually moved.
    """
    if not REMOTE_REPO:
        return str(db.DB_PATH.stat().st_mtime_ns) if db.DB_PATH.exists() else "none"
    headers = {"Accept": "application/vnd.github+json"}
    if token := _secret("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    branch = _secret("GITHUB_DATA_BRANCH") or "data"
    try:
        r = requests.get(f"https://api.github.com/repos/{REMOTE_REPO}/commits/{branch}",
                         headers=headers, timeout=15)
        if r.status_code == 200:
            return r.json().get("sha", "unknown")
    except (requests.RequestException, ValueError):
        pass
    return "unknown"          # fall through to a normal fetch rather than block the dashboard


@st.cache_data(ttl=3600, show_spinner="Loading the latest collection…")
def load(version: str) -> tuple[pd.DataFrame, pd.DataFrame, int, dict]:
    if REMOTE_REPO:
        # The monitor stores the database gzipped (a third of the size over the wire)
        content, _ = fetch_remote("data/osint.db.gz", version)
        if content:
            content = gzip.decompress(content)
        else:
            content, _ = fetch_remote("data/osint.db", version)   # branches written before that change
        if content:
            db.DB_PATH.parent.mkdir(exist_ok=True)
            db.DB_PATH.write_bytes(content)
    conn = db.connect()
    collected = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    items = pd.read_sql_query("SELECT * FROM items WHERE sentiment IS NOT NULL", conn)
    tickets = pd.read_sql_query("SELECT * FROM tickets ORDER BY created_at DESC", conn)
    briefs = {int(r["window_hours"]): dict(r) for r in conn.execute("SELECT * FROM digests")}
    conn.close()
    # Charts get naive local time so the axis reads in Dhaka time regardless of the browser
    # A database written by an older version of the monitor has no credibility columns yet
    for column in ("country", "corroboration", "credibility", "cred_reason", "factcheck_rating",
                   "factcheck_publisher", "factcheck_url"):
        if column not in items.columns:
            items[column] = None
    # Never more than ~160 characters of body text are shown; the rest only cost memory and
    # went through the cache pickle on every miss (~11 MB of it).
    items["text"] = items["text"].str.slice(0, 300)
    items["ts"] = pd.to_datetime(items["ts"], utc=True, format="ISO8601").dt.tz_convert(TZ).dt.tz_localize(None)
    items["category_label"] = items["category"].map(CATEGORIES)
    tickets["created"] = pd.to_datetime(tickets["created_at"], utc=True, format="ISO8601").dt.tz_convert(TZ).dt.tz_localize(None)
    return items, tickets, collected, briefs


# The brand mark and wordmark are the ones the SynergyAXN site uses in its own header, so the
# two read as one product. Geist carries no Bangla glyphs, hence the Bengali fallback below —
# some headlines arrive in Bangla and must not land in a default serif.
BRAND = """
<style>
  html, body, [class*="st-"], .stMarkdown, .stDataFrame {
    font-family: "Geist", "Noto Sans Bengali", system-ui, -apple-system, sans-serif;
  }
  .brand { display: flex; align-items: center; gap: .5rem; margin: 0 0 .15rem; color: #eceef1; }
  .brand-name { font-size: 1.02rem; font-weight: 500; letter-spacing: -.02em; }
  .brand-dim { color: #8a919c; }


  /* A live pulse on the collection time — it beats while the data is fresh and goes amber
     when the monitor has not reported in a while. */
  .live { display: inline-flex; align-items: center; gap: .45rem; color: #8a919c; font-size: .85rem; }
  .live-dot { width: 7px; height: 7px; border-radius: 50%; background: #5ee6ff; position: relative; flex: none; }
  .live-dot::after {
    content: ""; position: absolute; inset: -4px; border-radius: 50%; border: 1px solid #5ee6ff;
    animation: axn-pulse 2.4s cubic-bezier(.2, .6, .3, 1) infinite;
  }
  .live-dot.stale, .live-dot.stale::after { background: #ffd236; border-color: #ffd236; }
  @keyframes axn-pulse {
    0%   { transform: scale(.5); opacity: .9; }
    70%  { transform: scale(1.7); opacity: 0; }
    100% { transform: scale(1.7); opacity: 0; }
  }

  /* Streamlit stacks the page's rows 16px apart; the panels need more air than that. */
  [data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] { gap: 1.75rem; }

  /* Every section sits on its own panel, so where one ends and the next begins is plain.
     The three selectors are the three shapes a section takes: one that opens with a subheader,
     a KPI tile, and the brief (an h4). `.brand` only exists in the page-level block — excluding
     it keeps the whole page from being painted as one giant card. */
  [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] h3),
  [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] > [data-testid="stMetric"]),
  [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] h4),
  [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .axn-head) {
    background: linear-gradient(180deg, #13171d 0%, #0c0f13 46%, #0a0c10 100%);
    border-color: #262c34 !important;
    border-radius: 1rem;
    padding: 1.2rem 1.35rem;
    box-shadow:
      0 1px 0 0 #ffffff14 inset,      /* the lit top edge: where the light would fall */
      0 18px 34px -28px #000000e6,    /* contact shadow, close under the panel */
      0 44px 80px -60px #000;         /* and the soft one that lifts it off the page */
  }

  /* Section titles in the site's own micro-label: Geist Mono, uppercase, wide-tracked, muted,
     with a cyan rule down the left so the eye finds the next section while scrolling. */
  [data-testid="stHeading"] h3 {
    font-family: "Geist Mono", ui-monospace, SFMono-Regular, monospace;
    font-size: .72rem; font-weight: 400; letter-spacing: .16em; text-transform: uppercase;
    color: #8a919c;
    padding-left: .55rem; border-left: 2px solid #5ee6ff;
    line-height: 1.2; margin-bottom: .95rem;
  }
  [data-testid="stMarkdownContainer"] h4 {
    padding-left: .6rem; border-left: 3px solid #5ee6ff; line-height: 1.25; margin-bottom: .5rem;
  }
  [data-testid="stMetricValue"] { font-family: "Geist Mono", ui-monospace, monospace; }
  [data-testid="stMetricLabel"] p {
    font-family: "Geist Mono", ui-monospace, monospace;
    font-size: .68rem; letter-spacing: .14em; text-transform: uppercase; color: #8a919c;
  }

  /* ── AXN Pulse panels ─────────────────────────────────────────────────────── */
  .axn-head { display: flex; align-items: center; gap: .45rem; margin-bottom: .55rem; }
  .axn-label {
    font-family: "Geist Mono", ui-monospace, monospace;
    font-size: .72rem; letter-spacing: .16em; text-transform: uppercase; color: #8a919c;
  }
  .axn-head .right { margin-left: auto; }

  .axn-stream .row { padding: .78rem .35rem; border-top: 1px solid #191b1d; }
  .axn-stream .row:first-child { border-top: 0; padding-top: .3rem; }
  .axn-stream .meta {
    display: flex; align-items: center; gap: .45rem; margin-bottom: .22rem;
    font-family: "Geist Mono", ui-monospace, monospace; font-size: .7rem; color: #8a919c;
  }
  .axn-stream .chip { border: 1px solid #23272e; border-radius: 4px; padding: .02rem .3rem; color: #a9b0ba; }
  .axn-stream .score { margin-left: auto; border-radius: 5px; padding: .05rem .38rem; letter-spacing: .04em; }
  .axn-stream .score.neg { background: #f871711f; color: #f87171; }
  .axn-stream .score.pos { background: #5ee6ff1f; color: #5ee6ff; }
  .axn-stream .score.neu { background: #8a919c1f; color: #8a919c; }
  .axn-stream .head { color: #eceef1; font-size: .9rem; line-height: 1.35; }

  .axn-index { font-family: "Geist Mono", ui-monospace, monospace; font-size: 2.3rem; color: #eceef1; line-height: 1.1; }
  .axn-index.down { color: #f87171; }

  .axn-bars .bar-row { display: flex; align-items: center; gap: .7rem; padding: .45rem 0; font-size: .83rem; }
  .axn-bars .name { width: 120px; flex: none; color: #c6ccd4; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .axn-bars .track { flex: 1; height: 6px; border-radius: 3px; background: #1b1f25; overflow: hidden; }
  .axn-bars .fill { display: block; height: 100%; border-radius: 3px; background: #5ee6ff; }
  .axn-bars .fill.hot { background: #8b7cff; }
  .axn-bars .n { width: 38px; text-align: right; color: #8a919c;
                 font-family: "Geist Mono", ui-monospace, monospace; font-size: .75rem; }

  /* ── Motion ───────────────────────────────────────────────────────────────
     The panels arrive with a slight 3D lift as they scroll into view, tilt a little
     towards the pointer, and take one cyan sweep along their top edge. Everything here
     is keyed off `.axn-motion`, a class the companion script sets on <html> — so if the
     script never runs, nothing is hidden and the page just sits still. */
  @property --axn-angle { syntax: "<angle>"; initial-value: 0deg; inherits: false; }

  .axn-card { position: relative; }
  .axn-motion .axn-card {
    transform: perspective(1000px) rotateX(var(--rx, 0deg)) rotateY(var(--ry, 0deg))
               translate3d(0, var(--ty, 0px), 0);
    transition: transform .4s cubic-bezier(.2, .7, .2, 1) var(--d, 0ms),
                opacity .6s ease var(--d, 0ms), box-shadow .4s ease, border-color .4s ease;
  }
  .axn-motion .axn-card:hover { will-change: transform; }
  /* Panels arrive tipped away from the reader and swing level */
  .axn-motion .axn-card:not(.axn-in) { opacity: 0; --ty: 26px; --rx: 7deg; }
  .axn-motion .axn-card.axn-in { opacity: 1; }
  .axn-motion .axn-card:hover {
    --ty: -4px;
    border-color: #34505d !important;
    box-shadow:
      0 1px 0 0 #ffffff1f inset,
      0 28px 52px -30px #000,
      0 0 70px -34px #5ee6ff66;      /* the panel throws a little of its own light */
  }

  /* Two lights live in the 1px band between a panel's edge and its content. The mask knocks
     the middle out, so neither of them can ever land on something being read: one follows the
     pointer across the edge, the other runs a lap around it while the pointer is on the panel. */
  .axn-card::before, .axn-card::after {
    content: ""; position: absolute; inset: -1px; border-radius: inherit; padding: 1px;
    pointer-events: none; opacity: 0; transition: opacity .35s ease;
    -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
    -webkit-mask-composite: xor;
    mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
    mask-composite: exclude;
  }
  .axn-card::before {
    background: radial-gradient(260px circle at var(--mx, 50%) var(--my, 50%), #5ee6ff, transparent 55%);
  }
  .axn-card::after {
    background: conic-gradient(from var(--axn-angle), transparent 58%, #5ee6ff 76%,
                               #8b7cff 88%, transparent 97%);
    animation: axn-orbit 4.5s linear infinite paused;   /* only burns a frame while hovered */
  }
  .axn-motion .axn-card:hover::before { opacity: .85; }
  .axn-motion .axn-card:hover::after { opacity: .7; animation-play-state: running; }
  @keyframes axn-orbit { to { --axn-angle: 360deg; } }

  /* The title lifts with the panel */
  .axn-motion .axn-card:hover [data-testid="stHeading"] h3,
  .axn-motion .axn-card:hover .axn-label { color: #c6ccd4; transition: color .35s ease; }

  /* Each event slides in behind the one above it, so the feed reads as a feed. */
  .axn-stream .row { animation: axn-row-in .5s cubic-bezier(.2, .8, .2, 1) both; border-radius: 7px; }
  .axn-stream .row:hover { background: #ffffff06; }
  @keyframes axn-row-in {
    from { opacity: 0; transform: translate3d(-10px, 0, 0); }
    to   { opacity: 1; transform: none; }
  }
  .axn-bars .fill { animation: axn-grow .9s cubic-bezier(.2, .8, .2, 1) both; }
  @keyframes axn-grow { from { width: 0; } }
  .axn-index { animation: axn-index-in .7s cubic-bezier(.2, .8, .2, 1) both; }
  @keyframes axn-index-in {
    from { opacity: 0; transform: translate3d(0, 8px, 0) scale(.97); }
    to   { opacity: 1; transform: none; }
  }

  /* Anyone who asks their system for less motion gets none of it. */
  @media (prefers-reduced-motion: reduce) {
    .axn-stream .row, .axn-bars .fill, .axn-index { animation: none; }
    .axn-card::after { animation: none; }
    .axn-motion .axn-card { transition: none; transform: none; opacity: 1; }
    .live-dot::after { animation: none; opacity: .35; }
  }
</style>
<div class="brand">
  <svg width="22" height="22" viewBox="0 0 32 32" fill="none" aria-hidden="true">
    <circle cx="16" cy="16" r="14.5" stroke="currentColor" stroke-opacity="0.35"></circle>
    <path d="M6 22 L16 6 L26 22" stroke="currentColor" stroke-width="2" stroke-linejoin="round"></path>
    <path d="M10 26 L22 14" stroke="#5ee6ff" stroke-width="2" stroke-linecap="round"></path>
    <circle cx="16" cy="16" r="2.2" fill="#5ee6ff"></circle>
  </svg>
  <span class="brand-name">Synergy<span class="brand-dim">AXN</span></span>
</div>
"""
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


# ─── Motion companion ─────────────────────────────────────────────────────────
# Streamlit strips <script> from markdown, so the few lines that CSS cannot do on its own —
# reveal a panel when it scrolls into view, tilt it towards the pointer — ride in a zero-height
# component iframe and reach back into the page. Progressive enhancement: the `axn-motion`
# class is what arms the CSS, so if this never runs the page is simply still.
components.html("""
<script>
(function () {
  let doc;
  try { doc = window.parent && window.parent.document; } catch (e) { return; }  // cross-origin: stay still
  if (!doc || doc === document) return;
  if (window.parent.matchMedia && window.parent.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  doc.documentElement.classList.add("axn-motion");

  const scroller = doc.querySelector('[data-testid="stMain"]') || doc.querySelector(".stMain");
  const io = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry, i) {
      if (!entry.isIntersecting) return;
      const el = entry.target;
      el.style.setProperty("--d", Math.min(i, 6) * 70 + "ms");   // a cascade, not a stampede
      el.classList.add("axn-in");
      setTimeout(function () { el.style.removeProperty("--d"); }, 1000);  // tilt must not inherit the delay
      io.unobserve(el);
    });
  }, { root: scroller || null, threshold: 0.06 });

  function scan() {
    doc.querySelectorAll('[data-testid="stVerticalBlock"]').forEach(function (el) {
      // A bordered block is a panel; everything else is layout plumbing
      if (el.classList.contains("axn-card")) return;
      if (parseFloat(getComputedStyle(el).borderTopWidth) < 1) return;
      el.classList.add("axn-card");
      io.observe(el);
      // Failsafe: a panel is never allowed to stay invisible because the observer did not
      // fire (a different scroll container, a browser quirk) — the reveal is a nicety.
      setTimeout(function () { el.classList.add("axn-in"); }, 8000);
    });
  }

  let active = null, rect = null, queued = false, mx = 0, my = 0;
  function clear() {
    if (!active) return;
    ["--rx", "--ry", "--mx", "--my"].forEach(function (v) { active.style.removeProperty(v); });
    active = null; rect = null;
  }
  doc.addEventListener("pointermove", function (e) {
    const card = e.target && e.target.closest ? e.target.closest(".axn-card") : null;
    if (card !== active) {
      clear();
      // A tall panel (a table, the ticket list) tilts into nonsense and costs a huge repaint
      if (card && card.getBoundingClientRect().height <= 900) { active = card; rect = card.getBoundingClientRect(); }
    }
    if (!active) return;
    mx = e.clientX; my = e.clientY;
    if (queued) return;                       // one write per frame, no layout read per event
    queued = true;
    window.parent.requestAnimationFrame(function () {
      queued = false;
      if (!active || !rect) return;
      const px = (mx - rect.left) / rect.width, py = (my - rect.top) / rect.height;
      active.style.setProperty("--ry", ((px - 0.5) * 3.4).toFixed(2) + "deg");
      active.style.setProperty("--rx", ((0.5 - py) * 2.6).toFixed(2) + "deg");
      // where the edge light sits, so it tracks the pointer along the border
      active.style.setProperty("--mx", (px * 100).toFixed(1) + "%");
      active.style.setProperty("--my", (py * 100).toFixed(1) + "%");
    });
  }, { passive: true });
  (scroller || doc).addEventListener("scroll", clear, { passive: true });   // a cached rect goes stale on scroll

  let pending;
  const watched = doc.querySelector('[data-testid="stMainBlockContainer"]') || doc.body;
  new MutationObserver(function () {        // Streamlit rebuilds the DOM on every rerun
    clearTimeout(pending);
    pending = setTimeout(scan, 250);
  }).observe(watched, { childList: true, subtree: true });
  scan();
})();
</script>
""", height=0)
