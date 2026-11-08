"""Which stories belong to each of the five buttons over the map.

The buttons first only changed the map's layers, while the panel beside it went on answering
for every story. Now the panel follows them as well: Security shows the security news,
worldwide or for the country that is picked.

A story's category alone cannot decide this. The analyzer gives a category to negative items
only, so a neutral report on a missile test or a new port would never be found. Each theme is
therefore its categories *or* its words, matched in the headline and the English summary.
Bangla words match as substrings, because Bangla inflects by suffix. Latin words match on word
boundaries, and a trailing * allows any ending.

"News" has no entry here on purpose: it is the general view, so it keeps every story.
"""
from __future__ import annotations

import re

import pandas as pd

THEMES: dict[str, dict] = {
    "event": {
        "categories": {"accident", "disaster", "health"},
        "words": [
            "earthquake*", "quake*", "tsunami*", "volcan*", "erupt*", "flood*", "landslide*",
            "cyclone*", "hurricane*", "typhoon*", "tornado*", "wildfire*", "bushfire*", "blaze*",
            "fire", "fires", "explosion*", "blast", "blasts", "crash*", "derail*", "capsiz*",
            "collapse*", "outbreak*", "epidemic*", "pandemic*", "cholera", "dengue", "measles",
            "mpox", "ebola", "bird flu", "outage*", "blackout*", "drought*", "heatwave*",
            "heat wave*", "evacuat*", "death toll", "stampede*", "sinkhole*",
            "ভূমিকম্প", "বন্যা", "ঘূর্ণিঝড়", "ভূমিধস", "অগ্নিকাণ্ড", "আগুন", "বিস্ফোরণ", "দুর্ঘটনা",
            "ডেঙ্গু", "প্রাদুর্ভাব", "কলেরা", "খরা", "লঞ্চডুবি",
        ],
    },
    "security": {
        "categories": {"security"},
        "words": [
            "military", "army", "armies", "armed forces", "troop*", "soldier*", "navy", "naval",
            "air force", "missile*", "drone*", "airstrike*", "air strike*", "bomb*", "shelling",
            "artillery", "war", "wars", "warfare", "ceasefire*", "invasion", "invad*",
            "insurgen*", "militant*", "terror*", "jihad*", "hostage*", "nuclear", "uranium",
            "warship*", "defence", "defense", "pentagon", "nato", "weapon*", "arms deal*",
            "gunfire", "gunmen", "attack*", "cyberattack*", "hacker*", "espionage", "spy",
            "spies",
            "সেনা", "সামরিক", "ক্ষেপণাস্ত্র", "হামলা", "জঙ্গি", "সন্ত্রাস", "যুদ্ধ", "সীমান্ত",
            "অস্ত্র", "গোলাগুলি", "বোমা", "পারমাণবিক", "ড্রোন",
        ],
    },
    "weather": {
        # No category: the analyzer's "disaster" covers earthquakes too, which are not weather
        "categories": set(),
        "words": [
            "weather", "forecast*", "rain", "rains", "rainfall", "rainy", "monsoon*", "storm",
            "storms", "cyclone*", "hurricane*", "typhoon*", "tornado*", "flood*", "heatwave*",
            "heat wave*", "extreme heat", "heatstroke", "heat stroke", "scorching", "snow*",
            "blizzard*", "cold wave*", "cold snap", "frost", "fog", "temperature*", "drought*",
            "winds", "gale*", "climate", "el nino", "la nina", "hail", "hailstorm*", "humid*",
            "air quality", "smog", "met office", "meteorolog*",
            "আবহাওয়া", "বৃষ্টি", "বর্ষা", "ঘূর্ণিঝড়", "বন্যা", "তাপপ্রবাহ", "শৈত্যপ্রবাহ", "কুয়াশা",
            "ঝড়", "তাপমাত্রা", "জলাবদ্ধতা", "বজ্রপাত", "নিম্নচাপ",
        ],
    },
    "infrastructure": {
        "categories": set(),
        # Words like "bank", "mine" and "dam" are left out: West Bank, the pronoun, and the
        # Banglish "dam" (price) would all match them.
        "words": [
            "infrastructure", "port", "ports", "seaport*", "shipping", "container ship*",
            "refiner*", "oil", "natural gas", "lng", "pipeline*", "power grid", "power plant*",
            "power station*", "electricity", "blackout*", "outage*", "energy", "nuclear plant*",
            "nuclear power", "reactor*", "renewable*", "solar", "wind farm*", "wind power",
            "hydropower", "mines", "mining", "miner", "miners", "data cent*", "semiconductor*",
            "stock exchange*", "stock market*", "central bank*", "banking", "bridge*",
            "railway*", "rail", "metro rail", "highway*", "expressway*", "airport*", "internet",
            "broadband", "telecom*", "undersea cable*", "subsea cable*", "satellite*",
            "spaceport*", "construction", "water supply", "canal", "suez", "hormuz",
            "chokepoint*",
            "বিদ্যুৎ", "গ্যাস", "জ্বালানি", "বন্দর", "সেতু", "রেল", "মেট্রো", "মহাসড়ক", "বিমানবন্দর",
            "ইন্টারনেট", "তেল", "খনি",
        ],
    },
}

BANGLA = re.compile(r"[ঀ-৿]")


def _pattern(words: list[str]) -> re.Pattern:
    latin, bangla = [], []
    for word in words:
        if BANGLA.search(word):
            bangla.append(re.escape(word))
        elif word.endswith("*"):
            latin.append(re.escape(word[:-1]) + r"\w*")
        else:
            latin.append(re.escape(word) + r"\b")
    parts = []
    if latin:
        parts.append(r"\b(?:" + "|".join(latin) + ")")
    parts += bangla
    return re.compile("|".join(parts), re.IGNORECASE)


PATTERNS = {slug: _pattern(theme["words"]) for slug, theme in THEMES.items()}


def select(frame: pd.DataFrame, slug: str | None) -> pd.DataFrame:
    """The rows of `frame` that belong to this theme. No theme, or "news", keeps every row."""
    if not slug or slug not in THEMES or frame.empty:
        return frame
    text = frame["title"].fillna("") + " " + frame["summary"].fillna("")
    by_words = text.str.contains(PATTERNS[slug], regex=True)
    by_category = frame["category"].isin(THEMES[slug]["categories"])
    return frame[by_words | by_category]
