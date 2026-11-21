"""Which kind of event drags the severity rating up, and by how much.

The question this answers is not "what is there a lot of" — the bar charts already answer
that, and volume is mostly a fact about how many outlets cover a subject. It is: *given that an
item is of this kind, how much harsher is it rated than everything else in the window?*

That difference is the impact score. It is a difference of two means — the mean severity of
one category against the mean severity of every other category in the same window — so it
comes with the usual obligation to say how sure we are. With a handful of items a category can
look devastating by accident; the confidence interval is what stops a bucket of four from
being reported as the most dangerous subject of the week.

Deliberately plain statistics. There is no scipy here and adding it for one function would be
a poor trade, so the interval is the normal approximation (±1.96 standard errors) rather than
Student's t. At the sample sizes this dashboard works with — hundreds to thousands of items a
window — the two agree to about a hundredth of a severity point. Under about thirty items they
do not, which is exactly where `minimum` holds a category back instead.

Nothing here touches Streamlit or the database: it takes a dataframe and returns one.
"""
from __future__ import annotations

import math

import pandas as pd

# 95%, two-sided, normal approximation. See the module note on why this is not a t critical.
Z = 1.959964


def influence(frame: pd.DataFrame, *, by: str = "category_label", value: str = "severity",
              minimum: int = 25) -> pd.DataFrame:
    """One row per code: how many, how harsh, and how much harsher than the rest.

    Columns: code, items, rating, others, impact, low, high, certain, share.

      * `rating`  — this code's mean severity
      * `others`  — the mean severity of everything *else* in the window, which is the thing
                    the code is being compared against rather than the overall mean (a code
                    is part of the overall mean, so comparing to it shrinks its own effect)
      * `impact`  — rating − others, the number the panel ranks on
      * `low/high`— the 95% interval around that difference
      * `certain` — whether the interval clears zero, i.e. whether we would say this out loud

    A code with fewer than `minimum` rated items is returned with `certain` False rather than
    dropped: "not enough to tell" is a finding, and a category that vanishes from the table
    looks like a category with nothing in it.
    """
    empty = pd.DataFrame(columns=["code", "items", "rating", "others", "impact",
                                  "low", "high", "certain", "share"])
    if frame is None or frame.empty or by not in frame or value not in frame:
        return empty
    rated = frame[frame[value].notna() & frame[by].notna()]
    if len(rated) < 2:
        return empty

    out = []
    for code, group in rated.groupby(by):
        rest = rated[rated[by] != code]
        n_a, n_b = len(group), len(rest)
        if n_a < 2 or n_b < 2:
            continue
        mean_a = float(group[value].mean())
        mean_b = float(rest[value].mean())
        # Sample variances; ddof=1 because these are samples of a day's news, not the whole of it.
        var_a = float(group[value].var(ddof=1) or 0.0)
        var_b = float(rest[value].var(ddof=1) or 0.0)
        error = math.sqrt(var_a / n_a + var_b / n_b)
        margin = Z * error
        difference = mean_a - mean_b
        out.append({
            "code": str(code),
            "items": n_a,
            "rating": mean_a,
            "others": mean_b,
            "impact": difference,
            "low": difference - margin,
            "high": difference + margin,
            "certain": bool(n_a >= minimum and (difference - margin) * (difference + margin) > 0),
            "share": n_a / len(rated),
        })
    if not out:
        return empty
    return pd.DataFrame(out).sort_values("impact", ascending=False).reset_index(drop=True)


def insight(rows: pd.DataFrame, *, window: str = "this range") -> list[str]:
    """What the table says, in sentences, for a reader who will not read the table.

    Only the findings the interval supports are stated as findings. The rest are reported as
    what they are — too few items to tell — because a dashboard that phrases every wobble as a
    discovery teaches its reader to ignore it.
    """
    if rows.empty:
        return ["Nothing is rated in this window yet, so there is nothing to compare."]

    lines: list[str] = []
    sure = rows[rows["certain"]]
    top = sure.iloc[0] if not sure.empty else None
    if top is not None and top["impact"] > 0:
        lines.append(
            f"**{top['code']}** is rated **{top['impact']:+.2f}** above everything else in "
            f"{window} — {top['rating']:.2f} against {top['others']:.2f}, across "
            f"{int(top['items']):,} items. The 95% interval is "
            f"{top['low']:+.2f} to {top['high']:+.2f}, so the gap is not noise.")
    else:
        lines.append(
            f"No category is reliably harsher than the rest in {window}. The differences that "
            "are there could all be the luck of which stories arrived.")

    gentle = sure[sure["impact"] < 0]
    if not gentle.empty:
        least = gentle.iloc[-1]
        lines.append(
            f"At the other end, **{least['code']}** runs **{least['impact']:+.2f}** below the "
            f"rest ({least['rating']:.2f} against {least['others']:.2f}).")

    thin = rows[(~rows["certain"]) & (rows["items"] < 25)]
    if not thin.empty:
        names = ", ".join(f"{r.code} ({int(r.items)})" for r in thin.head(4).itertuples())
        lines.append(f"Too few rated items to judge: {names}.")

    loud = rows.sort_values("items", ascending=False).iloc[0]
    if loud["code"] != (top["code"] if top is not None else None):
        lines.append(
            f"The busiest category is **{loud['code']}** at {int(loud['items']):,} items "
            f"({loud['share']:.0%} of what is rated), which is a fact about how many outlets "
            f"cover the subject rather than about how bad it is.")
    return lines


