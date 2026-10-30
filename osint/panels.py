"""The panels that stand under the globe, each answering for whatever is selected.

One idea runs through the whole file: a panel is a function that takes a Scope — the world, or
one country — and draws itself. It does not know how the country was chosen, whether a globe
or a dropdown picked it, and it never reaches back into the page. So the page can show six
panels or twenty, in any order, and a new one is a function plus a line in the registry.

Panels that have nothing to say for a country say so in one line rather than vanishing: a grid
that silently reshuffles when you click a country is harder to read than one that holds still.

Nothing here raises. Every feed these panels read is someone else's server, and a panel whose
source is down prints the reason and lets its neighbours carry the page.
"""
from __future__ import annotations

import html as html_lib
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd
import streamlit as st


# ─── What a panel is given ────────────────────────────────────────────────────
@dataclass
class Scope:
    """Either the whole world (cc is None) or one country, with the news already narrowed."""
    cc: str | None
    name: str
    news: pd.DataFrame
    world_news: pd.DataFrame
    markers: list[dict] = field(default_factory=list)
    range_name: str = ""
    # The page fills these in before each panel draws: which panel is being drawn, and how to
    # build the link that closes it. A panel asks for its own way out without being told its
    # name twice.
    panel_key: str = ""
    close_url: Callable[[str], str] | None = None

    @property
    def worldwide(self) -> bool:
        return not self.cc

    def shut(self) -> str:
        return self.close_url(self.panel_key) if self.close_url else ""


@dataclass
class Panel:
    key: str
    title: str
    group: str
    draw: Callable[[Scope], None]
    height: int = 320


PANELS: dict[str, Panel] = {}


def panel(key: str, title: str, group: str, height: int = 320):
    def keep(fn):
        PANELS[key] = Panel(key, title, group, fn, height)
        return fn
    return keep


# ─── Writing numbers the way a reader reads them ──────────────────────────────
def dollars(value: float) -> str:
    """$456bn, not $456,319,229,255.58 — the extra digits are noise at this size."""
    for size, suffix in ((1e12, "tn"), (1e9, "bn"), (1e6, "m"), (1e3, "k")):
        if abs(value) >= size:
            return f"${value / size:,.1f}{suffix}"
    return f"${value:,.2f}"


def count(value: float) -> str:
    for size, suffix in ((1e9, "bn"), (1e6, "m"), (1e3, "k")):
        if abs(value) >= size:
            return f"{value / size:,.1f}{suffix}"
    return f"{value:,.0f}"


def number(value: float, unit: str) -> str:
    if unit == "money":
        return dollars(value)
    if unit == "count":
        return count(value)
    if unit.startswith("percent") or unit == "%":
        return f"{value:,.1f}%"
    if unit == "$":
        return f"${value:,.2f}" if abs(value) < 1000 else f"${value:,.0f}"
    if unit == "¢":
        return f"{value:,.1f}¢"
    return f"{value:,.2f}"


def escape(text) -> str:
    return html_lib.escape(str(text))


# ─── Small drawings ───────────────────────────────────────────────────────────
def head(title: str, right: str = "", *, status: tuple[str, str] | None = None,
         count: int | None = None, tip: str = "", shut: str = "", dense: bool = True) -> str:
    """A panel's top line: its name, how fresh it is, how much it holds, and the way out.

    `status` is (kind, words) — "live" for something fetched this minute, "cached" for a
    figure that stands until its publisher moves it, "off" for a panel with nothing behind it
    today. Saying which of the three a number is doing is the whole job of the chip: a monthly
    commodity average and a live index level look identical without it.
    """
    tools = []
    if right:
        tools.append(f'<span class="axn-label">{escape(right)}</span>')
    if status:
        kind, words = status
        tools.append(f'<span class="chip {escape(kind)}"><i></i>{escape(words)}</span>')
    if count is not None:
        tools.append(f'<span class="count">{count:,}</span>')
    if shut:
        tools.append(f'<a class="shut" href="{escape(shut)}" target="_self" '
                     f'title="Close this panel">×</a>')
    return (f'<div class="axn-head{" dense" if dense else ""}">'
            f'<span class="axn-label">{escape(title)}</span>'
            + (f'<span class="tip" title="{escape(tip)}">?</span>' if tip else "")
            + (f'<span class="tools">{"".join(tools)}</span>' if tools else "")
            + "</div>")


# How old a cached reading is, written the way the chips on the panels write it.
def ago(stamp: str) -> str:
    """"2026-10-27" → "2d"; anything unparseable comes back as it arrived."""
    from datetime import date, datetime
    then = None
    # Our feeds date things two ways: machine order from the statistical sources, and the
    # readable form the currency table prints. Both end up as the same short chip.
    for shape, cut in (("%Y-%m-%d", 10), ("%d %b %Y", 11)):
        try:
            then = datetime.strptime(str(stamp)[:cut].strip(), shape).date()
            break
        except ValueError:
            continue
    if then is None:
        return str(stamp)
    days = (date.today() - then).days
    if days <= 0:
        return "today"
    if days == 1:
        return "1d"
    if days < 31:
        return f"{days}d"
    return f"{days // 30}mo"


def tags(rows: list[tuple[str, str]]) -> str:
    """[(words, kind)] — the small badges under a headline. kind styles it, or "" for plain."""
    if not rows:
        return ""
    return ('<div class="axn-tags">' + "".join(
        f'<span class="{escape(kind)}">{escape(words)}</span>' for words, kind in rows)
        + "</div>")


def spark(values: list[float], colour: str = "#5ee6ff") -> str:
    """A sparkline as inline SVG — no chart library, and it costs one element per tile.

    A flat series would divide by zero on the way to a scale, so it draws down the middle
    instead: a straight line is the truth about a number that has not moved.
    """
    if len(values) < 2:
        return ""
    low, high = min(values), max(values)
    span = high - low
    step = 100 / (len(values) - 1)
    points = " ".join(
        f"{i * step:.2f},{(24 - (v - low) / span * 20 - 2) if span else 12:.2f}"
        for i, v in enumerate(values))
    return (f'<svg viewBox="0 0 100 24" preserveAspectRatio="none" aria-hidden="true">'
            f'<polyline points="{points}" fill="none" stroke="{colour}" stroke-width="1.4" '
            f'vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>')


