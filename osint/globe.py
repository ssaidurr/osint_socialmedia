"""The 3D globe: country outlines, news volume, and the click that comes back to Python.

Streamlit cannot resolve the calling module when `declare_component` runs inside a page script
under pages/, so the declaration lives here and the page imports it.
"""
from __future__ import annotations

import json
from pathlib import Path

import streamlit.components.v1 as components

DIR = Path(__file__).resolve().parent.parent / "components" / "globe"
OUTLINES = DIR / "countries.geojson"

_component = components.declare_component("axn_globe", path=str(DIR))


def drawable_countries() -> set[str]:
    """The ISO-2 codes the outlines actually contain — 110m leaves out the smallest states."""
    if not OUTLINES.exists():
        return set()
    return {f["properties"]["iso2"] for f in json.loads(OUTLINES.read_text())["features"]}


def globe(rows: list[dict], selected: str | None = None, height: int = 560, key: str = "globe"):
    """Draw it, and return the ISO-2 code of the country last clicked (None until one is)."""
    return _component(rows=rows, selected=selected, height=height, key=key, default=None)
