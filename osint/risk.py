"""An instability reading per country, built only from things we can show our working for.

Every ranking of countries by danger is somebody's opinion dressed as a number. This one says
plainly what it adds up, and the panel prints the parts beside the total so a reader can
disagree with the weighting rather than with a mystery:

  displaced   how many people have fled the country or been uprooted inside it, against its
              population — UNHCR, the only one of the four that measures an outcome rather
              than an event
  hazard      disasters the UN and the European Commission currently rate orange or red — GDACS
  alarm       how negative this monitor's own news from the country is, and how severe,
              damped until there are enough items for the tone to mean anything
  volume      how much of that news there is at all, which is a crude proxy for attention

A score is out of 100 and is a rank, not a probability. A country this monitor collects little
from scores low on the last two for that reason alone, and the panel says so.
"""
from __future__ import annotations

import math

import pandas as pd
import requests
import streamlit as st

from . import feeds, geo, world

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; axn-pulse-news-monitor/1.0)"}

WEIGHTS = {"displaced": 0.40, "hazard": 0.20, "alarm": 0.25, "volume": 0.15}


@st.cache_data(ttl=86400, show_spinner=False)
def _populations() -> dict[str, float]:
    """Every country's population in one call, so the displaced share can be worked out."""
    try:
        reply = requests.get("https://api.worldbank.org/v2/country/all/indicator/SP.POP.TOTL",
                             params={"format": "json", "mrnev": 1, "per_page": 400},
                             headers=HEADERS, timeout=15)
        reply.raise_for_status()
        rows = reply.json()[1] or []
    except (requests.RequestException, ValueError, IndexError, KeyError):
        return {}
    return {row["countryiso3code"]: float(row["value"])
            for row in rows if row.get("value") and row.get("countryiso3code")}


def _displaced_share() -> dict[str, float]:
    """ISO-3 → the share of its people who are refugees or displaced at home, 0–1."""
    origins, _ = world.biggest_origins(limit=300)
    people = _populations()
    out = {}
    for row in origins:
        head_count = people.get(row["iso3"])
        if head_count:
            out[row["iso3"]] = (row["refugees"] + row["idps"]) / head_count
    return out


def _alert_counts() -> dict[str, int]:
    """ISO-2 → how many orange or red disaster alerts stand in it right now."""
    points, _ = feeds.disasters(days=7)
    counts: dict[str, int] = {}
    for point in points:
        # feeds.disasters hands on the colour it will draw, not GDACS's own word: red and
        # amber are that feed's names for red and orange, and green arrives as "dim".
        if str(point.get("level", "")).lower() not in ("amber", "red"):
            continue
        cc = geo.country_of(point["lat"], point["lng"])
        if cc:
            counts[cc] = counts.get(cc, 0) + 1
    return counts


def _scale(value: float, full: float) -> float:
    """0–100, bending so that the crowded low end still separates. log, not linear."""
    if value <= 0:
        return 0.0
    return min(100.0, 100 * math.log1p(value / full * 9) / math.log(10))


def table(news: pd.DataFrame) -> pd.DataFrame:
    """One row per country with a score and the four parts it is made of."""
    displaced = _displaced_share()
    alerts = _alert_counts()

    by_country = {}
    if not news.empty:
        located = news[news["country"].notna()]
        busiest = located.groupby("country").size().max() or 1
        for cc, rows in located.groupby("country"):
            negative = rows[rows["sentiment"] == "negative"]
            share = len(negative) / len(rows)
            severity = negative["severity"].mean() if len(negative) else 0
            # A single angry headline is not a country in crisis. Alarm is damped until
            # there is enough of our own news to mean anything, which is why a country we
            # barely collect from cannot reach the top of this table on tone alone.
            confidence = min(1.0, len(rows) / 8)
            by_country[cc] = {
                "alarm": _scale(share * (severity or 0), 5.0) * confidence,
                "volume": _scale(len(rows), busiest),
                "items": len(rows),
            }

    codes = set(by_country) | {cc for cc in alerts}
    codes |= {cc for cc in (world._codes() or {}) if world.iso3(cc) in displaced}

    rows = []
    for cc in codes:
        three = world.iso3(cc)
        parts = {
            "displaced": _scale(displaced.get(three, 0.0), 0.25),
            "hazard": _scale(alerts.get(cc, 0), 4),
            "alarm": by_country.get(cc, {}).get("alarm", 0.0),
            "volume": by_country.get(cc, {}).get("volume", 0.0),
        }
        total = sum(parts[name] * weight for name, weight in WEIGHTS.items())
        rows.append({"cc": cc, "score": round(total, 1),
                     "items": by_country.get(cc, {}).get("items", 0),
                     **{name: round(value) for name, value in parts.items()}})
    frame = pd.DataFrame(rows)
    return frame.sort_values("score", ascending=False).reset_index(drop=True) if len(frame) else frame


def one(cc: str, news: pd.DataFrame) -> dict | None:
    """The row for a single country, with its rank among the rest."""
    frame = table(news)
    if frame.empty or cc not in set(frame["cc"]):
        return None
    hit = frame[frame["cc"] == cc].iloc[0].to_dict()
    hit["rank"] = int(frame.index[frame["cc"] == cc][0]) + 1
    hit["of"] = len(frame)
    return hit
