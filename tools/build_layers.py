#!/usr/bin/env python3
"""Rebuild the map layers that are facts about the world rather than feeds.

These used to be written by hand, which meant nobody could tell how old they were or redo them
when they drifted. Now every one of them comes from a named source and this script fetches it:

  wikidata   a SPARQL query against query.wikidata.org — maintained by people, free, and the
             only practical global source for things like stock exchanges or military bases
             (Overpass times out on a planet-wide query and OSM extracts are far too large)
  file       a published dataset, downloaded and parsed

Run it with no arguments to rebuild everything, or name the layers to do fewer:

    python tools/build_layers.py                 # all of them
    python tools/build_layers.py bases mines     # just these two
    python tools/build_layers.py --list          # what there is to build

Each layer lands in osint/data/<key>.json as {built, source, attribution, items}. The `built`
date is what the dashboard shows when it says how old a layer is, so it is not decoration.
"""
from __future__ import annotations

import csv
import datetime
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from osint import geo                                       # noqa: E402  (after the path fix)

DATA = Path(__file__).resolve().parent.parent / "osint" / "data"
AGENT = "axn-pulse-news-monitor/1.0 (https://synergyaxn.com)"
SPARQL = "https://query.wikidata.org/sparql"

# Wikidata will happily return forty thousand rows of something; the globe will not happily draw
# them. Where a class is huge the query sorts by how many Wikipedias carry the article, which is
# a decent stand-in for "worth putting on a world map", and takes the top of that list.
BY_NOTABILITY = """
  ?x wdt:P625 ?coord; rdfs:label ?name.
  OPTIONAL { ?x wdt:P17/wdt:P297 ?cc }
  OPTIONAL { ?x wikibase:sitelinks ?rank }
  FILTER(lang(?name) = "en")
"""


def sparql(where: str, limit: int | None = None, order: bool = False) -> list[dict]:
    """Ask Wikidata, and hand back rows of {name, lat, lng, cc, rank}."""
    query = ("SELECT ?name ?coord ?cc ?rank WHERE {" + where + BY_NOTABILITY + "}"
             + (" ORDER BY DESC(?rank)" if order else "")
             + (f" LIMIT {limit}" if limit else ""))
    r = requests.post(SPARQL, data={"query": query}, timeout=180,
                      headers={"User-Agent": AGENT, "Accept": "application/sparql-results+json"})
    r.raise_for_status()
    out, seen = [], set()
    for row in r.json()["results"]["bindings"]:
        point = re.match(r"Point\(([-0-9.]+) ([-0-9.]+)\)", row["coord"]["value"])
        if not point:
            continue
        name = row["name"]["value"]
        # One item can come back several times — one row per country, per label, per anything
        # else that was left optional. The first row for a name is the one that is kept.
        if name in seen:
            continue
        seen.add(name)
        out.append({"name": name,
                    "lat": round(float(point.group(2)), 4),
                    "lng": round(float(point.group(1)), 4),
                    "cc": row.get("cc", {}).get("value", ""),
                    "rank": int(row.get("rank", {}).get("value", 0))})
    return out


def download(url: str) -> bytes:
    r = requests.get(url, timeout=300, headers={"User-Agent": AGENT})
    r.raise_for_status()
    return r.content


# ─── The layers ───────────────────────────────────────────────────────────────
# Each builder returns (items, source, attribution). Keep them small: a builder that needs more
# than a screen is usually a sign the source is wrong for the job.

def build_bases():
    # Exactly "military base", not everything beneath it: the subclass tree reaches down into
    # civilian airports, and a map of military bases that opens on Dubai International is wrong.
    rows = sparql("?x wdt:P31 wd:Q245016.", limit=1800, order=True)
    return rows, "Wikidata (military base)", "Wikidata contributors, CC0"


def build_airbases():
    rows = sparql("?x wdt:P31 wd:Q695850.", limit=900, order=True)
    return rows, "Wikidata (air base)", "Wikidata contributors, CC0"


def build_datacenters():
    rows = sparql("?x wdt:P31/wdt:P279* wd:Q671224.", limit=900, order=True)
    return rows, "Wikidata (data centre)", "Wikidata contributors, CC0"


def build_exchanges():
    rows = sparql("?x wdt:P31/wdt:P279* wd:Q11691.")
    return rows, "Wikidata (stock exchange)", "Wikidata contributors, CC0"


def build_central_banks():
    rows = sparql("?x wdt:P31 wd:Q66344.")
    return rows, "Wikidata (central bank)", "Wikidata contributors, CC0"


def build_ports():
    rows = sparql("?x wdt:P31/wdt:P279* wd:Q44782.", limit=1200, order=True)
    return rows, "Wikidata (port)", "Wikidata contributors, CC0"


def build_mines():
    # Ranking mines by how famous they are returns museums: Wieliczka, Zollverein, Hashima.
    # Requiring a recorded product (P1056) asks instead for the ones that dig something up.
    rows = sparql("?x wdt:P31/wdt:P279* wd:Q820477; wdt:P1056 ?product."
                  " FILTER NOT EXISTS { ?x wdt:P576 ?closed }", limit=1200, order=True)
    return rows, "Wikidata (mine, with a recorded product)", "Wikidata contributors, CC0"


