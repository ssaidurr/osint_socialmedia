"""What both pages of the dashboard share: the theme, the palette, and the data.

Keeping the loader here means the globe page and the overview page hit the same Streamlit
cache rather than each pulling their own copy of the database.
"""
from __future__ import annotations

import gzip
import os

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

from osint import db
from osint.analyzer import CATEGORIES
from osint.config import load_config

TZ = load_config().get("timezone", "Asia/Dhaka")

# The SynergyAXN palette (see .streamlit/config.toml). Positive takes the brand accent rather
# than the brand green: a red/green pair is the one combination colour-blind readers lose, and
# cyan sits clearly apart from the negative red.
NEG, NEU, POS = "#f87171", "#8a919c", "#5ee6ff"
MUTED = "#8a919c"
SURFACE = "#0b0d10"
# A red ramp for "how much negative", bright enough to read on the near-black surface
HEAT = ["#6a3537", "#823f41", "#99494a", "#b15354", "#c95d5e", "#e06767", "#f87171"]
SENTIMENTS = ["negative", "neutral", "positive"]

# The ranges stop where the data does — see retention_days in config.yaml
RANGES = {"Last 6 hours": 6, "Last 24 hours": 24, "Last 3 days": 72, "Last 7 days": 168, "Last 14 days": 336}


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
  /* …except the icon spans, which carry st-… classes too and draw their glyphs from a
     ligature font. Without this they print their own names ("double_arrow_right"). */
  [data-testid="stIconMaterial"], .material-symbols-rounded, span[class*="material-symbols"] {
    font-family: "Material Symbols Rounded" !important;
  }
  /* The world map is still being shown around: reachable at /World_map, not yet advertised.
     Delete this rule to put it back in the sidebar. */
  [data-testid="stSidebarNav"] a[href$="/World_map"] { display: none; }

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

  /* ── Panel chrome ─────────────────────────────────────────────────────────
     A panel's top line carries more than its name: how fresh it is, how much it holds, and
     the way out. Keeping all of it on one row is what lets the cards sit this close together
     without the page turning into a wall of headings. */
  .axn-head { flex-wrap: nowrap; }
  /* The title gives way before the chips do: "Infrastructure here" can lose its tail, but a
     status that wraps onto a second line stops looking like a status. */
  .axn-head > .axn-label:first-child {
    min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .axn-head .tools {
    margin-left: auto; display: flex; align-items: center; gap: .4rem;
    flex-wrap: nowrap; flex: none;
  }
  .axn-head .tools .axn-label { white-space: nowrap; }
  .axn-head .tip {
    display: inline-flex; align-items: center; justify-content: center;
    width: 14px; height: 14px; border: 1px solid #2a3038; border-radius: 50%;
    font-family: "Geist Mono", ui-monospace, monospace; font-size: .58rem; color: #5c636d;
    cursor: help; flex: none;
  }
  .axn-head .tip:hover { color: #5ee6ff; border-color: #5ee6ff66; }
  .axn-head .chip {
    display: inline-flex; align-items: center; gap: .3rem;
    font-family: "Geist Mono", ui-monospace, monospace; font-size: .58rem;
    letter-spacing: .12em; text-transform: uppercase; white-space: nowrap;
    border: 1px solid #23272e; border-radius: 999px; padding: .1rem .42rem; color: #8a919c;
  }
  .axn-head .chip i {
    width: 5px; height: 5px; border-radius: 50%; background: currentColor; flex: none;
  }
  .axn-head .chip.live { color: #5ee6ff; border-color: #5ee6ff33; }
  .axn-head .chip.live i { animation: axn-blip 2.4s ease-in-out infinite; }
  .axn-head .chip.cached { color: #ffd236; border-color: #ffd23633; }
  .axn-head .chip.off { color: #5c636d; }
  @keyframes axn-blip { 0%, 100% { opacity: 1; } 50% { opacity: .25; } }
  .axn-head .count {
    font-family: "Geist Mono", ui-monospace, monospace; font-size: .6rem; color: #c6ccd4;
    background: #1b1f25; border-radius: 999px; padding: .08rem .4rem; min-width: 1.1rem;
    text-align: center;
  }
  .axn-head .shut {
    color: #5c636d; text-decoration: none; font-size: .85rem; line-height: 1;
    padding: 0 .1rem;
  }
  .axn-head .shut:hover { color: #f87171; }

  /* The grid panels run denser than the page's full-width sections: the whole point of the
     stack is to get a lot of readings in front of the eye at once. */
  [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .axn-head.dense) {
    padding: .8rem .9rem .7rem !important;
    border-radius: .8rem !important;
  }
  .axn-head.dense { margin-bottom: .45rem; }
  .axn-head.dense .axn-label { font-size: .64rem; }

  /* The way in: a dashed slot at the end of the grid, the way an empty one should look. The
     tile is the button — two separate things to look at and press would be one too many. */
  [data-testid="stPopover"] button {
    background: transparent !important;
    border: 1px dashed #2a3038 !important; border-radius: .8rem !important;
    padding: 1.5rem 1rem !important; width: 100%;
    color: #5c636d !important;
    font-family: "Geist Mono", ui-monospace, monospace; font-size: .64rem;
    letter-spacing: .14em; text-transform: uppercase;
  }
  [data-testid="stPopover"] button:hover {
    color: #5ee6ff !important; border-color: #5ee6ff55 !important;
  }

  /* The channel and region strips. A radio group, dressed as the row of tabs a television
     remote would have: the thing being chosen is one of a dozen, and a dropdown hides eleven
     of them behind a click. Scoped by widget key so no other radio on the page is touched. */
  .st-key-axn_tv [role="radiogroup"],
  .st-key-axn_intel [role="radiogroup"],
  .st-key-axn_metal [role="radiogroup"],
  .st-key-pick_group [role="radiogroup"],
  .st-key-axn_cam_region [role="radiogroup"] {
    display: flex; flex-wrap: wrap; gap: .25rem; margin-bottom: .5rem;
  }
  .st-key-axn_tv [role="radiogroup"] label,
  .st-key-axn_intel [role="radiogroup"],
  .st-key-axn_metal [role="radiogroup"],
  .st-key-pick_group [role="radiogroup"] label,
  .st-key-axn_cam_region [role="radiogroup"] label {
    background: #12151a; border: 1px solid #23272e; border-radius: 5px;
    padding: .15rem .45rem; margin: 0; min-height: 0; cursor: pointer;
  }
  .st-key-axn_tv [role="radiogroup"] label > div:first-child,
  .st-key-axn_intel [role="radiogroup"],
  .st-key-axn_metal [role="radiogroup"],
  .st-key-pick_group [role="radiogroup"] label > div:first-child,
  .st-key-axn_cam_region [role="radiogroup"] label > div:first-child {
    display: none;  /* the dot: the chip already says which one is on */
  }
  .st-key-axn_tv [role="radiogroup"] label p,
  .st-key-axn_intel [role="radiogroup"],
  .st-key-axn_metal [role="radiogroup"],
  .st-key-pick_group [role="radiogroup"] label p,
  .st-key-axn_cam_region [role="radiogroup"] label p {
    font-family: "Geist Mono", ui-monospace, monospace; font-size: .55rem;
    letter-spacing: .1em; text-transform: uppercase; color: #8a919c; margin: 0;
  }
  .st-key-axn_tv [role="radiogroup"] label:hover p,
  .st-key-axn_intel [role="radiogroup"],
  .st-key-axn_metal [role="radiogroup"],
  .st-key-pick_group [role="radiogroup"] label:hover p,
  .st-key-axn_cam_region [role="radiogroup"] label:hover p { color: #e6e9ee; }
  .st-key-axn_tv [role="radiogroup"] label:has(input:checked),
  .st-key-axn_intel [role="radiogroup"],
  .st-key-axn_metal [role="radiogroup"],
  .st-key-pick_group [role="radiogroup"] label:has(input:checked),
  .st-key-axn_cam_region [role="radiogroup"] label:has(input:checked) {
    background: #f8717118; border-color: #f8717155;
  }
  .st-key-axn_tv [role="radiogroup"] label:has(input:checked) p,
  .st-key-axn_intel [role="radiogroup"],
  .st-key-axn_metal [role="radiogroup"],
  .st-key-pick_group [role="radiogroup"] label:has(input:checked) p,
  .st-key-axn_cam_region [role="radiogroup"] label:has(input:checked) p {
    color: #f87171;
  }

  /* The wall of cameras. Pictures first and captions over them, because the whole point of a
     webcam is the picture — a list of place names would be a worse version of the map. Any
     tile opens where it stands: :target does it with no rerun, and nothing navigates away. */
  .axn-cams { display: grid; grid-template-columns: repeat(2, 1fr); gap: .35rem; }
  .axn-cams .cam {
    position: relative; display: block; border-radius: .5rem; overflow: hidden;
    border: 1px solid #23272e; line-height: 0;
  }
  .axn-cams .cam:hover { border-color: #5ee6ff55; }
  .axn-cams .cam img { width: 100%; height: 86px; object-fit: cover; display: block; }
  .axn-cams .cam .open { position: absolute; inset: 0; z-index: 3; }
  .axn-cams .cam .shut {
    display: none; position: absolute; top: .2rem; right: .35rem; z-index: 4;
    font: 500 .9rem/1 "Geist Mono", ui-monospace, monospace; color: #e6e9ee;
    text-shadow: 0 1px 3px #000; text-decoration: none;
  }
  .axn-cams .cam .shut:hover { color: #f87171; }
  .axn-cams .cam .who, .axn-cams .cam .where {
    position: absolute; left: .3rem; right: .3rem; display: block;
    font-family: "Geist Mono", ui-monospace, monospace; letter-spacing: .06em;
    text-overflow: ellipsis; overflow: hidden; white-space: nowrap; line-height: 1.3;
    z-index: 2;  /* above the gradient below, which is painted after them */
  }
  .axn-cams .cam .who { bottom: .85rem; font-size: .55rem; color: #e6e9ee; }
  .axn-cams .cam .where {
    bottom: .2rem; font-size: .5rem; text-transform: uppercase; color: #8a919c;
  }
  .axn-cams .cam::after {
    content: ""; position: absolute; inset: 0;
    background: linear-gradient(to top, #0b0e12dd 0%, #0b0e1200 45%);
  }
  /* The one that was clicked: full width, tall enough to read, and its own way back */
  .axn-cams .cam:target { grid-column: 1 / -1; border-color: #5ee6ff55; }
  .axn-cams .cam:target img { height: 240px; }
  .axn-cams .cam:target .open { display: none; }
  .axn-cams .cam:target .shut { display: block; }

  /* Headlines as they come off the wire. The bar down the left is severity: at a glance you
     want to know whether to read the list at all, and colour says that faster than a number. */
  .axn-wire { display: flex; flex-direction: column; gap: .3rem; margin-top: .5rem; }
  .axn-wire .item {
    border-left: 2px solid #2a3038; padding: .15rem .5rem; background: #0f1217;
    border-radius: 0 .3rem .3rem 0;
  }
  .axn-wire .item.warn { border-left-color: #ffd236; }
  .axn-wire .item.bad { border-left-color: #f87171; }
  .axn-wire .meta {
    display: flex; justify-content: space-between; gap: .5rem;
    font-family: "Geist Mono", ui-monospace, monospace; font-size: .5rem;
    letter-spacing: .1em; text-transform: uppercase; color: #5c636d;
  }
  .axn-wire .item a, .axn-wire .item > span {
    display: block; font-size: .72rem; line-height: 1.35; color: #e6e9ee;
    text-decoration: none;
  }
  .axn-wire .item a:hover { color: #5ee6ff; }
  .axn-tiles .v.small { font-size: .8rem; }

  /* The panel chooser. A wall of boxes rather than a dropdown, because the question is
     "which of these is on" and a dropdown answers it by hiding twenty of them. */
  [data-testid="stPopoverBody"] { padding-top: .5rem; }
  [data-testid="stPopoverBody"] [data-testid="stCheckbox"] > label {
    display: flex; align-items: center; width: 100%;
    background: #12151a; border: 1px solid #23272e; border-radius: .4rem;
    padding: .4rem .5rem; margin: 0; cursor: pointer;
  }
  [data-testid="stPopoverBody"] [data-testid="stCheckbox"] > label:hover {
    border-color: #5ee6ff55;
  }
  [data-testid="stPopoverBody"] [data-testid="stCheckbox"] > label:has(input:checked) {
    background: #5ee6ff12; border-color: #5ee6ff66;
  }
  [data-testid="stPopoverBody"] [data-testid="stCheckbox"] p {
    font-family: "Geist Mono", ui-monospace, monospace; font-size: .58rem;
    letter-spacing: .08em; text-transform: uppercase; color: #8a919c; margin: 0;
  }
  [data-testid="stPopoverBody"] [data-testid="stCheckbox"] > label:has(input:checked) p {
    color: #e6e9ee;
  }
  [data-testid="stPopoverBody"] [data-testid="stTextInput"] input {
    font-family: "Geist Mono", ui-monospace, monospace; font-size: .62rem;
    letter-spacing: .06em;
  }

  /* Row badges: what a headline's provenance looks like at a glance. */
  .axn-tags { display: flex; flex-wrap: wrap; gap: .25rem; margin-top: .3rem; }
  .axn-tags span {
    font-family: "Geist Mono", ui-monospace, monospace; font-size: .55rem;
    letter-spacing: .1em; text-transform: uppercase; white-space: nowrap;
    border: 1px solid #23272e; border-radius: 4px; padding: .05rem .3rem; color: #8a919c;
  }
  .axn-tags .cred.high { color: #5ee6ff; border-color: #5ee6ff33; }
  .axn-tags .cred.mid { color: #ffd236; border-color: #ffd23633; }
  .axn-tags .cred.low { color: #f87171; border-color: #f8717133; }
  .axn-tags .warn { color: #ffd236; border-color: #ffd23633; }
  .axn-tags .alert { color: #f87171; border-color: #f8717133; }

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

  /* Live context — the weather where the story is, and the local money. It carries the same
     weight as the figures above it, and lights its edge once on arrival so it is seen. */
  .axn-context { display: flex; gap: .6rem; margin: .1rem 0 1rem; }
  .axn-context > div {
    flex: 1; min-width: 0; background: #11141a; border: 1px solid #262c34;
    border-left: 2px solid #5ee6ff; border-radius: .6rem; padding: .5rem .65rem;
    animation: axn-context-in .9s ease both;
  }
  .axn-context .axn-label { display: block; margin-bottom: .2rem; }
  .axn-context .v {
    display: block; font-family: "Geist Mono", ui-monospace, monospace;
    font-size: 1.15rem; color: #eceef1; line-height: 1.25; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis;
  }
  .axn-context .v em { font-style: normal; font-size: .78rem; color: #a9b0ba; }
  .axn-context .sub { display: block; font-size: .7rem; color: #8a919c; margin-top: .1rem; }
  @keyframes axn-context-in {
    from { opacity: 0; transform: translate3d(0, 6px, 0); box-shadow: 0 0 0 0 #5ee6ff00; }
    45%  { box-shadow: 0 0 22px -6px #5ee6ff88; }
    to   { opacity: 1; transform: none; box-shadow: 0 0 0 0 #5ee6ff00; }
  }
  .axn-stats { display: flex; gap: 1.8rem; margin: .2rem 0 1rem; }
  .axn-stats .axn-label { display: block; margin-bottom: .15rem; }
  .axn-stats .v { display: block; font-family: "Geist Mono", ui-monospace, monospace;
                  font-size: 1.5rem; color: #eceef1; line-height: 1.25; }
  .axn-index { font-family: "Geist Mono", ui-monospace, monospace; font-size: 2.3rem; color: #eceef1; line-height: 1.1; }
  .axn-index.down { color: #f87171; }

  /* A grid of readings — a market tile, a macro gauge, a count. They wrap on their own so the
     same block works in a half-width panel and a full-width one. */
  .axn-tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(134px, 1fr)); gap: .55rem; }
  .axn-tiles > div {
    border: 1px solid #23272e; border-radius: .7rem; padding: .55rem .65rem .5rem;
    background: #0e1216; position: relative; overflow: hidden;
  }
  .axn-tiles .t {
    font-family: "Geist Mono", ui-monospace, monospace; font-size: .62rem; letter-spacing: .12em;
    text-transform: uppercase; color: #8a919c; display: block; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis;
  }
  .axn-tiles .v {
    font-family: "Geist Mono", ui-monospace, monospace; font-size: 1.05rem; color: #eceef1;
    display: block; margin-top: .18rem; line-height: 1.2;
  }
  .axn-tiles .d { font-family: "Geist Mono", ui-monospace, monospace; font-size: .7rem; }
  .axn-tiles .d.up { color: #5ee6ff; }
  .axn-tiles .d.down { color: #f87171; }
  .axn-tiles .d.flat { color: #8a919c; }
  .axn-tiles .asof { color: #5c636d; font-size: .62rem; margin-left: .35rem; }
  .axn-tiles svg { display: block; margin-top: .3rem; width: 100%; height: 22px; }

  /* A verdict line: a word and a sentence, with the word carrying the colour. */
  .axn-verdict { display: flex; align-items: baseline; gap: .6rem; margin: .1rem 0 .9rem; flex-wrap: wrap; }
  .axn-verdict .word { font-size: 1.5rem; color: #eceef1; letter-spacing: -.01em; }
  .axn-verdict .word.warn { color: #ffd236; }
  .axn-verdict .word.bad { color: #f87171; }
  .axn-verdict .why { color: #8a919c; font-size: .82rem; }

  /* The odds bar on a prediction: the share the crowd gives the leading outcome. */
  .axn-odds .row { padding: .5rem 0; border-top: 1px solid #191b1d; }
  .axn-odds .row:first-child { border-top: 0; }
  .axn-odds .q { color: #eceef1; font-size: .86rem; line-height: 1.35; display: block; }
  .axn-odds .q a { color: inherit; text-decoration: none; }
  .axn-odds .q a:hover { color: #5ee6ff; }
  .axn-odds .track { display: block; height: 5px; border-radius: 3px; background: #1b1f25; margin: .35rem 0 .2rem; }
  .axn-odds .fill { display: block; height: 100%; border-radius: 3px; background: #5ee6ff; }
  .axn-odds .fill.no { background: #8a919c; }
  .axn-odds .meta {
    font-family: "Geist Mono", ui-monospace, monospace; font-size: .64rem; color: #8a919c;
    display: flex; gap: .55rem; letter-spacing: .06em; text-transform: uppercase;
  }

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


def motion() -> None:
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