def tiles(rows: list[dict], sparks: bool = True) -> str:
    """A grid of readings. Each row: {name, value, unit, percent?, as_of?, worse?, history?}."""
    out = []
    for row in rows:
        unit = row.get("unit", "")
        # A rate or a spread moves in percentage points, not in percent of itself. The Fed
        # funds rate going 3.63 → 3.75 is "+0.12 pp"; calling it "+3.31%" reads as though the
        # rate were 3.31 and is the sort of number a dashboard gets quoted wrongly on.
        in_points = unit == "%" or unit.startswith("percent")
        # A once-a-year figure has nothing to be compared against, and "+0.00%" beside it reads
        # as "unchanged" rather than "not measured". Rows that carry no comparison show none.
        moved = "percent" in row or "change" in row
        change = (row.get("change", 0.0) if in_points else row.get("percent", 0.0)) or 0.0
        written = f"{change:+.2f} pp" if in_points else f"{change:+.2f}%"
        worse_up = row.get("worse", "down") == "up"
        way = "flat" if abs(change) < 0.005 else ("down" if (change > 0) == worse_up else "up")
        colour = {"up": "#5ee6ff", "down": "#f87171", "flat": "#8a919c"}[way]
        as_of = row.get("as_of", "")
        out.append(
            f'<div><span class="t">{escape(row["name"])}</span>'
            f'<span class="v">{number(row["value"], unit)}</span>'
            + (f'<span class="d {way}">{written}</span>' if moved else "")
            + (f'<span class="asof">{escape(as_of)}</span>' if as_of else "")
            + (spark(row.get("history") or [], colour) if sparks else "")
            + "</div>")
    return f'<div class="axn-tiles">{"".join(out)}</div>'


def bars(rows: list[tuple[str, float, str]], unit: str = "") -> str:
    """[(name, value, written)] drawn against the largest of them."""
    if not rows:
        return ""
    top = max(abs(v) for _, v, _ in rows) or 1
    out = "".join(
        f'<div class="bar-row"><span class="name">{escape(name)}</span>'
        f'<span class="track"><span class="fill" style="width:{abs(value) / top:.0%}"></span></span>'
        f'<span class="n" style="width:auto">{escape(written)}</span></div>'
        for name, value, written in rows)
    return f'<div class="axn-bars">{out}</div>'


def verdict(word: str, why: str, tone: str = "") -> str:
    return (f'<div class="axn-verdict"><span class="word {tone}">{escape(word)}</span>'
            f'<span class="why">{escape(why)}</span></div>')


def nothing(message: str) -> None:
    st.caption(message)


def source(line: str) -> None:
    st.caption(line)


def freshness(rows: list[dict]) -> tuple[str, str]:
    """The chip for a set of readings: how long ago the newest of them was published."""
    dates = [str(row.get("as_of", "")) for row in rows if row.get("as_of")]
    newest = max(dates) if dates else ""
    return ("cached", ago(newest)) if newest else ("off", "no data")


LIVE = ("live", "live")


# ─── Money ────────────────────────────────────────────────────────────────────
@panel("markets", "Markets", "Money", height=300)
def _markets(scope: Scope) -> None:
    from . import feeds, money, places

    if scope.worldwide:
        rows = money.indices()
        coins = money.crypto()
        st.markdown(head("Markets", status=freshness(rows) if rows else ("off", "down"),
                         count=len(rows) + len(coins), shut=scope.shut(),
                         tip="Index levels and the dollar index from FRED (St. Louis Fed), at "
                             "each market's daily close. Crypto from CoinGecko, live."),
                    unsafe_allow_html=True)
        if not rows:
            return nothing("The market series are not answering — FRED is unreachable.")
        st.markdown(tiles(rows), unsafe_allow_html=True)
        if coins:
            mood = money.fear_and_greed()
            st.markdown(head("Crypto", mood["words"] if mood else "", status=LIVE,
                             tip="Prices from CoinGecko. The fear-and-greed index is "
                                 "Alternative.me's, 0–100: how the people holding it feel, "
                                 "which is a different question from what it costs.")
                        + tiles(coins + ([{"name": "Fear & greed", "value": mood["value"],
                                           "unit": "count", "change": mood["change"],
                                           "percent": mood["change"], "worse": "down"}]
                                         if mood else []), sparks=False),
                        unsafe_allow_html=True)
        return

    shown = []
    index = money.country_index(scope.cc)
    if index:
        shown.append(index)
    local = places.currencies().get(scope.cc)
    table, as_of = feeds.rates()
    if local and table.get(local[0]):
        shown.append({"name": f"USD → {local[0]}", "value": table[local[0]], "unit": "",
                      "as_of": as_of})
    st.markdown(head("Markets", scope.name,
                     status=freshness(shown) if shown else ("off", "none"),
                     count=len(shown) or None, shut=scope.shut(),
                     tip="Share prices from the OECD's main economic indicators via FRED; the "
                         "exchange rate from Frankfurter, which republishes the ECB's."),
                unsafe_allow_html=True)
    if not shown:
        return nothing("No share-price index is published for this country, and its currency "
                       "is not in the daily rates table.")
    st.markdown(tiles(shown), unsafe_allow_html=True)


@panel("macro", "Macro stress", "Money", height=400)
def _macro(scope: Scope) -> None:
    from . import money

    rows = money.macro()
    word, why = money.stress()
    tone = {"Steady": "", "Watchful": "warn", "Strained": "warn", "Stressed": "bad"}.get(word, "")
    st.markdown(head("Macro stress", "united states",
                     status=freshness(rows) if rows else ("off", "down"),
                     count=len(rows) or None, shut=scope.shut(),
                     tip="FRED (St. Louis Fed). The verdict counts how many of the VIX, the two "
                         "financial stress indices and the yield curve sit outside their usual "
                         "range. It summarises the tiles below it and forecasts nothing.")
                + verdict(word, why, tone), unsafe_allow_html=True)
    if not rows:
        return nothing("None of the macro series answered.")
    st.markdown(tiles(rows), unsafe_allow_html=True)


@panel("energy", "Energy", "Money", height=300)
def _energy(scope: Scope) -> None:
    from . import money

    rows = money.energy()
    st.markdown(head("Energy", "crude · gas · pump",
                     status=freshness(rows) if rows else ("off", "down"),
                     count=len(rows) or None, shut=scope.shut(),
                     tip="FRED, carrying EIA and IMF series. Crude and US gas are daily; the "
                         "European gas and uranium prices are monthly averages — each tile "
                         "carries its own date."), unsafe_allow_html=True)
    if not rows:
        return nothing("The energy series are not answering.")
    st.markdown(tiles(rows), unsafe_allow_html=True)


@panel("metals", "Metals & crops", "Money", height=300)
def _metals(scope: Scope) -> None:
    from . import money

    rows = money.metals() + money.grains()
    st.markdown(head("Metals & crops", "monthly",
                     status=freshness(rows) if rows else ("off", "down"),
                     count=len(rows) or None, shut=scope.shut(),
                     tip="IMF primary commodity prices via FRED, monthly averages. No keyless "
                         "feed publishes spot gold or silver any more, so neither is shown."),
                unsafe_allow_html=True)
    if not rows:
        return nothing("The commodity series are not answering.")
    st.markdown(tiles(rows), unsafe_allow_html=True)


