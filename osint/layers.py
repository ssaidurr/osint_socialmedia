"""The layers that do not change by the hour.

Cables, military bases, mines, ports, power stations and the rest are facts about the world
rather than feeds, so they are built into osint/data by tools/build_layers.py and read from
here — no key, no rate limit, and the same picture whether or not anyone's server is up.

Every file carries the date it was built, its source and its attribution alongside the items,
so the dashboard can say how old a layer is instead of implying it is live. Rebuild them with:

    python tools/build_layers.py            # all of them
    python tools/build_layers.py bases      # or just one

places.json is the exception: a short hand-written list of spaceports and chokepoints, because
there is no open registry of either worth taking a dependency on.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"


@lru_cache(maxsize=None)
def _read(name: str) -> dict:
    """{built, source, attribution, items} — or an empty one if the file was never built."""
    path = DATA / f"{name}.json"
    if not path.exists():
        return {"built": "", "source": "", "attribution": "", "items": []}
    return json.loads(path.read_text())


def items(name: str) -> list[dict]:
    """The rows of a built layer: each has name, lat, lng, cc, and whatever else it carries."""
    return _read(name).get("items", [])


def built(name: str) -> str:
    """The date the layer was last rebuilt, as YYYY-MM-DD, for the caption under the map."""
    return _read(name).get("built", "")


def credit(name: str) -> str:
    return _read(name).get("attribution", "")


def cables() -> list[dict]:
    """[{name, lines: [[[lng, lat], …], …]}] — a cable is often several strands."""
    return items("cables")


def military_bases() -> list[dict]:
    """Bases and air bases together: on a world map the distinction is not worth two layers."""
    return items("bases") + items("airbases")


@lru_cache(maxsize=1)
def _places() -> dict:
    path = DATA / "places.json"
    return json.loads(path.read_text()) if path.exists() else {}


def spaceports() -> list[dict]:
    """[{name, cc, lat, lng}] — the sites that actually launch."""
    return _places().get("spaceports", [])


def chokepoints() -> list[dict]:
    """[{name, lat, lng, detail}] — the straits and canals trade cannot route around."""
    return _places().get("chokepoints", [])