# ─── Correlation coefficient ──────────────────────────────────────────────────
# A different question from the one above, and worth keeping apart from it. The impact score
# asks "are items of this kind rated harsher"; this asks "do these two kinds of event rise and
# fall together". The first is about severity, the second about timing, and neither implies the
# other: two subjects can move in perfect step while one is rated twice as harshly as the other.
#
# What is correlated is two *count series* — how many items of each kind landed in each hour
# (or day) of the window — because a correlation needs paired observations and individual news
# items are not paired with anything.

MIN_POINTS = 6        # fewer buckets than this and a coefficient is an accident


def buckets(frame: pd.DataFrame, *, freq: str, start: pd.Timestamp, end: pd.Timestamp,
            column: str = "ts") -> pd.Series:
    """Counts per time bucket over the whole window, with empty buckets as real zeros.

    The zero-fill matters more than it looks. Dropping empty buckets would pair hour 3 of one
    series with hour 7 of the other and correlate two things that never happened together.
    """
    index = pd.date_range(start.floor(freq), end, freq=freq)
    if frame is None or frame.empty or column not in frame:
        return pd.Series(0.0, index=index)
    counted = frame[column].dt.floor(freq).value_counts()
    return counted.reindex(index, fill_value=0).astype(float)


def as_share(part: pd.Series, whole: pd.Series) -> pd.Series:
    """`part` as a fraction of `whole`, with an empty bucket counting as nought.

    Worth having beside the raw counts, because on counts alone almost every pair of
    categories correlates at around 0.6 in this collection — not because the subjects are
    related but because a busy hour is busy for all of them, and the common driver shows up
    in every pair. Dividing by the hour's own total takes that driver out: what is left is
    "when this subject takes a larger share of the news, does that one too", which is the
    question the raw figure looked like it was answering and was not.
    """
    return (part / whole.where(whole > 0)).fillna(0.0)


def pearson(x: pd.Series, y: pd.Series) -> dict:
    """Pearson's r for two aligned series, with a 95% interval and a least-squares line.

    The interval is Fisher's z transform — atanh(r), which is near enough normal with standard
    error 1/sqrt(n-3), then transformed back. It needs no scipy and it is the standard way the
    interval is built.

    One caveat the caller is expected to print rather than bury: the interval assumes the
    buckets are independent observations, and hourly news counts are not — a busy afternoon
    stays busy. Autocorrelation makes the effective sample smaller than n, so a real interval
    is wider than this one. It is honest about the sign and optimistic about the width.
    """
    out = {"r": float("nan"), "n": int(len(x)), "low": None, "high": None,
           "clear": False, "slope": 0.0, "intercept": 0.0}
    if len(x) < MIN_POINTS or len(x) != len(y):
        return out
    mean_x, mean_y = float(x.mean()), float(y.mean())
    dx, dy = x - mean_x, y - mean_y
    sxx, syy, sxy = float((dx * dx).sum()), float((dy * dy).sum()), float((dx * dy).sum())
    # A series that never moves has no correlation to find — not a correlation of zero, which
    # would claim we looked and found independence.
    if sxx <= 0 or syy <= 0:
        return out
    out["r"] = sxy / math.sqrt(sxx * syy)
    out["slope"] = sxy / sxx
    out["intercept"] = mean_y - out["slope"] * mean_x
    if out["n"] > 3 and abs(out["r"]) < 0.9999:
        spread = math.atanh(out["r"]), 1 / math.sqrt(out["n"] - 3)
        out["low"] = math.tanh(spread[0] - Z * spread[1])
        out["high"] = math.tanh(spread[0] + Z * spread[1])
        out["clear"] = out["low"] > 0 or out["high"] < 0
    return out


def strongest(frame: pd.DataFrame, column: str = "category_label", *, freq: str,
              start: pd.Timestamp, end: pd.Timestamp, limit: int = 8,
              minimum: int = 20) -> pd.DataFrame:
    """Every pair of categories in the window, ranked by how strongly they move together.

    Ranked on |r|, so a pair that moves in opposite directions is as interesting as one that
    moves in step — which it is, and a ranking on r alone would bury it at the bottom.
    """
    empty = pd.DataFrame(columns=["a", "b", "r", "low", "high", "clear"])
    if frame is None or frame.empty or column not in frame:
        return empty
    counts = frame[column].value_counts()
    names = [name for name, n in counts.items() if n >= minimum]
    if len(names) < 2:
        return empty
    series = {name: buckets(frame[frame[column] == name], freq=freq, start=start, end=end)
              for name in names}
    rows = []
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            fit = pearson(series[first], series[second])
            if fit["r"] != fit["r"]:          # NaN: nothing to rank
                continue
            rows.append({"a": first, "b": second, "r": fit["r"],
                         "low": fit["low"], "high": fit["high"], "clear": fit["clear"]})
    if not rows:
        return empty
    out = pd.DataFrame(rows)
    return out.reindex(out["r"].abs().sort_values(ascending=False).index).head(limit)
