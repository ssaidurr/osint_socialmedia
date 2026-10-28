"""Pin a story to a city.

The analyzer gives each item a country. This turns that into a point by looking for a city
name in the headline and summary — a local gazetteer, no geocoding service, no rate limit.

City list: GeoNames (CC-BY 4.0), every city of 50,000 people or more, built once into
osint/cities.json. Matching is deliberately conservative: a name has to appear capitalised,
has to be long enough to mean something, and when the item already has a country the city
must be in it. What is left unmatched stays at country level rather than being guessed at.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

CITIES = Path(__file__).resolve().parent / "cities.json"

# Names that are ordinary English words, or so ambiguous that a headline containing them is
# usually about something else entirely. Each one is a real city; none is worth the false hits.
AMBIGUOUS = {
    "nice", "split", "mobile", "reading", "bath", "major", "general", "union", "march",
    "victoria", "san", "santa", "san jose", "colon", "most", "best", "little", "white",
    "centre", "center", "national", "university", "park", "hospital", "university park",
    "of", "as", "la", "los", "new", "north", "south", "east", "west", "central", "capital",
    "york", "boston", "athens", "cambridge", "manchester", "richmond", "columbia", "canada",
    "same", "sale", "born", "gold", "hope", "liberty", "independence", "fair", "concord",
    "palm", "orange", "mission", "union city", "nation", "federal", "republic", "state",
    # Cities that are also people's first names. "Vladimir" matched eleven stories about
    # Putin before this list existed.
    "vladimir", "francisco", "antonio", "diego", "jose", "juan", "carlos", "maria",
    "hamilton", "tyler", "carson", "marion", "kelly", "nelson", "stanley", "gordon",
    "jordan", "florence", "valentin", "miranda", "angel", "cristobal", "salvador",
}

# With no country to anchor it, only a city big enough to be the subject of world news is
# accepted — otherwise a passing word lands the story on the wrong continent.
MIN_POP_WITHOUT_COUNTRY = 500_000


@lru_cache(maxsize=1)
def _index() -> dict[str, list[tuple]]:
    """lowercase city name -> [(name, country, lat, lng, population), …], biggest first."""
    index: dict[str, list[tuple]] = {}
    for name, country, lat, lng, pop in json.loads(CITIES.read_text()):
        key = name.lower()
        if len(key) < 4 or key in AMBIGUOUS:
            continue
        index.setdefault(key, []).append((name, country, lat, lng, pop))
    for rows in index.values():
        rows.sort(key=lambda r: -r[4])
    return index


# Capitalised runs of up to three words: "Rafah", "Cox's Bazar", "Port au Prince".
CANDIDATE = re.compile(r"\b[A-Z][\w'’-]+(?:[ -][A-Z][\w'’-]+){0,2}")


def find(text: str, country: str | None = None) -> tuple | None:
    """The city a piece of text is about, as (name, country, lat, lng), or None.

    A city in the item's own country always wins; among the rest, the biggest does. Only the
    first few candidates are considered, because a headline names its subject early.
    """
    if not text:
        return None
    index = _index()
    best = None
    for phrase in CANDIDATE.findall(text)[:12]:
        words = phrase.split()
        # longest first: "New York" should not be found as "York"
        for size in range(min(3, len(words)), 0, -1):
            for start in range(len(words) - size + 1):
                rows = index.get(" ".join(words[start:start + size]).lower())
                if not rows:
                    continue
                if country:
                    local = [r for r in rows if r[1] == country]
                    if local:
                        return local[0][0], local[0][1], local[0][2], local[0][3]
                    continue          # named a city, but not one in the country we were given
                if rows[0][4] < MIN_POP_WITHOUT_COUNTRY:
                    continue
                if best is None or rows[0][4] > best[4]:
                    best = rows[0]
    return (best[0], best[1], best[2], best[3]) if best else None


def _text(value) -> str:
    """A dataframe cell as text. `value or ""` is no good here: a NaN is a truthy float."""
    if value is None:
        return ""
    if isinstance(value, float):
        return "" if value != value else str(value)       # NaN is the one value unequal to itself
    return str(value)


def locate(records: tuple) -> list[dict]:
    """Place as many of `records` on the map as can be placed.

    `records` is a tuple of (id, title, summary, country, sentiment, severity, source, url, ts,
    category) — a tuple so Streamlit can cache the result.
    """
    out = []
    for (item_id, title, summary, country, sentiment, severity, source, url, ts,
         category) in records:
        hit = find(_text(title), _text(country) or None) or find(_text(summary), _text(country) or None)
        if not hit:
            continue
        name, city_country, lat, lng = hit
        out.append({"lat": lat, "lng": lng, "city": name, "country": city_country,
                    "title": _text(title) or _text(summary), "sentiment": sentiment,
                    "severity": None if severity is None else float(severity),
                    "category": _text(category),
                    "source": _text(source), "url": _text(url), "ts": _text(ts)[:16].replace("T", " ")})
    return out


@lru_cache(maxsize=256)
def biggest_city(country: str) -> tuple | None:
    """The largest city we hold for a country, as (name, lat, lng) — a stand-in for the place
    itself when something has to be asked about a single point."""
    best = None
    for rows in _index().values():
        for name, cc, lat, lng, pop in rows:
            if cc == country and (best is None or pop > best[4]):
                best = (name, cc, lat, lng, pop)
    return (best[0], best[2], best[3]) if best else None


@lru_cache(maxsize=1)
def currencies() -> dict:
    """Country -> (code, name, symbol), generated once from CLDR."""
    path = CITIES.parent / "currencies.json"
    return json.loads(path.read_text()) if path.exists() else {}
