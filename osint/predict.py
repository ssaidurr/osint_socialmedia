"""What people are betting will happen — Polymarket's open markets.

A prediction market is the one public number that is explicitly about the future, which is why
it earns a place next to news that is all about the past. Polymarket's gamma API is open and
needs no key.

Three decisions shape what comes back, and each of them is the answer to something that was
wrong with the first version of this file:

  * **Events, not markets.** The /markets listing returns one row per *outcome*, so a single
    race arrives as a hundred and twenty-eight near-identical rows — "Will Gavin Newsom win
    the 2028 Democratic presidential nomination?", "Will JD Vance…", and so on. Ordered by
    money, they filled the whole panel, and a dashboard about the world showed seven American
    candidates. The /events listing returns one row per *question* with its outcomes nested,
    so a race is one line with its front-runner named, and six other things fit on the screen.

  * **Polymarket's own tags decide the category.** Each event carries labels — Geopolitics,
    Elections, Crypto, Fed Rates, Middle East — written by the people who listed the market.
    Guessing the subject from keywords in the question was close, but it put "Netanyahu out by
    end of 2026?" in no category at all and had nothing to say about half the board. Asking the
    API for one tag at a time means the category is known by construction rather than inferred,
    and it is a far smaller download than the whole board.

  * **Country comes from the data, not the prose.** Events carry `countryName`, and their tags
    name countries too. The general matcher in `countries` only reads full names out of running
    text, which misses "U.S." and every market whose country is a label rather than a word.

Nothing here raises. Every call is someone else's server, read against a deadline, and a tag
that does not answer is simply a category with nothing in it today.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from . import countries, http
from .cache import remember

API = "https://gamma-api.polymarket.com/events"

# Our categories, and the Polymarket tag each one asks for. Order is priority: an event tagged
# both Geopolitics and Politics is a conflict story, because that is the more specific thing to
# say about it, and an election is an election before it is politics.
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("Conflict", "geopolitics"),
    ("Elections", "elections"),
    ("Politics", "politics"),
    ("Economy", "economy"),
    ("Crypto", "crypto"),
    ("Tech", "tech"),
    ("Climate", "climate"),
    ("Health", "health"),
)

# Sport, games and celebrity are most of Polymarket by volume and none of this dashboard. They
# arrive anyway, because an event can wear several tags, so they are dropped on the way in.
NOT_OURS = {"sports", "esports", "games", "soccer", "nba", "basketball", "nfl", "football",
            "baseball", "mlb", "nhl", "hockey", "tennis", "golf", "ufc", "boxing", "f1",
            "cricket", "olympics", "movies", "music", "awards", "pop culture", "celebrity",
            "mention markets", "trades"}

# A market the crowd has made up its mind about carries no news, so anything this certain is
# left out rather than shown as a finding.
SETTLED = 0.97

# The shorthand Polymarket writes countries in, which the general matcher does not know.
SHORTHAND = {"u.s.": "US", "us": "US", "usa": "US", "u.s.a.": "US", "america": "US",
             "uk": "GB", "u.k.": "GB", "uae": "AE", "drc": "CD", "south korea": "KR",
             "north korea": "KP", "czechia": "CZ"}

# Tags that are about a country without naming it. The Federal Reserve is American whether or
# not the market says so, and a market about Trump is a market about what Washington does.
IMPLIES = {"fed": "US", "fed rates": "US", "us election": "US", "trump": "US",
           "trump presidency": "US", "congress": "US", "supreme court": "US",
           "us government": "US", "midterms": "US"}


# The countries Polymarket keeps a tag for and had open markets under, checked one by one in
# November 2026. It is a starting list, not a guarantee: a tag that has since run dry reaches a
# panel that says no open market names the country, which is what a quiet country gets too. The
# board adds whatever else is busy today, so the list grows on its own.
COVERED: tuple[str, ...] = (
    "AE", "AF", "AM", "AR", "AU", "BE", "BR", "CA", "CH", "CN", "CO", "CU", "DE", "DK", "EC",
    "EG", "ES", "ET", "FI", "FR", "GB", "GE", "GR", "HT", "HU", "ID", "IL", "IN", "IQ", "IR",
    "IT", "JP", "KP", "KR", "LB", "MX", "MY", "NG", "NL", "NO", "NZ", "PE", "PH", "PK", "PL",
    "PT", "QA", "RO", "RS", "RU", "SA", "SD", "SE", "SG", "SY", "TR", "TW", "UA", "US", "VE",
    "YE", "ZA",
)


def _iso(name: str) -> str | None:
    """The ISO-2 code for a country written however Polymarket happens to write it."""
    key = (name or "").strip().lower()
    if not key:
        return None
    if key in SHORTHAND:
        return SHORTHAND[key]
    if key in IMPLIES:
        return IMPLIES[key]
    hit = countries.named_in(name)
    return hit[0] if hit else None


# A grouped event is either a field of candidates ("who") or a ladder of dates ("by when"), and
# the interesting row is not the same one in each. This is how a date is recognised.
DATED = re.compile(r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}"
                   r"|^\d{4}$|^(q[1-4]|h[12])\s*\d{4}$", re.I)


def _outcomes(market: dict) -> tuple[list[str], list[float]]:
    """A market's outcome names and prices. They arrive as JSON *strings*, not arrays."""
    try:
        names = json.loads(market.get("outcomes") or "[]")
        prices = [float(p) for p in json.loads(market.get("outcomePrices") or "[]")]
    except (ValueError, TypeError):
        return [], []
    return (names, prices) if names and len(names) == len(prices) else ([], [])


