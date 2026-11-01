#!/usr/bin/env python3
"""Fetch the economic series into the repo, because the deployment cannot fetch them itself.

FRED is reachable from a laptop and from a GitHub runner, and not from Streamlit Cloud — the
deployed app times out on every request and every money panel stands empty. Rather than argue
with that, this does what the map layers already do: fetches on a schedule, commits the result,
and lets the dashboard read a file. The deployed app then makes no outbound call for any of
this, which is also why the money panels now paint instantly.

The list is not written out here. It is read from osint/money, which is where the tables live
and where anyone adding a row would put it — a second copy would drift the first time somebody
added one and forgot this file existed.

    python tools/build_series.py

It lands in osint/data/series.json as {built, source, series: {id: [[date, value], …]}}.
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from osint import money                                     # noqa: E402  (after the path fix)

DATA = Path(__file__).resolve().parent.parent / "osint" / "data"
CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
AGENT = "axn-pulse-news-monitor/1.0 (https://synergyaxn.com)"
KEEP = 400          # observations per series: enough for every window the panels offer

# The OECD share-price index exists for its members and a few others, one series each. Which
# ones answer is not documented anywhere useful, so the builder asks and keeps what comes back.
COUNTRIES = ("US GB DE FR IT ES NL BE AT PT IE GR FI DK SE NO PL CZ HU SK SI EE LV LT LU "
             "CH TR IL CA MX BR CL CO CR JP KR AU NZ ZA IN ID CN RU").split()


def observations(series_id: str) -> list[list]:
    """One FRED series as [[date, value], …], oldest first, gaps dropped."""
    reply = requests.get(CSV, params={"id": series_id},
                         headers={"User-Agent": AGENT}, timeout=60)
    if reply.status_code != 200:
        return []
    rows = []
    for line in reply.text.strip().splitlines()[1:]:
        day, _, value = line.partition(",")
        value = value.strip()
        if value in ("", "."):          # a holiday, or a figure not published yet
            continue
        try:
            rows.append([day, float(value)])
        except ValueError:
            continue
    return rows[-KEEP:]


def wanted() -> list[str]:
    """Every series the dashboard asks for, in the order the tables list them."""
    ids = []
    for table in (money.INDICES, money.ENERGY, money.METALS, money.GRAINS, money.MACRO):
        ids += [row[0] for row in table]
    ids += [f"SPASTT01{cc}M661N" for cc in COUNTRIES]
    return list(dict.fromkeys(ids))     # ordered, without repeats


def main() -> int:
    series, missing = {}, []
    for series_id in wanted():
        print(f"  {series_id:22}", end=" ", flush=True)
        try:
            rows = observations(series_id)
        except Exception as problem:    # one bad series must not lose the others
            print(f"failed: {type(problem).__name__}")
            missing.append(series_id)
            continue
        if not rows:
            print("nothing published")
            missing.append(series_id)
            continue
        series[series_id] = rows
        print(f"{len(rows):>4} points  to {rows[-1][0]}")

    if not series:
        print("\nFRED gave us nothing at all — the old file was left alone", file=sys.stderr)
        return 1

    path = DATA / "series.json"
    path.write_text(json.dumps({
        "built": datetime.date.today().isoformat(),
        "source": "Federal Reserve Bank of St Louis (FRED)",
        "series": series,
    }, separators=(",", ":")))
    print(f"\n{len(series)} series  {path.stat().st_size / 1024:,.0f} KB  -> {path}")
    if missing:
        # Not a failure: the OECD does not publish a share-price index for every country asked
        print(f"{len(missing)} had nothing: {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