@panel("predict", "Predictions", "Money", height=340)
def _predict(scope: Scope) -> None:
    from . import predict

    rows = predict.questions(scope.cc, limit=7)
    st.markdown(head("Predictions", "polymarket", status=("live", "15m") if rows else ("off", "none"),
                     count=len(rows) or None, shut=scope.shut(),
                     tip="Polymarket's open markets, ordered by money traded. Questions the "
                         "crowd has already settled — above 97% — are left out, because a "
                         "market that certain carries no news."), unsafe_allow_html=True)
    if not rows:
        return nothing(f"No open market names {scope.name}." if not scope.worldwide
                       else "Polymarket is not answering.")
    out = []
    for row in rows:
        leading_no = row["outcome"].strip().lower() in ("no", "down", "false")
        out.append(
            f'<div class="row"><span class="q">'
            f'<a href="{escape(row["url"])}" target="_blank">{escape(row["question"])}</a></span>'
            f'<span class="track"><span class="fill{" no" if leading_no else ""}" '
            f'style="width:{row["chance"]:.0%}"></span></span>'
            f'<span class="meta"><span>{row["chance"]:.0%} {escape(row["outcome"])}</span>'
            f'<span>{dollars(row["volume"])}</span>'
            f'<span>ends {escape(row["ends"])}</span></span></div>')
    st.markdown(f'<div class="axn-odds">{"".join(out)}</div>', unsafe_allow_html=True)


# ─── Risk ─────────────────────────────────────────────────────────────────────
PARTS = [("displaced", "displaced"), ("hazard", "hazard"), ("alarm", "alarm"), ("volume", "volume")]

INDEX_TIP = ("Our own index, out of 100: 40% people displaced against population (UNHCR and "
             "the World Bank), 20% standing orange or red disaster alerts (GDACS), 25% how "
             "negative and severe this monitor's news from the country is, 15% how much of it "
             "there is. It ranks; it does not predict.")


@panel("instability", "Instability", "Risk", height=340)
def _instability(scope: Scope) -> None:
    from . import countries, risk

    frame = risk.table(scope.world_news)
    if scope.worldwide:
        st.markdown(head("Instability", "countries scored",
                         status=LIVE if len(frame) else ("off", "none"),
                         count=len(frame) or None, shut=scope.shut(), tip=INDEX_TIP),
                    unsafe_allow_html=True)
        if frame.empty:
            return nothing("Not enough to score countries on yet.")
        st.markdown(bars([(countries.NAMES.get(r.cc, r.cc), r.score, f"{r.score:.0f}")
                          for r in frame.head(10).itertuples()]), unsafe_allow_html=True)
        return

    hit = risk.one(scope.cc, scope.world_news)
    st.markdown(head("Instability", scope.name, status=LIVE if hit else ("off", "none"),
                     shut=scope.shut(), tip=INDEX_TIP), unsafe_allow_html=True)
    if not hit:
        return nothing("Nothing to score this country on — no displacement figures, no "
                       "standing alert, and no news in this range.")
    st.markdown(
        f'<div class="axn-stats">'
        f'<div><span class="axn-label">score</span><span class="v">{hit["score"]:.0f}</span></div>'
        f'<div><span class="axn-label">rank</span><span class="v">{hit["rank"]}<span '
        f'style="font-size:.9rem;color:#8a919c"> / {hit["of"]}</span></span></div>'
        f'</div>' + bars([(label, hit[key], f'{hit[key]:.0f}') for key, label in PARTS]),
        unsafe_allow_html=True)


@panel("displacement", "Displacement", "Risk", height=300)
def _displacement(scope: Scope) -> None:
    from . import world

    tip = ("UNHCR's refugee data, counted by country of origin wherever those people now are. "
           "It is published a year at a time, so the newest figures land in June.")
    if scope.worldwide:
        rows, total = world.biggest_origins(limit=8)
        st.markdown(head("Displacement", f"{count(total)} refugees",
                         status=("cached", str(rows[0]["year"])) if rows else ("off", "down"),
                         count=len(rows) or None, shut=scope.shut(), tip=tip),
                    unsafe_allow_html=True)
        if not rows:
            return nothing("UNHCR is not answering.")
        st.markdown(bars([(r["name"], r["refugees"] + r["idps"],
                           count(r["refugees"] + r["idps"])) for r in rows]),
                    unsafe_allow_html=True)
        return

    found = world.displacement(scope.cc)
    st.markdown(head("Displacement", scope.name,
                     status=("cached", str(found["year"])) if found else ("off", "none"),
                     shut=scope.shut(), tip=tip), unsafe_allow_html=True)
    if not found:
        return nothing("UNHCR records no displacement from this country, or is not answering.")
    st.markdown(tiles([
        {"name": "Refugees", "value": found["refugees"], "unit": "count"},
        {"name": "Displaced at home", "value": found["idps"], "unit": "count"},
        {"name": "Seeking asylum", "value": found["asylum_seekers"], "unit": "count"},
        {"name": "Returned", "value": found["returned_refugees"], "unit": "count"},
    ], sparks=False), unsafe_allow_html=True)


@panel("profile", "Country profile", "Risk", height=300)
def _profile(scope: Scope) -> None:
    from . import world

    tip = ("World Bank open data. These are annual statistics and each tile carries its own "
           "year — none of them is a live reading.")
    if scope.worldwide:
        st.markdown(head("Country profile", status=("off", "pick one"), shut=scope.shut(),
                         tip=tip), unsafe_allow_html=True)
        return nothing("Pick a country on the globe to see what the World Bank says about it.")
    rows = world.profile(scope.cc)
    st.markdown(head("Country profile", scope.name,
                     status=("cached", max((r["year"] for r in rows), default="")) if rows
                     else ("off", "none"),
                     count=len(rows) or None, shut=scope.shut(), tip=tip), unsafe_allow_html=True)
    if not rows:
        return nothing("The World Bank publishes no recent indicators for this country.")
    st.markdown(tiles([{**row, "as_of": row["year"]} for row in rows], sparks=False),
                unsafe_allow_html=True)


