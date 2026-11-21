"""Where the daily count is heading, and how little that is worth knowing yet.

Holt's linear trend with damping, fitted by searching the three parameters for the lowest
sum of squared one-step errors. Damping matters more than anything else here: an undamped
trend fitted to two good weeks projects a straight line to the moon, and the first thing a
reader does with a forecast is believe it.

Three things this is careful about, and the third is the important one.

**The last day is thrown away.** A day that is still happening always looks like a collapse —
at nine in the morning it holds a ninth of its eventual count — and a model fitted through it
learns a downward trend that does not exist.

**The interval widens.** One day out is a guess; seven days out is barely a guess. The band
is the residual spread times the square root of the horizon, which is the right shape for a
series whose errors accumulate.

**This is a forecast of our own collection, not of the world.** The number counts items this
monitor stored. It moves when a feed is added, when a run fails, when the collector is paused
— the thing it measures is partly us. `describe` returns the sentence that says so, and the
panel is expected to print it rather than hide it.
"""
from __future__ import annotations

import math

import pandas as pd

Z = 1.959964          # 95%, two-sided, normal approximation
MIN_DAYS = 5          # fewer whole days than this and there is nothing to fit


def daily(frame: pd.DataFrame, column: str = "collected_at") -> pd.Series:
    """One count per calendar day, gaps filled with zero, the unfinished last day dropped."""
    if frame is None or frame.empty or column not in frame:
        return pd.Series(dtype=float)
    when = pd.to_datetime(frame[column], errors="coerce", utc=True, format="mixed").dropna()
    if when.empty:
        return pd.Series(dtype=float)
    counts = when.dt.tz_convert(None).dt.floor("D").value_counts().sort_index()
    # A day with no collection is a real zero, not a missing row; leaving it out would close
    # the gap and hide the outage.
    whole = counts.reindex(pd.date_range(counts.index.min(), counts.index.max(), freq="D"),
                           fill_value=0).astype(float)
    return whole.iloc[:-1] if len(whole) > 1 else whole


def _fit(values: list[float], alpha: float, beta: float, phi: float) -> tuple[float, float, float]:
    """One pass of damped Holt. Returns (level, trend, sum of squared one-step errors)."""
    level, trend, sse = values[0], values[1] - values[0], 0.0
    for actual in values[1:]:
        ahead = level + phi * trend
        sse += (actual - ahead) ** 2
        level, previous = alpha * actual + (1 - alpha) * ahead, trend
        trend = beta * (level - (ahead - phi * previous)) + (1 - beta) * phi * previous
    return level, trend, sse


def project(series: pd.Series, horizon: int = 7) -> pd.DataFrame:
    """History and forecast in one frame: day, value, kind, low, high.

    `kind` is "actual" or "forecast". The bounds are empty on the actual rows, because a
    number we counted has no interval around it.
    """
    empty = pd.DataFrame(columns=["day", "value", "kind", "low", "high"])
    if series is None or len(series) < MIN_DAYS:
        return empty

    values = [float(v) for v in series.to_list()]
    best = None
    # A small grid rather than an optimiser: three parameters, a coarse enough surface, and
    # no dependency to add for it. phi below 1 is the damping that keeps the line honest.
    for alpha in (0.1, 0.2, 0.3, 0.45, 0.6, 0.8):
        for beta in (0.02, 0.05, 0.1, 0.2, 0.35):
            for phi in (0.75, 0.85, 0.92, 0.98):
                level, trend, sse = _fit(values, alpha, beta, phi)
                if best is None or sse < best[0]:
                    best = (sse, alpha, beta, phi, level, trend)
    sse, alpha, beta, phi, level, trend = best
    sigma = math.sqrt(sse / max(len(values) - 2, 1))

    history = pd.DataFrame({"day": series.index, "value": values, "kind": "actual",
                            "low": pd.NA, "high": pd.NA})
    rows, damped = [], 0.0
    last_day = pd.Timestamp(series.index[-1])
    for step in range(1, horizon + 1):
        damped += phi ** step
        point = max(0.0, level + damped * trend)
        spread = Z * sigma * math.sqrt(step)
        rows.append({"day": last_day + pd.Timedelta(days=step), "value": point,
                     "kind": "forecast", "low": max(0.0, point - spread), "high": point + spread})
    return pd.concat([history, pd.DataFrame(rows)], ignore_index=True)


def spikes(series: pd.Series, sensitivity: float = 2.0) -> pd.Series:
    """The days that stand out from the ones around them, as a boolean series.

    Measured against a seven-day rolling median rather than the overall mean, so a steady
    climb is not read as a week of spikes and one loud Tuesday still is.
    """
    if series is None or len(series) < MIN_DAYS:
        return pd.Series(dtype=bool)
    middle = series.rolling(7, min_periods=3, center=True).median()
    gap = series - middle
    spread = gap.abs().median() or 1.0
    return gap.abs() > sensitivity * spread * 1.4826      # MAD scaled to a standard deviation


def describe(series: pd.Series) -> str:
    """The caveat the panel must print, sized to how much history there actually is."""
    days = len(series)
    if days < MIN_DAYS:
        return (f"Only {days} whole day{'s' if days != 1 else ''} of collection — too few to "
                "project anything from. The forecast appears once there are five.")
    weak = ("The model has no weekly cycle in it: that needs six to eight weeks of history "
            "and there are only %d days, so a quiet Sunday is noise to it rather than a "
            "pattern." % days) if days < 42 else (
        "A weekly cycle is visible in %d days of history and the damped trend follows it "
        "loosely." % days)
    return (f"{weak} And the series counts what this monitor collected, not what happened: "
            "it moves when a feed is added or a run fails, so read it as a forecast of our "
            "own throughput.")
