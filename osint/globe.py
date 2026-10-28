"""The 3D globe: country outlines, news volume, and the click that comes back to Python.

Streamlit cannot resolve the calling module when `declare_component` runs inside a page script
under pages/, so the declaration lives here and the page imports it.
"""
from __future__ import annotations

import json
from pathlib import Path

import streamlit.components.v1 as components

DIR = Path(__file__).resolve().parent.parent / "components" / "globe"
OUTLINES = DIR / "countries.geojson"      # also read by osint.geo, which must not import this

_component = components.declare_component("axn_globe", path=str(DIR))


def drawable_countries() -> set[str]:
    """The ISO-2 codes the outlines actually contain — 110m leaves out the smallest states."""
    if not OUTLINES.exists():
        return set()
    return {f["properties"]["iso2"] for f in json.loads(OUTLINES.read_text())["features"]}


def globe(rows: list[dict], selected: str | None = None, height: int = 560,
          markers: list[dict] | None = None, sun: dict | None = None,
          dim: bool = False, weather: list[dict] | None = None,
          view: str = "3d", surface: str = "map",
          layers: list[dict] | None = None, paths: list[dict] | None = None,
          key: str = "globe"):
    """Draw it, and return what was last clicked.

    The answer is an ISO-2 country code, "city:<name>|<cc>", "event:<kind>|<lat>|<lng>", or one
    of the map's own buttons: "view:2d", "surface:sat", "layers:<csv of layer keys>".

    `markers` are the dots laid over the countries and `paths` the lines drawn under them — each
    already carries the colour and size it should be drawn at. `weather` is the loop of
    transparent textures that goes over the surface, not instead of it. `layers` is the panel inside the
    map: every layer there is, whether it is on, and what it found. `sun` is where the sun is
    overhead, which the night side is cast from.
    """
    return _component(rows=rows, selected=selected, height=height,
                      markers=markers or [], sun=sun, dim=dim,
                      weather=weather or [], view=view, surface=surface,
                      layers=layers or [], paths=paths or [], key=key, default=None)