@panel("infra", "Infrastructure here", "Risk", height=300)
def _infra(scope: Scope) -> None:
    from . import layers as built

    named = [("ports", "Ports"), ("bases", "Military bases"), ("airbases", "Air bases"),
             ("nuclear", "Nuclear plants"), ("renewables", "Power stations"),
             ("refineries", "Refineries"), ("mines", "Mines"),
             ("datacenters", "Data centres"), ("exchanges", "Stock exchanges"),
             ("central_banks", "Central banks")]
    rows = []
    for key, label in named:
        items = built.items(key)
        if not scope.worldwide:
            items = [row for row in items if row.get("cc") == scope.cc]
        if items:
            rows.append((label, len(items), f"{len(items):,}"))
    if scope.worldwide and built.cables():
        rows.append(("Submarine cables", len(built.cables()), f"{len(built.cables()):,}"))

    made = built.built("ports") or built.built("bases")
    st.markdown(head("Infrastructure here", scope.name if not scope.worldwide else "worldwide",
                     status=("cached", ago(made)) if made else ("off", "unbuilt"),
                     count=sum(n for _, n, _ in rows) or None, shut=scope.shut(),
                     tip="The map's own built layers, from Wikidata, OpenStreetMap and the ITU, "
                         "rebuilt monthly. Switch a layer on above to see these on the globe."),
                unsafe_allow_html=True)
    if not rows:
        return nothing("None of the built layers has anything in this country.")
    st.markdown(bars(sorted(rows, key=lambda r: -r[1])), unsafe_allow_html=True)


@panel("hazards", "Live hazards", "Risk", height=300)
def _hazards(scope: Scope) -> None:
    kinds: dict[str, int] = {}
    worst = []
    for mark in scope.markers:
        if not str(mark.get("pick", "")).startswith("event:"):
            continue
        if not scope.worldwide and mark.get("cc") != scope.cc:
            continue
        layer = str(mark.get("layer", "")).split(" · ")[0]
        kinds[layer] = kinds.get(layer, 0) + 1
        if str(mark.get("level", "")).lower() in ("red", "amber"):
            worst.append(mark)

    st.markdown(head("Live hazards", scope.name if not scope.worldwide else "worldwide",
                     status=LIVE if kinds else ("off", "quiet"),
                     count=sum(kinds.values()) or None, shut=scope.shut(),
                     tip="USGS, GDACS (the UN and European Commission), NASA EONET, WHO and the "
                         "live flight and satellite feeds — whichever you have switched on in "
                         "the layer panel on the globe."), unsafe_allow_html=True)
    if not kinds:
        return nothing("No live event from the switched-on layers falls here. Switch more "
                       "layers on from the panel on the globe.")
    st.markdown(bars(sorted(((k, v, f"{v:,}") for k, v in kinds.items()), key=lambda r: -r[1])),
                unsafe_allow_html=True)
    if worst:
        lines = "".join(
            f'<div class="row"><div class="meta"><span>{escape(mark.get("ts", ""))}</span></div>'
            f'<div class="head">{escape(mark.get("title", ""))} — {escape(mark.get("detail", ""))}'
            f'</div>{tags([(str(mark.get("level", "")), "alert" if str(mark.get("level")) == "red" else "warn")])}'
            f'</div>' for mark in worst[:4])
        st.markdown(head("Rated orange or red", count=len(worst))
                    + f'<div class="axn-stream">{lines}</div>', unsafe_allow_html=True)


# ─── News ─────────────────────────────────────────────────────────────────────
# Severity is scored 1–5 by the analyzer. These are the words it is given on screen, because
# "severity 4" means nothing to a reader and "high" does.
BANDS = [(5, "Critical", "#f87171"), (4, "High", "#fb923c"), (3, "Medium", "#ffd236"),
         (2, "Low", "#5ee6ff"), (1, "Info", "#8a919c")]


def provenance(row) -> list[tuple[str, str]]:
    """The badges under a headline: how well we think of the publisher, and what else we know.

    Worth spelling out, because it is easy to read these the wrong way round. "Cred" is this
    monitor's own score for the publisher, not anyone else's rating. "Single publisher" means
    nobody else in our collection carried the story — not that it is wrong, only that it
    stands alone. "Unreviewed" means no fact-checker has looked at it, which is the normal
    state of almost all news.
    """
    out = []
    cred = row.get("credibility")
    if cred is not None and pd.notna(cred):
        band = "high" if cred >= 65 else "mid" if cred >= 40 else "low"
        out.append((f"cred {cred:.0f}", f"cred {band}"))
    rating = row.get("factcheck_rating")
    if rating is not None and pd.notna(rating) and str(rating).strip():
        out.append((str(rating)[:18], "warn"))
    else:
        out.append(("unreviewed", ""))
    corroboration = row.get("corroboration")
    if corroboration is not None and pd.notna(corroboration) and corroboration <= 1:
        out.append(("single publisher", ""))
    label = row.get("category_label")
    if label is not None and pd.notna(label) and str(label) != "Other":
        out.append((str(label)[:20], ""))
    return out


@panel("threat", "Threat timeline", "News", height=360)
def _threat(scope: Scope) -> None:
    import altair as alt

    frame = scope.news
    negative = frame[(frame["sentiment"] == "negative") & frame["severity"].notna()]
    st.markdown(head("Threat timeline", scope.range_name.lower(),
                     status=LIVE if len(negative) else ("off", "quiet"),
                     count=len(negative) or None, shut=scope.shut(),
                     tip="This monitor's own collection, scored 1–5 by its analyzer and "
                         "stacked by the day the story carried."), unsafe_allow_html=True)
    if negative.empty:
        return nothing("Nothing negative with a severity score here in this range.")

    graded = negative.assign(
        day=negative["ts"].dt.floor("D"),
        band=negative["severity"].round().clip(1, 5).map({n: w for n, w, _ in BANDS}))
    counts = graded.groupby(["day", "band"]).size().reset_index(name="items")
    order = [word for _, word, _ in BANDS]
    st.altair_chart(
        alt.Chart(counts).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
            x=alt.X("day:T", title=None, axis=alt.Axis(format="%d %b", tickCount=7)),
            y=alt.Y("items:Q", title=None, stack=True),
            color=alt.Color("band:N", title=None, sort=order,
                            scale=alt.Scale(domain=order, range=[c for _, _, c in BANDS]),
                            legend=alt.Legend(orient="bottom", direction="horizontal")),
            tooltip=["day:T", "band:N", "items:Q"],
        ).properties(height=170), width="stretch")

    worst = graded[graded["severity"] >= 4].sort_values("ts", ascending=False)
    if not worst.empty:
        lines = "".join(
            f'<div class="row"><div class="meta"><span>{it.ts.strftime("%d %b %H:%M")}</span>'
            f'<span class="chip">{escape(str(it.source))[:22]}</span>'
            f'<span class="score neg">{escape(str(it.band)).upper()}</span></div>'
            f'<div class="head">{escape(str(it.title or "")[:120])}</div>'
            f'{tags(provenance(it._asdict()))}</div>'
            for it in worst.head(3).itertuples())
        st.markdown(head("Worst of them", count=len(worst))
                    + f'<div class="axn-stream">{lines}</div>', unsafe_allow_html=True)


