"""Which country a point falls in, from the outlines the globe already ships.

An earthquake feed gives coordinates, not a country. Rather than add a geocoding service for
something the map already knows, the same Natural Earth outlines answer it here: a bounding
box to throw out the obvious misses, then ray casting against the ring.
"""
from __future__ import annotations

import json
from functools import lru_cache

from osint.globe import OUTLINES


@lru_cache(maxsize=1)
def _shapes() -> list[tuple]:
    """(iso2, min_lng, min_lat, max_lng, max_lat, rings) for every piece of every country."""
    if not OUTLINES.exists():
        return []
    out = []
    for feature in json.loads(OUTLINES.read_text())["features"]:
        iso2 = feature["properties"]["iso2"]
        geometry = feature["geometry"]
        parts = ([geometry["coordinates"]] if geometry["type"] == "Polygon"
                 else geometry["coordinates"])
        for rings in parts:
            lngs = [p[0] for p in rings[0]]
            lats = [p[1] for p in rings[0]]
            out.append((iso2, min(lngs), min(lats), max(lngs), max(lats), rings))
    return out


def _in_ring(ring: list, lng: float, lat: float) -> bool:
    inside = False
    j = len(ring) - 1
    for i, (x_i, y_i) in enumerate(ring):
        x_j, y_j = ring[j]
        if (y_i > lat) != (y_j > lat) and lng < (x_j - x_i) * (lat - y_i) / (y_j - y_i) + x_i:
            inside = not inside
        j = i
    return inside


def country_of(lat: float, lng: float) -> str | None:
    """The ISO-2 code of the country containing this point, or None over water."""
    for iso2, x0, y0, x1, y1, rings in _shapes():
        if not (x0 <= lng <= x1 and y0 <= lat <= y1):
            continue
        if _in_ring(rings[0], lng, lat) and not any(_in_ring(h, lng, lat) for h in rings[1:]):
            return iso2
    return None
