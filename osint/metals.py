"""The metals that get fought over, and what a euro buys.

Two feeds that neither charge nor ask for a key, which is rarer than it sounds for prices.

Spot metal comes from Swissquote's public quote feed — a bank publishing the two-sided market
it is willing to trade on. That is a dealable price rather than an official fix, so it moves
all day and will not match the LBMA afternoon benchmark to the cent; the mid is what this
takes. LBMA's own feed sits behind a bot check, and FRED retired its gold series, so for
anything keyless this is the honest option.

The euro rates are the European Central Bank's daily reference rates, published once each
working day at about 16:00 CET. They are the rates the ECB itself cites, which is why a
Tuesday afternoon reading does not move again until Wednesday — a reference, not a market.
"""
from __future__ import annotations

import requests
from .cache import remember

QUOTES = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/{}/USD"
ECB = "https://data-api.ecb.europa.eu/service/data/EXR/D.{}.EUR.SP00.A"
HEADERS = {"User-Agent": "axn-pulse-news-monitor/1.0"}
TIMEOUT = 12

# What each symbol is, and the unit the market quotes it in — a troy ounce for all four.
SPOT = [("XAU", "Gold"), ("XAG", "Silver"), ("XPT", "Platinum"), ("XPD", "Palladium")]

# The euro pairs worth a row. Dollar first because it is the one everything else is read
# against; the renminbi last because it is the one that moves least and means most.
PAIRS = ["USD", "GBP", "JPY", "CHF", "CAD", "CNY"]


@remember(900)
def spot() -> list[dict]:
    """Gold, silver, platinum and palladium in dollars an ounce, mid of the quoted market."""
    out = []
    for symbol, name in SPOT:
        try:
            reply = requests.get(QUOTES.format(symbol), headers=HEADERS, timeout=TIMEOUT)
            reply.raise_for_status()
            blocks = reply.json()
        except (requests.RequestException, ValueError):
            continue

        # The feed answers per platform, each with several spread profiles. The tightest is
        # the one a price gets quoted at, so take the narrowest rather than the first.
        best = None
        for block in blocks if isinstance(blocks, list) else []:
            for price in block.get("spreadProfilePrices") or []:
                bid, ask = price.get("bid"), price.get("ask")
                if bid and ask and (best is None or ask - bid < best[1] - best[0]):
                    best = (bid, ask)
        if best:
            out.append({"name": name, "symbol": symbol, "value": (best[0] + best[1]) / 2,
                        "spread": best[1] - best[0]})
    return out


@remember(3600)
def euro() -> list[dict]:
    """The ECB's daily reference rates, with the move since the previous working day."""
    try:
        reply = requests.get(ECB.format("+".join(PAIRS)), headers=HEADERS, timeout=TIMEOUT,
                             params={"lastNObservations": 2, "format": "jsondata"})
        reply.raise_for_status()
        body = reply.json()
        names = body["structure"]["dimensions"]["series"][1]["values"]
        series = body["dataSets"][0]["series"]
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return []

    out = []
    for key, block in series.items():
        # The key is one index per dimension, "0:3:0:0:0"; the second is the currency, and it
        # indexes the list above. Reading it by position would assume the order we asked in,
        # which the portal does not promise.
        try:
            code = names[int(key.split(":")[1])]["id"]
        except (ValueError, IndexError, KeyError):
            continue
        points = [v[0] for _, v in sorted(block.get("observations", {}).items(),
                                          key=lambda kv: int(kv[0])) if v and v[0] is not None]
        if not points:
            continue
        out.append({"name": f"EUR/{code}", "value": points[-1],
                    "change": points[-1] - points[-2] if len(points) > 1 else None})
    return sorted(out, key=lambda row: PAIRS.index(row["name"][4:])
                  if row["name"][4:] in PAIRS else 99)