@panel("topics", "What it is about", "News", height=300)
def _topics(scope: Scope) -> None:
    frame = scope.news
    st.markdown(head("What it is about", scope.name if not scope.worldwide else "",
                     status=LIVE if len(frame) else ("off", "empty"),
                     count=len(frame) or None, shut=scope.shut(),
                     tip="This monitor's own collection, as its analyzer categorised it."),
                unsafe_allow_html=True)
    if frame.empty:
        return nothing("No news here in this range.")
    counts = frame["category_label"].dropna().value_counts().head(7)
    if counts.empty:
        return nothing("None of this news has been categorised yet.")
    st.markdown(bars([(str(name), int(n), f"{int(n):,}") for name, n in counts.items()]),
                unsafe_allow_html=True)
    kinds = frame["source_type"].value_counts()
    st.markdown(head("Where it came from", count=int(kinds.size))
                + bars([(str(k).replace("_", " "), int(n), f"{int(n):,}")
                        for k, n in kinds.head(5).items()]), unsafe_allow_html=True)


@panel("sources", "Publishers", "News", height=300)
def _sources(scope: Scope) -> None:
    frame = scope.news
    st.markdown(head("Publishers", scope.name if not scope.worldwide else "",
                     status=LIVE if len(frame) else ("off", "empty"),
                     count=int(frame["source"].nunique()) or None, shut=scope.shut(),
                     tip="Credibility is this monitor's own score for the publisher, out of "
                         "100 — not a rating anyone else awards."), unsafe_allow_html=True)
    if frame.empty:
        return nothing("No news here in this range.")
    grouped = frame.groupby("source").agg(items=("id", "size"),
                                          cred=("credibility", "mean")).sort_values(
        "items", ascending=False).head(8)
    rows = []
    for name, row in grouped.iterrows():
        written = f"{int(row['items']):,}"
        if pd.notna(row["cred"]):
            written += f" · {row['cred']:.0f}"
        rows.append((str(name), int(row["items"]), written))
    st.markdown(bars(rows), unsafe_allow_html=True)


@panel("tone", "Press tone", "News", height=300)
def _tone(scope: Scope) -> None:
    import altair as alt

    from . import gdelt

    rows, note = gdelt.timeline(scope.cc or "", "tone")
    st.markdown(head("Press tone", scope.name.lower() if not scope.worldwide else "war · sanctions",
                     status=("live", "30m") if rows else ("off", "waiting"),
                     count=len(rows) or None, shut=scope.shut(),
                     tip="GDELT, which reads the open web in a hundred languages. Tone runs "
                         "roughly −10 to +10; below zero is coverage that reads as bad news. "
                         "GDELT asks to be queried sparingly, so this is held for half an hour."),
                unsafe_allow_html=True)
    if not rows:
        return nothing(note)
    frame = pd.DataFrame(rows)
    st.markdown(
        f'<div class="axn-stats"><div><span class="axn-label">average</span>'
        f'<span class="v">{frame["value"].mean():+.2f}</span></div>'
        f'<div><span class="axn-label">lowest</span>'
        f'<span class="v">{frame["value"].min():+.2f}</span></div></div>',
        unsafe_allow_html=True)
    st.altair_chart(
        alt.Chart(frame).mark_area(line={"color": "#5ee6ff"}, opacity=0.22,
                                   color="#5ee6ff").encode(
            x=alt.X("when:T", title=None, axis=alt.Axis(format="%d %b")),
            y=alt.Y("value:Q", title=None, scale=alt.Scale(zero=False)),
            tooltip=["when:T", "value:Q"],
        ).properties(height=150), width="stretch")


PLAYER_CSS = ("html,body{margin:0;height:100%;background:transparent}"
              "iframe{height:100%;aspect-ratio:16/9;max-width:100%;border:0;"
              "border-radius:.5rem;display:block;margin:0 auto}")


def player(src: str, title: str, rolling: bool = False) -> str:
    """One video, kept at 16:9 whatever the panel's width turns out to be.

    A fixed pixel height is what squashes these: the panel is as wide as half the grid, which
    changes with the window, so the only height that is ever right is the one the browser works
    out from the width. aspect-ratio does that; a number here could not.
    """
    allow = ("autoplay; " if rolling else "") + "encrypted-media; picture-in-picture"
    return (f'<style>{PLAYER_CSS}</style>'
            f'<iframe src="{escape(src)}" title="{escape(title)}" '
            f'allow="{allow}" allowfullscreen></iframe>')


# ─── Live ─────────────────────────────────────────────────────────────────────
TV_TIP = ("Each broadcaster's own round-the-clock stream, addressed by channel so the link "
          "survives the nightly restart that gives the stream a new video id. Nothing plays "
          "until you press play. Picking a country moves the strip to the newsroom nearest it. "
          "A dark player means that channel is between bulletins, not that the link is broken.")


@panel("tv", "Live news TV", "Live", height=440)
def _tv(scope: Scope) -> None:
    from . import cams

    names = [name for name, _ in cams.CHANNELS]
    st.markdown(head("Live news TV", "" if scope.worldwide else scope.name, status=LIVE,
                     count=len(names), shut=scope.shut(), tip=TV_TIP), unsafe_allow_html=True)

    # Selecting a country moves the strip to the newsroom nearest it, once. After that the
    # choice is the reader's: re-picking it on every rerun would fight whoever is watching.
    if st.session_state.get("axn_tv_for") != (scope.cc or ""):
        st.session_state["axn_tv"] = cams.opening_channel(scope.cc)
        st.session_state["axn_tv_for"] = scope.cc or ""
    chosen = st.radio("Channel", names, key="axn_tv", horizontal=True,
                      label_visibility="collapsed")
    # components.html wraps the player in a document of our own, which is what holds it to
    # 16:9 whatever width the panel turns out to be. It is deprecated, so when it finally goes
    # the panel falls back to st.iframe: a fixed box rather than a shaped one, letterboxed by
    # YouTube, which is worse-looking but not broken.
    src = cams.embed(dict(cams.CHANNELS)[chosen])
    try:
        import streamlit.components.v1 as components
        components.html(player(src, f"{chosen} live"), height=300)
    except (ImportError, AttributeError):
        st.iframe(src, height=300)


CAM_TIP = ("Windy's public webcam catalogue: tens of thousands of cameras, each one placed, "
           "which is what lets this narrow to the country you have selected. The pictures "
           "refresh every few minutes rather than streaming; click one for its live player.")

OPEN_TIP = ("Three transport authorities that publish their traffic cameras openly — London, "
            "California and Singapore — refreshed every few minutes. A free Windy webcams key "
            "in secrets.toml as windy_webcams_key swaps these for the worldwide catalogue of "
            "about 65,000 cameras, which is the same source worldmonitor's own layer uses.")


