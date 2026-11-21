"""Breaking a window of news down one attribute at a time.

A decomposition tree: start with everything, pick a region, then a nation inside it, then a
state, then a city — and at every step see what the level below is made of and how much of
the total each part carries. The same machinery walks a chain of attributes that is not
geography at all (type, then day, then publisher), because "expand the next attribute" is one
operation whatever the attributes happen to be.

Two things this is careful about.

**The score is a sum, so it adds up.** A node's significance is the total severity of the
items under it, which means a parent is exactly the sum of its children and a percentage of
the whole is a real percentage. An average would not add up, and a reader who sees a tree
assumes the numbers in it do.

**A missing level is shown, not skipped.** Only a minority of items name a city the gazetteer
knows, and an item whose city is unknown has not stopped existing — it is held at the level
above under a node that says so. Dropping it would make every city look more significant than
it is, because the denominator would quietly shrink.

Nothing here imports Streamlit. It takes a dataframe and returns dataframes.
"""
from __future__ import annotations

import pandas as pd

from . import countries, places

# What the tree can be broken down by. Each is (key, column, label) — the column is expected
# to exist on the frame `enrich` returns.
PLACE_CHAIN = (("region", "region", "Region"),
               ("country", "country_name", "Nation"),
               ("state", "state", "State"),
               ("city", "city", "City"))

EVENT_CHAIN = (("category", "category_label", "Event code"),
               ("day", "day", "Date"),
               ("country", "country_name", "Country"),
               ("source", "source", "Publisher"))

CHAINS = {"Place — region, nation, state, city": PLACE_CHAIN,
          "Event — code, date, country, publisher": EVENT_CHAIN}

UNKNOWN = "— not identified —"


def _words(value) -> str:
    """A dataframe cell as text. `value or ""` will not do: a NaN is a truthy float."""
    if value is None:
        return ""
    if isinstance(value, float):
        return "" if value != value else str(value)
    return str(value)


def enrich(frame: pd.DataFrame) -> pd.DataFrame:
    """The frame plus the columns the chains group by.

    `region` comes from a lookup table; `state` and `city` come from reading the headline
    against the gazetteer, which succeeds for the minority of items that name a place it
    holds. Everything unresolved becomes UNKNOWN rather than NaN, so it groups instead of
    disappearing.
    """
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["region", "country_name", "state", "city", "day", "score"])

    out = frame.copy()
    out["region"] = out["country"].map(lambda c: countries.region(c) if pd.notna(c) else UNKNOWN)
    out["country_name"] = out["country"].map(
        lambda c: countries.NAMES.get(c, c) if pd.notna(c) else UNKNOWN)

    cities, states = [], []
    for title, summary, country in zip(out.get("title", ""), out.get("summary", ""),
                                       out.get("country", "")):
        cc = country if isinstance(country, str) else None
        hit = places.find(_words(title), cc) or places.find(_words(summary), cc)
        cities.append(hit[0] if hit else UNKNOWN)
        states.append((hit[4] or UNKNOWN) if hit else UNKNOWN)
    out["city"], out["state"] = cities, states

    out["day"] = pd.to_datetime(out["ts"]).dt.strftime("%d %b")
    # Severity is only set on negative items; an item with none still belongs to its branch,
    # it simply adds nothing to the weight of it.
    out["score"] = pd.to_numeric(out.get("severity"), errors="coerce").fillna(0.0)
    if "category_label" not in out:
        out["category_label"] = UNKNOWN
    out["source"] = out.get("source", pd.Series(UNKNOWN, index=out.index)).fillna(UNKNOWN)
    return out


def narrow(frame: pd.DataFrame, chain: tuple, path: dict) -> pd.DataFrame:
    """The rows under a path — {"region": "Southern Asia", "country": "India"} and so on."""
    out = frame
    for key, column, _label in chain:
        chosen = path.get(key)
        if chosen is None:
            break
        out = out[out[column] == chosen]
    return out


def level(frame: pd.DataFrame, chain: tuple, path: dict, depth: int, *,
          limit: int = 14) -> pd.DataFrame:
    """What the level at `depth` is made of, under `path`, heaviest first.

    Columns: value, items, score, share — share being this node's part of the parent, which
    is the number that makes a tree readable: "half of Southern Asia's weight is India".
    """
    empty = pd.DataFrame(columns=["value", "items", "score", "share"])
    if frame.empty or depth >= len(chain):
        return empty
    here = narrow(frame, chain, path)
    if here.empty:
        return empty
    column = chain[depth][1]
    grouped = here.groupby(column, dropna=False).agg(
        items=("score", "size"), score=("score", "sum")).reset_index()
    grouped = grouped.rename(columns={column: "value"})
    whole = grouped["score"].sum()
    grouped["share"] = grouped["score"] / whole if whole else 0.0
    # The unidentified bucket sorts with the rest rather than being pinned anywhere: if it is
    # the biggest thing at this level, that is the most important fact on the screen.
    return grouped.sort_values(["score", "items"], ascending=False).head(limit).reset_index(drop=True)


def detail(frame: pd.DataFrame, chain: tuple, path: dict, *, limit: int = 12) -> pd.DataFrame:
    """The items themselves at the end of a path, heaviest first.

    This is the bottom of the drill: a title, who published it, where to read it, and what it
    contributed to the score of every node above it.
    """
    here = narrow(frame, chain, path)
    if here.empty:
        return pd.DataFrame(columns=["title", "source", "url", "score", "ts"])
    keep = [c for c in ("title", "source", "url", "score", "ts", "country_name", "city")
            if c in here]
    return here.sort_values("score", ascending=False).head(limit)[keep].reset_index(drop=True)


def trail(chain: tuple, path: dict) -> list[tuple[str, str]]:
    """The path so far as (label, value) pairs, for a breadcrumb."""
    out = []
    for key, _column, label in chain:
        if path.get(key) is None:
            break
        out.append((label, str(path[key])))
    return out
