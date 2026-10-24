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

  .axn-stats { display: flex; gap: 1.8rem; margin: .2rem 0 1rem; }
  .axn-stats .axn-label { display: block; margin-bottom: .15rem; }
  .axn-stats .v { display: block; font-family: "Geist Mono", ui-monospace, monospace;
                  font-size: 1.5rem; color: #eceef1; line-height: 1.25; }
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