def cam_grid(rows: list[dict]) -> str:
    """The cameras as a wall of pictures, any one of which opens where it stands.

    Pictures rather than players, and not for want of trying: nearly every webcam channel on
    YouTube allowlists the domains that may embed it, so a wall built from them is dark
    everywhere but the owner's own site. A still that always loads beats a player that usually
    does not.

    Clicking enlarges in place rather than following a link. A link would send you to the
    publisher's tab — and some of these, Singapore's among them, serve their pictures as
    downloads, so the click would land a file in your downloads folder instead of showing you
    the road. The whole trick is :target, so it costs no rerun and no round trip.
    """
    cells = []
    for index, row in enumerate(rows):
        where = ", ".join(part for part in (row["city"], row["country"]) if part)
        tile = f"axn-cam-{index}"
        cells.append(
            f'<div class="cam" id="{tile}">'
            f'<img src="{escape(row["thumb"])}" alt="{escape(row["title"])}" loading="lazy">'
            f'<a class="open" href="#{tile}" title="{escape(row["title"])}"></a>'
            f'<a class="shut" href="#axn-cams" title="Smaller">×</a>'
            f'<span class="who">{escape(row["title"][:42])}</span>'
            + (f'<span class="where">{escape(where)}</span>' if where else "")
            + "</div>")
    return f'<div class="axn-cams" id="axn-cams">{"".join(cells)}</div>'


@panel("webcams", "Live webcams", "Live", height=440)
def _webcams(scope: Scope) -> None:
    from . import cams, places

    keyed = bool(cams.key())
    tabs = [name for name, _ in cams.REGIONS]

    if not scope.worldwide:
        # A country is selected: no tabs, because the country has already chosen the region
        if keyed:
            place = places.biggest_city(scope.cc)
            rows = cams.country(scope.cc, place[1] if place else None,
                                place[2] if place else None)
            where = scope.name
        else:
            rows, where = cams.open_region(cams.OPEN_BY_CC.get(scope.cc or "", ""))
        st.markdown(head("Live webcams", where or scope.name,
                         status=LIVE if rows else ("off", "none"), count=len(rows) or None,
                         shut=scope.shut(), tip=CAM_TIP if keyed else OPEN_TIP),
                    unsafe_allow_html=True)
        if not rows:
            return nothing(
                f"Windy lists no public camera in {scope.name}." if keyed else
                f"No open camera feed covers {scope.name}. A free Windy webcams key opens the "
                f"worldwide catalogue, which then follows whichever country you select.")
        st.markdown(cam_grid(rows[:6]), unsafe_allow_html=True)
        return source(f"{len(rows)} cameras · {'Windy' if keyed else where}")

    # Worldwide: the tabs stand whether or not there is a key, so the panel always says what it
    # could show as well as what it is showing
    st.markdown(head("Live webcams", status=LIVE, shut=scope.shut(),
                     tip=CAM_TIP if keyed else OPEN_TIP), unsafe_allow_html=True)
    region = st.radio("Region", tabs, key="axn_cam_region", horizontal=True,
                      index=tabs.index("Europe"), label_visibility="collapsed")
    rows, where = (cams.region(region), region) if keyed else cams.open_region(region)

    if not rows:
        return nothing(
            f"Windy is not answering for {region}." if keyed else
            f"No open camera feed covers {region}. London, California and Singapore publish "
            f"theirs; a free Windy webcams key opens the other 65,000.")
    st.markdown(cam_grid(rows[:6]), unsafe_allow_html=True)
    source(f"{len(rows)} cameras · {'Windy' if keyed else where}")


# ─── Themes, and news against markets ────────────────────────────────────────
INTEL_TIP = ("This monitor's own collection, cut by what a headline is about rather than how "
             "the analyzer filed it — a hospital ransomware attack is Health by category and "
             "Cyber by theme, and both are worth being able to ask for. Tone is the mean "
             "sentiment score of the day's items, from -1 to +1; volume is how many there "
             "were.")

MARKET_TIP = ("Daily news volume for one theme against one market's close, on the days both "
              "have. The interval is Fisher's z at 95%, and on a week of daily points it is "
              "normally wide enough to contain zero — which is the finding. Two lines moving "
              "together over seven days is not evidence that one moves the other.")


def since(stamp) -> str:
    """How long ago, to the minute. ago() rounds to the day because the sources it reads
    publish by the day; a wire needs to tell this hour from this morning."""
    when = pd.to_datetime(stamp, errors="coerce")
    if pd.isna(when):
        return ""
    gap = (pd.Timestamp.now() - when).total_seconds()
    if gap < 3600:
        return f"{max(0, int(gap // 60))}m"
    if gap < 86400:
        return f"{int(gap // 3600)}h"
    return f"{int(gap // 86400)}d"


def one_each(rows: pd.DataFrame, limit: int) -> list:
    """One row per story. A forum thread arrives as a dozen comments quoting the same line,
    and a panel that shows eight of them has shown you one headline eight times."""
    import re

    seen, kept = set(), []
    for _, row in rows.iterrows():
        title = str(row.get("title") or "")
        # Strip what varies and keep what repeats: the commenter's name changes, the quoted
        # headline does not.
        body = re.sub(r"^/u/\S+\s+on\s+", "", title, flags=re.I)
        key = re.sub(r"[^a-z0-9]+", "", body.lower())[:60]
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(row)
        if len(kept) == limit:
            break
    return kept


def wire(rows: pd.DataFrame, limit: int = 8) -> str:
    """Headlines as they come off the wire: who filed it, how long ago, and how bad it reads."""
    cells = []
    for row in one_each(rows, limit):
        sev = int(row.get("severity") or 0)
        tone = "bad" if sev >= 4 else "warn" if sev >= 2 else ""
        when = since(row.get("ts"))
        link = str(row.get("url") or "")
        title = escape(str(row.get("title") or "")[:120])
        cells.append(
            f'<div class="item {tone}">'
            f'<div class="meta"><span>{escape(str(row.get("source") or "")[:28])}</span>'
            f'<span>{escape(when)}</span></div>'
            + (f'<a href="{escape(link)}" target="_blank" rel="noopener">{title}</a>'
               if link else f"<span>{title}</span>")
            + "</div>")
    return f'<div class="axn-wire">{"".join(cells)}</div>'


@panel("intel", "Live intelligence", "News", height=440)
def _intel(scope: Scope) -> None:
    from . import intel

    frame = scope.news
    st.markdown(head("Live intelligence", "" if scope.worldwide else scope.name,
                     status=LIVE if len(frame) else ("off", "empty"), shut=scope.shut(),
                     tip=INTEL_TIP), unsafe_allow_html=True)
    theme = st.radio("Theme", list(intel.THEMES), key="axn_intel", horizontal=True,
                     label_visibility="collapsed")
    if frame.empty:
        return nothing("No news here in this range.")

    rows = intel.match(frame, theme)
    if rows.empty:
        return nothing(f"Nothing about {theme.lower()} in this range"
                       + ("." if scope.worldwide else f" for {scope.name}."))

    # Tone and volume as the day-by-day shape, which is what makes a spike legible
    by_day = intel.daily(rows, days=14)
    tone = float(rows["score"].mean())
    st.markdown(tiles([
        {"name": "Tone", "value": tone, "unit": "", "worse": "down",
         "history": [float(v) for v in by_day["tone"].fillna(0)]},
        {"name": "Volume", "value": float(len(rows)), "unit": "count", "worse": "up",
         "history": [float(v) for v in by_day["volume"]]},
    ]) + wire(rows), unsafe_allow_html=True)