def _pick(markets: list[dict]) -> tuple[float, str] | None:
    """The one line worth printing for an event: (chance, what it is a chance of).

    A single market speaks for itself — its likeliest outcome. A field of candidates is
    answered by its front-runner. A ladder of dates is different, and getting it wrong is how
    a panel ends up saying "Netanyahu out by June 30, 2027 — 72%": the furthest rung is always
    the likeliest, and so always the least worth reading. The rung that carries information is
    the *soonest* one the crowd already thinks is more likely than not.
    """
    ranked: list[tuple[float, str, dict]] = []
    for market in markets:
        names, prices = _outcomes(market)
        if not names:
            continue
        label = (market.get("groupItemTitle") or "").strip()
        if len(markets) > 1 and label:
            yes = prices[names.index("Yes")] if "Yes" in names else max(prices)
            ranked.append((yes, label, market))
        else:
            best = max(range(len(prices)), key=lambda i: prices[i])
            ranked.append((prices[best], str(names[best]), market))
    if not ranked:
        return None
    if len(ranked) > 1 and all(DATED.match(label) for _, label, _ in ranked):
        rungs = sorted(ranked, key=lambda r: r[2].get("endDate") or "")
        likely = [r for r in rungs if r[0] >= 0.5]
        chance, label, _ = likely[0] if likely else rungs[-1]
        return chance, f"by {label}"
    chance, label, _ = max(ranked, key=lambda r: r[0])
    return chance, label


# The labels that stand for one of our categories beyond the tag we queried by. Polymarket
# writes "Climate & Science" where our slug says climate, and files most economic questions
# under Business or Finance rather than Economy.
ALSO = {
    "Conflict": ("geopolitics", "middle east", "war", "military strikes", "foreign policy",
                 "peace deal", "ceasefire", "nuclear"),
    "Elections": ("elections", "global elections", "world elections", "us election",
                  "main election", "midterms"),
    "Politics": ("politics", "trump presidency", "congress", "supreme court"),
    "Economy": ("economy", "economic policy", "business", "finance", "fed", "fed rates",
                "inflation", "tariffs", "trade", "oil", "ipos"),
    "Crypto": ("crypto", "crypto prices", "bitcoin", "ethereum", "solana", "token launch"),
    "Tech": ("tech", "ai", "big tech", "ai bubble", "openai", "space"),
    "Climate": ("climate", "climate & science", "weather", "science", "natural disasters"),
    "Health": ("health", "medicine", "public health"),
}


def _classify(labels: list[str]) -> str | None:
    """Which of our categories an event's tags put it in, in priority order.

    A query by tag already knows the answer; a query by *country* does not, because "Iran"
    says where and nothing about what. The tags are the same either way, so the same table
    reads them — and an event wearing several is filed under the first CATEGORIES claims,
    which is why "Will China invade Taiwan?" is a conflict and not an election.
    """
    lower = {label.lower() for label in labels}
    return next((name for name, _ in CATEGORIES
                 if lower & set(ALSO[name]) or name.lower() in lower), None)


def _row(event: dict, category: str | None = None) -> dict | None:
    """One event, flattened to what a panel draws. None if it is not ours to show."""
    labels = [(tag.get("label") or "").strip() for tag in (event.get("tags") or [])]
    if {label.lower() for label in labels} & NOT_OURS:
        return None
    category = category or _classify(labels)
    if not category:
        return None
    live = [m for m in (event.get("markets") or []) if m.get("active") and not m.get("closed")]
    picked = _pick(live)
    if not picked or picked[0] >= SETTLED:
        return None
    chance, outcome = picked

    where: list[str] = []
    for name in [event.get("countryName") or ""] + labels:
        code = _iso(name)
        if code and code not in where:
            where.append(code)
    for code in countries.named_in(event.get("title") or ""):
        if code not in where:
            where.append(code)

    return {
        "id": str(event.get("id") or event.get("slug") or ""),
        "question": (event.get("title") or "").strip(),
        "outcome": outcome,
        "chance": chance,
        "volume": float(event.get("volume") or 0),
        "day_volume": float(event.get("volume24hr") or 0),
        "ends": (event.get("endDate") or "")[:10],
        "url": f"https://polymarket.com/event/{event.get('slug', '')}",
        "category": category,
        "countries": where,
        "choices": len(live),
    }