def build_refineries():
    rows = sparql("?x wdt:P31/wdt:P279* wd:Q12353044.", limit=600, order=True)
    return rows, "Wikidata (oil refinery)", "Wikidata contributors, CC0"


def build_cables():
    """Submarine cables, as lines rather than points — the one layer that is drawn, not dotted."""
    raw = download("https://www.submarinecablemap.com/api/v3/cable/cable-geo.json")
    rows = []
    for feature in json.loads(raw)["features"]:
        geometry, props = feature["geometry"], feature["properties"]
        lines = (geometry["coordinates"] if geometry["type"] == "MultiLineString"
                 else [geometry["coordinates"]])
        rows.append({"name": props.get("name", ""), "cc": "", "rank": 0,
                     "lines": [[[round(x, 3), round(y, 3)] for x, y in line] for line in lines]})
    return rows, "TeleGeography Submarine Cable Map", "TeleGeography, CC-BY-SA 4.0"


def _power_plants():
    """The WRI database, read once and handed to whichever layer wants rows out of it."""
    raw = download("https://wri-dataportal-prod.s3.amazonaws.com/manual/"
                   "global_power_plant_database_v_1_3.zip")
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        name = next(n for n in bundle.namelist() if n.endswith(".csv") and "global_power" in n)
        with bundle.open(name) as fh:
            return list(csv.DictReader(io.TextIOWrapper(fh, "utf-8")))


def _plants_of(*fuels: str):
    wanted = {f.lower() for f in fuels}
    rows = []
    for p in _power_plants():
        if (p.get("primary_fuel") or "").lower() not in wanted:
            continue
        try:
            lat, lng = float(p["latitude"]), float(p["longitude"])
        except (TypeError, ValueError):
            continue
        rows.append({"name": p["name"], "lat": round(lat, 4), "lng": round(lng, 4),
                     "cc": p.get("country_long", ""), "rank": 0,
                     "mw": round(float(p.get("capacity_mw") or 0)),
                     "fuel": p.get("primary_fuel", "")})
    rows.sort(key=lambda r: -r["mw"])
    return rows


def build_nuclear():
    rows = _plants_of("Nuclear")
    return rows, "WRI Global Power Plant Database v1.3", "World Resources Institute, CC-BY 4.0"


def build_renewables():
    # The biggest thousand: the database has thirty thousand solar roofs, and a map of those is
    # a map of nothing.
    rows = _plants_of("Solar", "Wind", "Hydro", "Geothermal")[:1200]
    return rows, "WRI Global Power Plant Database v1.3", "World Resources Institute, CC-BY 4.0"


def build_happiness():
    text = download("https://ourworldindata.org/grapher/"
                    "happiness-cantril-ladder.csv").decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    # Entity, Code, Year, then the one value column, whose name OWID changes from time to time
    column = reader.fieldnames[3]
    newest: dict[str, dict] = {}
    for row in reader:
        code, year, score = row.get("Code") or "", row.get("Year") or "0", row.get(column)
        if len(code) != 3 or not score:
            continue
        if code not in newest or int(year) > int(newest[code]["year"]):
            newest[code] = {"name": row["Entity"], "iso3": code, "year": int(year),
                            "score": round(float(score), 3)}
    return list(newest.values()), "World Happiness Report via Our World in Data", "OWID, CC-BY"


BUILDERS = {
    "cables": build_cables,
    "bases": build_bases,
    "airbases": build_airbases,
    "datacenters": build_datacenters,
    "exchanges": build_exchanges,
    "central_banks": build_central_banks,
    "ports": build_ports,
    "mines": build_mines,
    "refineries": build_refineries,
    "nuclear": build_nuclear,
    "renewables": build_renewables,
    "happiness": build_happiness,
}


def stamp_countries(items: list[dict]) -> None:
    """Give every row an ISO-2 country, working it out from the outlines where the source gave
    a name or a three-letter code instead. It is done here, once, rather than on every page
    load: which country a mine is in does not change between reruns."""
    for row in items:
        if len(row.get("cc", "")) == 2 or "lat" not in row:
            continue
        row["cc"] = geo.country_of(row["lat"], row["lng"]) or ""


def main(argv: list[str]) -> int:
    if "--list" in argv:
        print("\n".join(sorted(BUILDERS)))
        return 0
    wanted = [a for a in argv if not a.startswith("-")] or sorted(BUILDERS)
    unknown = [w for w in wanted if w not in BUILDERS]
    if unknown:
        print(f"no such layer: {', '.join(unknown)}", file=sys.stderr)
        return 2

    DATA.mkdir(parents=True, exist_ok=True)
    failed = []
    for key in wanted:
        print(f"{key:16} … ", end="", flush=True)
        try:
            items, source, attribution = BUILDERS[key]()
        except Exception as e:                       # one bad source must not lose the others
            print(f"failed: {type(e).__name__}: {e}")
            failed.append(key)
            continue
        if not items:
            print("failed: the source answered with nothing")
            failed.append(key)
            continue
        stamp_countries(items)
        path = DATA / f"{key}.json"
        path.write_text(json.dumps({
            "built": datetime.date.today().isoformat(),
            "source": source, "attribution": attribution, "items": items,
        }, separators=(",", ":")))
        print(f"{len(items):>6,} items  {path.stat().st_size / 1024:>7,.0f} KB")
    if failed:
        print(f"\n{len(failed)} failed: {', '.join(failed)} — the old files were left alone",
              file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