MARKETS = [("SP500", "S&P 500"), ("DJIA", "Dow Jones"), ("NASDAQCOM", "Nasdaq"),
           ("DTWEXBGS", "Dollar index"), ("VIXCLS", "VIX"), ("DCOILBRENTEU", "Brent crude"),
           ("DHHNGSP", "US natural gas"), ("DGS10", "10-year yield")]


@panel("correlate", "News ↔ markets", "Money", height=440)
def _correlate(scope: Scope) -> None:
    import altair as alt
    from . import intel, series, ui

    st.markdown(head("News ↔ markets", "" if scope.worldwide else scope.name, status=LIVE,
                     shut=scope.shut(), tip=MARKET_TIP), unsafe_allow_html=True)

    left, middle, right = st.columns(3)
    theme = left.selectbox("News theme", list(intel.THEMES), key="axn_corr_theme")
    market = middle.selectbox("Market", [name for _, name in MARKETS], key="axn_corr_market")
    days = right.selectbox("Window", [7, 14, 30], index=1, key="axn_corr_days",
                           format_func=lambda n: f"{n} days")

    # The page's own range can be six hours, which is no window at all for this, so the panel
    # reads the whole collection and cuts its own
    frame = ui.load(ui.data_version())[0]
    if not scope.worldwide:
        frame = frame[frame["country"] == scope.cc]
    rows = intel.match(frame, theme)
    by_day = intel.daily(rows, days=days)
    closes = series.fred(dict((b, a) for a, b in MARKETS)[market], points=days * 2)
    paired = intel.against(by_day, closes)

    if len(paired) < 4:
        return nothing("Not enough days where both the news and the market have a figure.")

    news_line = alt.Chart(paired).mark_line(color="#ffd236", strokeWidth=1.4).encode(
        x=alt.X("day:T", title=None, axis=alt.Axis(format="%d %b")),
        y=alt.Y("volume:Q", title=None, axis=alt.Axis(titleColor="#ffd236")),
        tooltip=["day:T", "volume:Q"])
    price_line = alt.Chart(paired).mark_line(color="#5ee6ff", strokeWidth=1.8).encode(
        x=alt.X("day:T", title=None),
        y=alt.Y("price:Q", title=None, scale=alt.Scale(zero=False)),
        tooltip=["day:T", "price:Q"])
    st.altair_chart(alt.layer(news_line, price_line).resolve_scale(y="independent")
                    .properties(height=150), width="stretch")

    fit = intel.pearson(list(paired["volume"]), list(paired["price"]))
    if not fit:
        return nothing("One of the two did not move at all this window.")
    # Written out rather than passed through tiles(): the interval is a pair of numbers, and
    # the whole point of showing it is that it is usually wider than r alone would suggest
    stats = ('<div class="axn-tiles">'
             f'<div><span class="t">Pearson r</span>'
             f'<span class="v">{fit["r"]:+.2f}</span></div>'
             f'<div><span class="t">95% interval</span>'
             f'<span class="v small">{fit["lo"]:+.2f} … {fit["hi"]:+.2f}</span></div>'
             f'<div><span class="t">Paired days</span>'
             f'<span class="v">{fit["n"]}</span></div></div>')
    st.markdown(stats + verdict(
        fit["words"], f'{theme} news volume against {market}',
        "" if "No clear" in fit["words"] else "warn"), unsafe_allow_html=True)
    source("This monitor's own collection · market series from FRED")


METAL_TIP = ("Spot metal is the mid of Swissquote's public two-sided quote — a dealable "
             "market price, not the LBMA benchmark fix, so it moves all day and will not "
             "match the afternoon fix to the cent. The feed carries no previous close, which "
             "is why these four show a level and no move. Copper and aluminium are monthly "
             "IMF averages and do carry one. Euro rates are the ECB's daily reference rates, "
             "set once each working day at about 16:00 CET.")


@panel("materials", "Metals & materials", "Money", height=360)
def _materials(scope: Scope) -> None:
    from . import metals, money

    st.markdown(head("Metals & materials", status=LIVE, shut=scope.shut(), tip=METAL_TIP),
                unsafe_allow_html=True)
    tab = st.radio("Table", ["Commodities", "EUR FX"], key="axn_metal", horizontal=True,
                   label_visibility="collapsed")

    if tab == "Commodities":
        precious = metals.spot()
        # The four that are priced by the ounce, then the two that are priced by the tonne —
        # different markets, so they are shown as separate rows rather than one ranking
        if precious:
            st.markdown(tiles([{"name": row["name"], "value": row["value"], "unit": "$"}
                               for row in precious], sparks=False), unsafe_allow_html=True)
        else:
            nothing("The spot metal feed is not answering.")
        base = [row for row in money.metals() if row["name"] in ("Copper", "Aluminium")]
        if base:
            st.markdown(head("Base metals", "monthly average",
                             status=freshness(base)) + tiles(base), unsafe_allow_html=True)
        return source("Swissquote spot · IMF monthly averages via FRED")

    rates = metals.euro()
    if not rates:
        return nothing("The ECB reference rates are not answering.")
    cells = []
    for row in rates:
        moved = row.get("change")
        # A currency pair moves in its own units, not in per cent of itself: EUR/JPY shifting
        # 0.06 is six sen, and writing it as a percentage buries that.
        way = "flat" if moved is None or abs(moved) < 5e-5 else ("up" if moved > 0 else "down")
        digits = 2 if row["value"] > 10 else 4
        cells.append(
            f'<div><span class="t">{escape(row["name"])}</span>'
            f'<span class="v">{row["value"]:,.{digits}f}</span>'
            + (f'<span class="d {way}">{moved:+.4f}</span>' if moved is not None else "")
            + "</div>")
    st.markdown(f'<div class="axn-tiles">{"".join(cells)}</div>', unsafe_allow_html=True)
    source("ECB daily reference rates · change against the previous working day")


# ─── Trade, air and relief ────────────────────────────────────────────────────
CHOKE_TIP = ("IMF PortWatch, from AIS transponder data. Each waterway is measured against "
             "itself — the last seven days against the ninety before them — never against "
             "another waterway: comparing straits says only which is busier, which never "
             "changes. A ship with its transponder off is not counted, which is why "
             "PortWatch's Hormuz figure sits in single digits though the strait is one of the "
             "busiest in the world. A level is only graded where there are at least three "
             "weeks of baseline behind it.")