@remember(900)
def _tagged(slug: str, category: str | None = None, limit: int = 20) -> list[dict]:
    """The busiest open events carrying one Polymarket tag — a subject, or a country.

    `category` is passed when the tag asked for *is* one of ours, because then every row it
    returns is that category by definition. A country tag knows no such thing, so its rows
    work out their own category from their tags.
    """
    events = http.json(API, 15, cap=32_000_000,
                       params={"tag_slug": slug, "closed": "false", "active": "true",
                               "order": "volume", "ascending": "false", "limit": limit})
    if not isinstance(events, list):
        return []
    rows = [_row(event, category) for event in events]
    return [row for row in rows if row]


def _slug(code: str) -> str:
    """Polymarket's tag for a country: its name, lowercased and hyphenated."""
    return countries.NAMES.get(code, code).lower().replace(" ", "-").replace(".", "")


@remember(900)
def board() -> list[dict]:
    """Every category's events in one list, each event appearing once.

    The eight queries are eight independent waits on the same server, and run one after
    another they cost six seconds — paid on every cold page load, because the tab this panel
    sits in is rendered whether or not it is the tab on top. Run together they cost about one,
    which is the difference between a panel that is there when the page arrives and a panel
    the page waits for.

    An event wears several tags, so the same race comes back under `elections` and again under
    `politics`. CATEGORIES is in priority order and the first claim wins, which is why an
    election is filed as an election rather than as politics.
    """
    with ThreadPoolExecutor(max_workers=len(CATEGORIES)) as pool:
        fetched = list(pool.map(lambda pair: _tagged(pair[1], pair[0]), CATEGORIES))
    seen: dict[str, dict] = {}
    for rows in fetched:
        for row in rows:
            seen.setdefault(row["id"], row)
    return sorted(seen.values(), key=lambda r: -r["volume"])


def _ongoing(rows: list[dict]) -> list[dict]:
    """Markets that have not quietly run past their own end date."""
    today = date.today().isoformat()
    return [r for r in rows if not r["ends"] or r["ends"] >= today]


def _spread(rows: list[dict], limit: int) -> list[dict]:
    """Busiest first, but no category may take the whole screen.

    Ordering purely by money is how the panel came to show nothing but American primaries:
    the three 2028 nomination races alone carry more than two billion dollars between them.
    Taking the categories in turn costs a little ranking and buys a page that is about the
    world rather than about one election.
    """
    queues: dict[str, list[dict]] = {}
    for row in rows:
        queues.setdefault(row["category"], []).append(row)
    out: list[dict] = []
    while len(out) < limit and any(queues.values()):
        for category, _ in CATEGORIES:
            queue = queues.get(category)
            if queue:
                out.append(queue.pop(0))
                if len(out) >= limit:
                    break
    return out


def questions(country: str | None = None, limit: int = 8, category: str | None = None) -> list[dict]:
    """The open questions worth showing: one country, one category, both, or everything.

    A country is asked for by its own tag rather than sifted out of the board, because the
    board is only the busiest twenty of each subject and a country of ordinary size never
    survives that cut. Polymarket keeps a tag per country, and asking it directly is both
    deeper — thirty Iran questions where the board held nine — and cheaper to download.

    With a filter on, the answer is simply the busiest matches: someone who asked for Iran, or
    for the economy, wants them ranked by money, not rearranged for variety.
    """
    if country:
        rows = _tagged(_slug(country), limit=30)
        # A tag is a subject, not a sworn statement about where. Keep the ones that really do
        # name the country, so "Iran" cannot quietly return a market about American rates.
        rows = [r for r in rows if country in r["countries"]]
        if category:
            rows = [r for r in rows if r["category"] == category]
    elif category:
        rows = _tagged(dict(CATEGORIES)[category], category)
    else:
        rows = board()
    rows = _ongoing(rows)
    rows.sort(key=lambda r: -r["volume"])
    if country or category:
        return rows[:limit]
    return _spread(rows, limit)


def choices() -> tuple[list[tuple[str, int]], list[str]]:
    """What the filters can offer: (categories with their counts, country codes).

    The categories are counted from their own queries rather than from the board, because the
    board keeps one copy of each event and a race that is filed under Elections would leave
    Politics reading zero while its filter still had twelve questions behind it.

    The countries are not counted at all. Counting them would mean a query per country, and
    the list is sixty-two long; a country whose tag has since run dry simply reaches a panel
    that says so, which is the same thing the panel says for a quiet country anyway.
    """
    by_category = [(name, len(_ongoing(_tagged(slug, name)))) for name, slug in CATEGORIES]
    hot: dict[str, int] = {}
    for row in _ongoing(board()):
        for code in row["countries"]:
            hot[code] = hot.get(code, 0) + 1
    known = sorted(set(COVERED) | set(hot), key=lambda c: countries.NAMES.get(c, c))
    return [c for c in by_category if c[1]], known
