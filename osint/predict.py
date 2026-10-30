"""What people are betting will happen — Polymarket's open markets.

A prediction market is the one public number that is explicitly about the future, which is why
it earns a panel next to news that is all about the past. Polymarket's gamma API is open and
needs no key.

Two filters stand between the raw list and the panel, and both are deliberate:

  * by subject, because the busiest markets by money are often sport or novelty ("Will Jesus
    Christ return before 2027?" outranks most wars), and a geopolitics dashboard that shows
    them looks like it is not reading its own data;
  * by country, using the same name matcher the outbreak layer uses, so picking a country on
    the globe narrows the questions to the ones that name it.
"""
from __future__ import annotations

import json

import requests
from .cache import remember

from . import countries

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; axn-pulse-news-monitor/1.0)"}
API = "https://gamma-api.polymarket.com/markets"

# What counts as our subject. Kept broad on purpose: the country filter does the narrowing,
# and a question about a tariff or an oil price belongs on this dashboard as much as a war does.
SUBJECTS = (
    "war ceasefire invade invasion strike strikes troops military nuclear missile drone "
    "sanction sanctions embargo blockade coup regime election elections president "
    "prime minister chancellor parliament referendum independence treaty summit nato "
    "ukraine russia china taiwan iran israel gaza korea venezuela sudan "
    "tariff tariffs trade recession inflation rate cut rate hike fed opec oil gas "
    "shutdown default debt ceiling impeach resign hostage prisoner peace deal"
).split()


@remember(900)
def _open_markets(pages: int = 3) -> list[dict]:
    """The busiest open markets, flattened to what a panel needs."""
    rows: list[dict] = []
    for page in range(pages):
        try:
            reply = requests.get(API, params={"closed": "false", "active": "true",
                                              "order": "volumeNum", "ascending": "false",
                                              "limit": 100, "offset": page * 100},
                                 headers=HEADERS, timeout=15)
            reply.raise_for_status()
            batch = reply.json()
        except (requests.RequestException, ValueError):
            break
        if not batch:
            break
        for market in batch:
            question = (market.get("question") or "").strip()
            if not question:
                continue
            # outcomes and prices arrive as JSON *strings*, not arrays
            try:
                names = json.loads(market.get("outcomes") or "[]")
                prices = [float(p) for p in json.loads(market.get("outcomePrices") or "[]")]
            except (ValueError, TypeError):
                continue
            if not names or len(names) != len(prices):
                continue
            best = max(range(len(prices)), key=lambda i: prices[i])
            rows.append({
                "question": question,
                "outcome": str(names[best]),
                "chance": prices[best],
                "volume": float(market.get("volumeNum") or 0),
                "day_volume": float(market.get("volume24hr") or 0),
                "ends": (market.get("endDate") or "")[:10],
                "url": f"https://polymarket.com/market/{market.get('slug', '')}",
                "countries": countries.named_in(question),
                "words": set(question.lower().replace("?", " ").replace(",", " ").split()),
            })
    return rows


# A market the crowd has made up its mind about carries no news. The busiest list by money is
# full of them — several dozen "will <celebrity> win the 2028 election?" markets all sitting at
# 100% No — so anything this certain is left out rather than shown as a finding.
SETTLED = 0.97


def questions(country: str | None = None, limit: int = 8) -> list[dict]:
    """The open questions worth showing, busiest first — narrowed to one country if asked."""
    rows = [r for r in _open_markets() if r["chance"] < SETTLED]
    if country:
        rows = [r for r in rows if country in r["countries"]]
    else:
        rows = [r for r in rows if r["words"] & set(SUBJECTS)]
    return sorted(rows, key=lambda r: -r["volume"])[:limit]
