"""Themes cut across the categories, and what the news does against what a market does.

The analyzer files every item under one category — Political unrest, Economy & prices, and so
on — which is the right way to ask "what is this country's news about". It is the wrong way to
ask "what is happening in cyber", because a ransomware attack on a hospital lands under Health
and a state hack lands under Security. So this module cuts a second way across the same items,
by what they are about rather than how they were filed, and lets the two coexist.

The correlation half is deliberately modest. Seven daily points cannot establish that news
moves a market, and the interval this returns is wide enough to say so out loud. It is here to
show the two lines against each other and to be honest about how little that proves.
"""
from __future__ import annotations

import math

import pandas as pd

# What each theme is made of. Matched against the headline, not the category, because the
# category answers a different question — see the note above.
THEMES: dict[str, str] = {
    "Military": r"military|troops?|army|navy|air ?strike|missile|drone|offensive|ceasefire|"
                r"warship|soldiers?|deployment|nato|artillery|frontline|invasion",
    "Cyber": r"cyber|hack(?:ed|er|ing)?|ransomware|data breach|malware|phishing|ddos|"
             r"data leak|spyware|zero.day",
    "Nuclear": r"nuclear|uranium|enrich(?:ment|ed)?|reactor|iaea|warhead|atomic|plutonium",
    "Energy": r"\boil\b|\bgas\b|pipeline|opec|refiner(?:y|ies)|lng|crude|electricity|"
              r"blackout|power grid|barrel",
    "Sanctions": r"sanction|embargo|tariff|export control|blacklist|frozen assets|"
                 r"trade ban|restrictions? on",
    "Unrest": r"protest|riot|demonstration|clash(?:es)?|uprising|coup|strike action|unrest",
    "AI & tech": r"\bai\b|artificial intelligence|machine learning|chatbot|openai|anthropic|"
                 r"nvidia|semiconductor|\bchips?\b|data cent(?:re|er)|algorithm|\bllm\b|"
                 r"quantum comput|robotics?",
}


def match(frame: pd.DataFrame, theme: str) -> pd.DataFrame:
    """Every item whose headline is about this theme, newest first."""
    words = THEMES.get(theme)
    if frame.empty or not words:
        return frame.iloc[0:0]
    hit = frame["title"].fillna("").str.contains(words, case=False, regex=True)
    return frame[hit].sort_values("ts", ascending=False)


def daily(frame: pd.DataFrame, days: int = 14) -> pd.DataFrame:
    """One row per day: how many items, and how they read. Days with no news are kept at zero —
    a gap in a line is a day nobody looked, which is not the same as a quiet day."""
    if frame.empty:
        return pd.DataFrame(columns=["day", "volume", "tone"])
    rows = frame.dropna(subset=["ts"]).copy()
    rows["day"] = pd.to_datetime(rows["ts"]).dt.normalize()
    grouped = rows.groupby("day").agg(volume=("id", "size"), tone=("score", "mean"))
    last = grouped.index.max()
    span = pd.date_range(end=last, periods=days, freq="D")
    return (grouped.reindex(span).fillna({"volume": 0}).rename_axis("day").reset_index())


def pearson(xs: list[float], ys: list[float]) -> dict | None:
    """Pearson's r with a 95% interval, and what that pair is worth saying in words.

    The interval comes from Fisher's z, which is the usual transform for this: r is bounded at
    ±1 so its own sampling distribution is skewed, and a plain ± would run off the end. With
    the handful of daily points a week gives, the interval is normally wide enough to contain
    zero — which is the finding, and the panel says so rather than quoting r on its own.
    """
    pairs = [(x, y) for x, y in zip(xs, ys)
             if x is not None and y is not None
             and not (isinstance(x, float) and math.isnan(x))
             and not (isinstance(y, float) and math.isnan(y))]
    n = len(pairs)
    if n < 4:
        return None

    mx = sum(x for x, _ in pairs) / n
    my = sum(y for _, y in pairs) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x, _ in pairs))
    sy = math.sqrt(sum((y - my) ** 2 for _, y in pairs))
    if not sx or not sy:          # a flat week on either side has no correlation to report
        return None
    r = sum((x - mx) * (y - my) for x, y in pairs) / (sx * sy)
    r = max(-0.999999, min(0.999999, r))

    half = 1.96 / math.sqrt(n - 3)
    z = math.atanh(r)
    lo, hi = math.tanh(z - half), math.tanh(z + half)

    if lo <= 0 <= hi:
        words = "No clear relationship"
    else:
        strength = ("Weak" if abs(r) < 0.4 else "Moderate" if abs(r) < 0.6 else "Strong")
        words = f"{strength}, {'together' if r > 0 else 'opposed'}"
    return {"r": r, "lo": lo, "hi": hi, "words": words, "n": n}


def against(news: pd.DataFrame, market: list[tuple[str, float]]) -> pd.DataFrame:
    """The daily news counts and the market's closes on the days both of them have.

    Markets shut at weekends and the news does not, so an inner join is the only honest
    alignment — carrying Friday's close through Sunday would invent two points of agreement.
    """
    if news.empty or not market:
        return pd.DataFrame(columns=["day", "volume", "price"])
    prices = pd.DataFrame(market, columns=["day", "price"])
    prices["day"] = pd.to_datetime(prices["day"])
    return news.merge(prices, on="day", how="inner")
