"""What the humanitarian system says a country needs, and how much of it has been paid for.

UN OCHA's Humanitarian API carries the two figures that between them say more about a crisis
than any news feed does: how many people a response plan counts as needing help, and what
share of the money that plan asked for has actually arrived. A country whose appeal is 20%
funded is a country where something is about to get worse, and that is visible here weeks
before it is visible anywhere else on this dashboard.

HAPI asks callers to identify themselves with a base64 of "app:email" rather than with a key,
so that it can get in touch if an app misbehaves. The default below is this project's own; a
deployment that wants its own contact address can set hapi_app_identifier in secrets.
"""
from __future__ import annotations

import base64

import requests
import streamlit as st

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; axn-pulse-news-monitor/1.0)"}
API = "https://hapi.humdata.org/api/v2"
TIMEOUT = 15
DEFAULT_CONTACT = "axn-pulse:mssaidurr@gmail.com"


def _identifier() -> str:
    try:
        contact = st.secrets.get("hapi_app_identifier", DEFAULT_CONTACT)
    except Exception:              # no secrets file at all, which is fine
        contact = DEFAULT_CONTACT
    return base64.b64encode(str(contact).encode()).decode()


def _ask(path: str, **params) -> list[dict]:
    try:
        reply = requests.get(f"{API}/{path}",
                             params={"output_format": "json", "limit": 200,
                                     "app_identifier": _identifier(), **params},
                             headers=HEADERS, timeout=TIMEOUT)
        reply.raise_for_status()
        return reply.json().get("data", []) or []
    except (requests.RequestException, ValueError):
        return []


@st.cache_data(ttl=86400, show_spinner=False)
def appeal(iso3: str) -> dict | None:
    """The most recent humanitarian appeal for a country: what it asked for and what it got."""
    if not iso3:
        return None
    rows = [row for row in _ask("coordination-context/funding", location_code=iso3)
            if row.get("requirements_usd")]
    if not rows:
        return None
    newest = max(rows, key=lambda row: str(row.get("reference_period_start") or ""))
    return {
        "name": newest.get("appeal_name", ""),
        "needs": float(newest["requirements_usd"]),
        "funded": float(newest.get("funding_usd") or 0),
        "share": float(newest.get("funding_pct") or 0),
        "year": str(newest.get("reference_period_start") or "")[:4],
    }


@st.cache_data(ttl=86400, show_spinner=False)
def in_need(iso3: str) -> dict | None:
    """How many people the current response plan counts as needing help, by sector.

    HAPI reports several population statuses against the same sector — in need, targeted,
    reached. Only "in need" is taken here, and the whole-country rows only, so sector figures
    are not double-counted with the provinces inside them.
    """
    if not iso3:
        return None
    rows = [row for row in _ask("affected-people/humanitarian-needs", location_code=iso3,
                                admin_level=0)
            if row.get("population") and row.get("population_status") == "INN"]
    if not rows:
        return None
    year = max(str(row.get("reference_period_start") or "")[:4] for row in rows)
    latest = [row for row in rows if str(row.get("reference_period_start") or "").startswith(year)]
    by_sector: dict[str, float] = {}
    for row in latest:
        sector = row.get("sector_name") or row.get("sector_code") or "All sectors"
        by_sector[sector] = max(by_sector.get(sector, 0), float(row["population"]))
    return {"year": year, "sectors": by_sector, "most": max(by_sector.values(), default=0)}
