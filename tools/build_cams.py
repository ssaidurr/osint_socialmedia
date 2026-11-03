#!/usr/bin/env python3
"""Fetch the open camera catalogues into the repo, because reading them costs the page too much.

Caltrans publishes its Los Angeles cameras as one 1.8 MB file served at about fifty kilobytes a
second: thirty-eight seconds on a good run, measured once at a hundred and forty-eight. The
panel needs six cameras out of six hundred, and the list of which cameras exist changes about as
often as a road does. So it rides in the repo the way the map layers do, and the page reads a
file instead of waiting on a road authority.

What does NOT ride in the repo is the picture. Each row keeps the camera's own address, which
the panel asks the browser to load directly, with a five-minute cache-buster on it — so the
catalogue is a month old and the image is five minutes old, which is the right way round.

Singapore is deliberately absent: its feed is small, it answers in under half a second, and
every capture there gets its own address, so a shipped row would freeze the picture.

    python tools/build_cams.py

It lands in osint/data/cams.json as {built, regions: {name: [{title, city, …, thumb}, …]}}.
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from osint import http                                      # noqa: E402  (after the path fix)

DATA = Path(__file__).resolve().parent.parent / "osint" / "data"
JAMCAMS = "https://api.tfl.gov.uk/Place/Type/JamCam"
CALTRANS = "https://cwwp2.dot.ca.gov/data/d7/cctv/cctvStatusD07.json"
BUDGET = 180        # a runner has the time the page does not
KEEP = 60           # the panel shows six; the rest are there for a region filter to narrow


def london() -> list[dict]:
    """Transport for London's traffic cameras."""
    rows = http.json(JAMCAMS, BUDGET) or []
    out = []
    for row in rows:
        extra = {p.get("key"): p.get("value") for p in (row.get("additionalProperties") or [])}
        if str(extra.get("available")).lower() != "true" or not extra.get("imageUrl"):
            continue
        out.append({"title": row.get("commonName") or "Camera", "city": "London",
                    "country": "United Kingdom", "cc": "GB",
                    "lat": row.get("lat"), "lng": row.get("lon"),
                    "thumb": extra["imageUrl"],
                    "page": extra.get("videoUrl") or row.get("url") or ""})
    return out


def california() -> list[dict]:
    """Caltrans' district 7, which is Los Angeles."""
    out = []
    for row in (http.json(CALTRANS, BUDGET) or {}).get("data", []):
        cctv = row.get("cctv") or {}
        where, image = cctv.get("location") or {}, cctv.get("imageData") or {}
        url = ((image.get("static") or {}).get("currentImageURL") or "").strip()
        if str(cctv.get("inService")).lower() != "true" or not url:
            continue
        out.append({"title": where.get("locationName") or "Camera", "city": "Los Angeles",
                    "country": "United States", "cc": "US",
                    "lat": where.get("latitude"), "lng": where.get("longitude"),
                    "thumb": url, "page": url})
    return out


def spread(rows: list[dict], keep: int) -> list[dict]:
    """Take them from across the list rather than off the front, which on a road authority's
    feed is the same junction photographed eight times."""
    step = max(1, len(rows) // keep) if rows else 1
    return rows[::step][:keep]


def main() -> int:
    regions, empty = {}, []
    for name, fetch in (("London", london), ("California", california)):
        rows = spread(fetch(), KEEP)
        print(f"  {name:12} {len(rows):>3} cameras")
        if rows:
            regions[name] = rows
        else:
            empty.append(name)

    if not regions:
        # Keeping yesterday's cameras beats shipping none of them
        print("\nNeither authority answered — the old file was left alone", file=sys.stderr)
        return 1

    if empty:
        # One authority being down must not drop the other's cameras from the file
        old = json.loads((DATA / "cams.json").read_text()) if (DATA / "cams.json").exists() else {}
        for name in empty:
            kept = (old.get("regions") or {}).get(name)
            if kept:
                regions[name] = kept
                print(f"  {name:12} kept {len(kept)} from the previous build")

    path = DATA / "cams.json"
    path.write_text(json.dumps({
        "built": datetime.date.today().isoformat(),
        "source": "Transport for London; California Department of Transportation",
        "regions": regions,
    }, separators=(",", ":")))
    print(f"\n{sum(len(r) for r in regions.values())} cameras  "
          f"{path.stat().st_size / 1024:,.0f} KB  -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
