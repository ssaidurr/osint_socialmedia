"""Rebuild osint/cities.json from GeoNames, with the state each city sits in.

The gazetteer used to hold name, country, latitude, longitude and population, which is all a
map pin needs. A drill-down from nation to state to city needs one more column — the admin-1
division, which GeoNames calls the first-order administrative division and everyone else
calls a state, province, oblast, governorate or département depending on where they live.

Two files, both public (GeoNames, CC-BY 4.0):

  cities15000.zip        every settlement over 15,000 people, tab separated
  admin1CodesASCII.txt   "IN.09" -> "Karnataka", for turning a code into a name

Only cities over POPULATION are kept, which is the cut the old file was built on and the
reason it holds twelve thousand rows rather than twenty-five.

    python tools/build_cities.py

The sixth column is appended rather than inserted, so anything still reading the first five
by position keeps working.
"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from osint import http  # noqa: E402

DUMP = "https://download.geonames.org/export/dump"
OUT = Path(__file__).resolve().parent.parent / "osint" / "cities.json"
POPULATION = 50_000
BUDGET = 180


def admin_names() -> dict[str, str]:
    """"IN.09" -> "Karnataka". The file is code, name, ascii name, geonameid."""
    body = http.get(f"{DUMP}/admin1CodesASCII.txt", BUDGET, cap=8_000_000)
    if not body:
        raise SystemExit("admin1CodesASCII.txt did not arrive")
    out = {}
    for line in body.decode("utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0]:
            out[parts[0]] = parts[1]
    return out


def cities(states: dict[str, str]) -> list[list]:
    body = http.get(f"{DUMP}/cities15000.zip", BUDGET, cap=40_000_000)
    if not body:
        raise SystemExit("cities15000.zip did not arrive")
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        raw = archive.read("cities15000.txt").decode("utf-8")

    rows = []
    for line in raw.splitlines():
        field = line.split("\t")
        if len(field) < 15:
            continue
        name, country, admin1, population = field[1], field[8], field[10], field[14]
        try:
            pop = int(population or 0)
            lat, lng = round(float(field[4]), 3), round(float(field[5]), 3)
        except ValueError:
            continue
        if pop < POPULATION or not name or not country:
            continue
        # A city with no admin-1 code is usually a city-state — Singapore, Monaco, Hong Kong.
        # Its own name is the truest thing to call the level above it.
        state = states.get(f"{country}.{admin1}", "") if admin1 else ""
        rows.append([name, country, lat, lng, pop, state])
    rows.sort(key=lambda r: -r[4])
    return rows


def main() -> None:
    states = admin_names()
    print(f"admin-1 divisions: {len(states):,}")
    rows = cities(states)
    placed = sum(1 for r in rows if r[5])
    print(f"cities over {POPULATION:,}: {len(rows):,} — {placed:,} with a state "
          f"({placed / max(len(rows), 1):.0%})")
    OUT.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