@panel("chokepoints", "Trade chokepoints", "Risk", height=340)
def _chokepoints(scope: Scope) -> None:
    from . import flows

    rows, note = flows.chokepoints()
    if not scope.worldwide:
        rows = [row for row in rows if scope.cc in row["countries"]]
    st.markdown(head("Trade chokepoints", scope.name if not scope.worldwide else "daily transits",
                     status=("cached", ago(rows[0]["as_of"])) if rows else ("off", "waiting"),
                     count=len(rows) or None, shut=scope.shut(), tip=CHOKE_TIP),
                unsafe_allow_html=True)
    if not rows:
        return nothing(f"No chokepoint is reached through {scope.name}." if not scope.worldwide
                       else note or "IMF PortWatch is not answering.")

    # Already ordered by how far below its own habit each has fallen: a strait behaving
    # normally is not news, and the one that has quietly emptied is the whole point
    cells = []
    for row in rows[:8]:
        tone = {"critical": "bad", "elevated": "warn", "moderate": "warn"}.get(row["level"], "")
        cells.append(
            f'<div class="item {tone}">'
            f'<div class="meta"><span>{escape(row["name"])}</span>'
            f'<span>{row["level"] or "ungraded"}</span></div>'
            f'<span>{row["change"]:+.0f}% against its own norm · '
            f'{row["ships"]:.1f} a day, usually {row["norm"]:.1f}</span></div>')
    st.markdown(f'<div class="axn-wire">{"".join(cells)}</div>', unsafe_allow_html=True)
    source(f"{len(rows)} waterways · IMF PortWatch")


@panel("air", "Air & atmosphere", "Risk", height=390)
def _air(scope: Scope) -> None:
    from . import climate, places

    if scope.worldwide:
        cities = climate.watched()
        st.markdown(head("Air & atmosphere", "worst of the watched cities",
                         status=LIVE if cities else ("off", "down"),
                         count=len(cities) or None, shut=scope.shut(),
                         tip="Open-Meteo's air quality forecast, from the Copernicus CAMS "
                             "model, as the US AQI. Carbon dioxide is NOAA's global daily "
                             "mean, seasonally adjusted."), unsafe_allow_html=True)
        if cities:
            st.markdown(bars([(row["name"], row["aqi"], f'{row["aqi"]:.0f} · {row["band"]}')
                              for row in cities[:7]]), unsafe_allow_html=True)
    else:
        place = places.biggest_city(scope.cc)
        now = climate.air(place[1], place[2]) if place else None
        st.markdown(head("Air & atmosphere", place[0] if place else scope.name,
                         status=LIVE if now else ("off", "none"), shut=scope.shut(),
                         tip="Open-Meteo's air quality forecast for this country's largest "
                             "city, as the US AQI. Carbon dioxide is NOAA's global mean — the "
                             "same figure wherever you are standing."), unsafe_allow_html=True)
        if not now:
            nothing("No air quality reading for this country.")
        else:
            st.markdown(verdict(f"{now['aqi']:.0f}", now["band"],
                                "" if now["aqi"] <= 100 else "warn" if now["aqi"] <= 150 else "bad")
                        + tiles([{"name": "PM2.5", "value": now["pm2_5"] or 0, "unit": ""},
                                 {"name": "PM10", "value": now["pm10"] or 0, "unit": ""},
                                 {"name": "NO₂", "value": now["no2"] or 0, "unit": ""},
                                 {"name": "Ozone", "value": now["ozone"] or 0, "unit": ""}],
                                sparks=False), unsafe_allow_html=True)

    co2 = climate.carbon()
    if co2:
        st.markdown(head("Carbon dioxide", "global mean", status=("cached", ago(co2["as_of"])))
                    + tiles([{"name": "CO₂ ppm", "value": co2["ppm"], "unit": "",
                              "as_of": co2["as_of"], "worse": "up",
                              "percent": (co2["ppm"] - co2["a_year_ago"]) / co2["a_year_ago"] * 100,
                              "history": co2["history"]}]), unsafe_allow_html=True)


@panel("relief", "Humanitarian response", "Risk", height=340)
def _relief(scope: Scope) -> None:
    from . import countries, relief, risk, world

    tip = ("UN OCHA's Humanitarian API: what a country's response plan asked for, what share "
           "of it has arrived, and how many people the plan counts as needing help. An appeal "
           "that is barely funded is the clearest early warning on this page.")

    if scope.worldwide:
        # The worst-off countries by our own index, each with its appeal's funding gap
        frame = risk.table(scope.world_news)
        rows = []
        for cc in [r.cc for r in frame.head(12).itertuples()]:
            money = relief.appeal(world.iso3(cc))
            if money:
                rows.append((countries.NAMES.get(cc, cc), money["share"],
                             f'{money["share"]:.0f}% of ${money["needs"] / 1e6:,.0f}m'))
        st.markdown(head("Humanitarian response", "appeals funded",
                         status=("cached", "2026") if rows else ("off", "none"),
                         count=len(rows) or None, shut=scope.shut(), tip=tip),
                    unsafe_allow_html=True)
        if not rows:
            return nothing("OCHA has no current appeal for the countries at the top of the "
                           "instability table, or is not answering.")
        st.markdown(bars(sorted(rows, key=lambda r: r[1])), unsafe_allow_html=True)
        source("Sorted by how little has arrived, not by how much was asked for.")
        return

    three = world.iso3(scope.cc)
    money = relief.appeal(three)
    needs = relief.in_need(three)
    st.markdown(head("Humanitarian response", scope.name,
                     status=("cached", money["year"]) if money else ("off", "none"),
                     shut=scope.shut(), tip=tip), unsafe_allow_html=True)
    if not money and not needs:
        return nothing("OCHA runs no humanitarian response plan for this country — which for "
                       "most countries is the ordinary state of affairs.")
    if money:
        st.markdown(f'<p style="color:#c6ccd4;margin:.1rem 0 .5rem;font-size:.85rem">'
                    f'{escape(money["name"])}</p>'
                    + tiles([{"name": "Asked for", "value": money["needs"], "unit": "money"},
                             {"name": "Arrived", "value": money["funded"], "unit": "money"},
                             {"name": "Funded", "value": money["share"], "unit": "%",
                              "worse": "down"}], sparks=False), unsafe_allow_html=True)
    if needs and needs["sectors"]:
        top = sorted(needs["sectors"].items(), key=lambda row: -row[1])[:5]
        st.markdown(head("People in need", needs["year"], count=len(needs["sectors"]))
                    + bars([(name, value, count(value)) for name, value in top]),
                    unsafe_allow_html=True)
