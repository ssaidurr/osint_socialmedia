"""Credibility signals for negative items.

This is deliberately NOT a "% true" score — nothing can compute that for fresh news. It combines
three things that CAN be measured:

  1. published fact-checks matching the claim (Google Fact Check Tools API — Rumor Scanner, BOOM, AFP, ...)
  2. corroboration: how many independent sources carry the same story
  3. source quality: established outlet vs. an unknown site vs. a user comment

A high score means "reported by sources that usually get it right, and others report it too" —
not "verified true". A low score means "treat with suspicion", not "this is fake".
"""
from __future__ import annotations

import logging
import os
import re
import time
from collections import defaultdict

import requests

from . import db

log = logging.getLogger(__name__)

FACT_CHECK_URL = "https://factchecktools.googleapis.com/v1alpha1/claims:search"

# Publishers word their verdicts differently; match loosely, lowercase
FALSE_MARKERS = ("false", "fake", "misleading", "incorrect", "hoax", "no evidence", "unproven", "distorted",
                 "altered", "miscaptioned", "satire", "pants on fire", "scam", "rumor", "rumour",
                 "মিথ্যা", "ভুয়া", "গুজব", "বিভ্রান্তিকর", "বানোয়াট")
TRUE_MARKERS = ("true", "correct", "accurate", "verified", "সত্য", "সঠিক")

STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "has", "have", "are", "was", "were", "will",
    "after", "over", "into", "amid", "says", "said", "new", "two", "three", "more", "than", "its",
    "bangladesh", "bangladeshi", "dhaka", "news", "video", "live", "update", "updates",
    "এবং", "এই", "তার", "করে", "হয়", "হবে", "থেকে", "জন্য", "সঙ্গে", "নিয়ে", "করা", "হয়েছে", "কর্মসূচি",
    "বাংলাদেশ", "বাংলাদেশে", "বাংলাদেশের", "ঢাকা", "ঢাকায়", "খবর", "সংবাদ",
}

WORD_RE = re.compile(r"[\wঀ-৿]+", re.UNICODE)
BANGLA_RE = re.compile(r"[ঀ-৿]")

# Base score by what kind of source the item came from
UGC_TYPES = {"reddit_comment", "reddit_post", "youtube_comment"}
BASE_TRUSTED, BASE_UNKNOWN_NEWS, BASE_VIDEO, BASE_UGC = 65, 45, 30, 20


def _tokens(text: str) -> set[str]:
    return {w for w in WORD_RE.findall((text or "").lower()) if len(w) > 2 and w not in STOPWORDS}


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _grams(text: str, n: int = 4) -> set[str]:
    """Character n-grams. Word matching fails on Bangla (ইউনূস vs ইউনূসের are different tokens)
    and across scripts, where fact-check databases often store the claim in English and the
    article title in Bangla; character n-grams survive both."""
    s = " ".join((text or "").lower().split())
    return {s[i:i + n] for i in range(max(0, len(s) - n + 1))}


def _containment(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _item_text(item: dict) -> str:
    return " ".join(p for p in (item.get("title"), item.get("text")) if p)[:400]


def corroborating_sources(item: dict, index: dict[str, list[int]], pool: list[dict],
                          pool_tokens: list[set[str]], min_overlap: float = 0.5) -> set[str]:
    """Distinct other sources whose headline covers the same story (token overlap)."""
    tokens = _tokens(_item_text(item))
    candidates: dict[int, int] = defaultdict(int)
    for token in tokens:
        for idx in index.get(token, ()):
            candidates[idx] += 1
    sources = set()
    for idx, shared in candidates.items():
        other = pool[idx]
        if other["id"] == item["id"] or shared < 2 or other["source"] == item["source"]:
            continue
        if _overlap(tokens, pool_tokens[idx]) >= min_overlap:
            sources.add(other["source"])
    return sources


def build_index(pool: list[dict]) -> tuple[dict[str, list[int]], list[set[str]]]:
    index: dict[str, list[int]] = defaultdict(list)
    pool_tokens = []
    for i, it in enumerate(pool):
        tokens = _tokens(_item_text(it))
        pool_tokens.append(tokens)
        for token in tokens:
            index[token].append(i)
    return index, pool_tokens


class FactChecker:
    """Google Fact Check Tools API. Returns a published fact-check only when it really matches the item."""

    def __init__(self, api_key: str, max_age_days: int, min_similarity: float = 0.30):
        self.api_key = api_key
        self.max_age_days = max_age_days
        self.min_similarity = min_similarity
        self.available = True
        self._strikes = 0  # a freshly enabled API answers 403 from some frontends for a few minutes

    def lookup(self, item: dict) -> dict | None:
        if not self.available:
            return None
        query = (item.get("title") or item.get("text") or "")[:200].strip()
        if not query:
            return None
        params = {"query": query, "pageSize": 5,
                  "languageCode": "bn" if BANGLA_RE.search(query) else "en"}
        if self.max_age_days:  # 0 = no limit, which finds more: rumours resurface years later
            params["maxAgeDays"] = self.max_age_days
        for attempt in (1, 2):
            try:
                r = requests.get(FACT_CHECK_URL, headers={"X-Goog-Api-Key": self.api_key},
                                 params=params, timeout=20)
            except requests.RequestException as e:
                log.warning("Fact Check API unreachable (%s)", e)
                return None
            if r.status_code == 429:
                log.warning("Fact Check API rate limited; skipping this item")
                return None
            if r.status_code == 403:
                # Not enabled for this project, key restricted to other APIs — or the API was just
                # enabled and some frontends still say no. Only give up after two 403s in a row.
                message = r.json().get("error", {}).get("message", "")[:160]
                if attempt == 1:
                    time.sleep(3)
                    continue
                self._strikes += 1
                log.warning("Fact Check API refused (403): %s", message)
                if self._strikes >= 2:
                    log.error("Fact Check lookups disabled for this run after repeated 403s")
                    self.available = False
                return None
            if r.status_code >= 500 and attempt == 1:
                time.sleep(2)  # the API returns the odd 503; one retry clears it
                continue
            if r.status_code != 200:
                log.warning("Fact Check API error %s", r.status_code)
                return None
            break
        self._strikes = 0

        # Guard against unrelated claims: the API matches loosely, and mislabelling a real story
        # as "False" is worse than returning nothing. Threshold calibrated on real claim pairs:
        # same story scored >= 0.39, different stories <= 0.21.
        item_grams = _grams(_item_text(item))
        best, best_score = None, 0.0
        for claim in r.json().get("claims", []):
            review = (claim.get("claimReview") or [{}])[0]
            if not review.get("textualRating"):
                continue
            # The claim text and the fact-check headline can be in different languages; try both
            score = _containment(item_grams, _grams(f"{claim.get('text', '')} {review.get('title', '')}"))
            if score > best_score:
                best, best_score = (claim, review), score
        if not best or best_score < self.min_similarity:
            return None
        claim, review = best
        log.info("Fact-check matched (similarity %.2f): %s", best_score, review.get("title", "")[:80])
        return {"rating": review["textualRating"],
                "publisher": (review.get("publisher") or {}).get("name"),
                "url": review.get("url"),
                "claim": claim.get("text")}


def source_base(item: dict, trusted: list[str]) -> tuple[int, str]:
    source = (item.get("source") or "").lower()
    if item["source_type"] in UGC_TYPES:
        kind = "Reddit" if item["source_type"].startswith("reddit") else "YouTube"
        return BASE_UGC, f"{kind} {item['source_type'].split('_')[1]}"
    if item["source_type"] == "youtube_video":
        return BASE_VIDEO, "YouTube video"
    if any(t.lower() in source for t in trusted):
        return BASE_TRUSTED, f"{item['source']} (established)"
    return BASE_UNKNOWN_NEWS, f"{item['source']} (not a known outlet)"


def score_item(item: dict, sources: set[str], fact: dict | None, trusted: list[str]) -> dict:
    base, why = source_base(item, trusted)
    credibility = base + min(len(sources), 3) * 10
    reason = why
    if sources:
        reason += f" · {len(sources) + 1} sources carry it"
    elif item["source_type"] not in UGC_TYPES:
        reason += " · only source so far"

    if fact:
        rating = (fact["rating"] or "").lower()
        if any(m in rating for m in FALSE_MARKERS):
            credibility = 10
            reason = f"Fact-checked \"{fact['rating']}\" by {fact['publisher']}"
        elif any(m in rating for m in TRUE_MARKERS):
            credibility = max(credibility, 85)
            reason = f"Fact-checked \"{fact['rating']}\" by {fact['publisher']} · {reason}"
        else:  # a rating we can't read either way — surface it, don't guess
            reason = f"Fact-check: \"{fact['rating']}\" ({fact['publisher']}) · {reason}"
    return {
        "id": item["id"],
        "corroboration": len(sources) + 1,
        "credibility": max(5, min(95, credibility)),  # never 0 or 100: this is a signal, not proof
        "cred_reason": reason,
        "factcheck_rating": fact["rating"] if fact else None,
        "factcheck_publisher": fact["publisher"] if fact else None,
        "factcheck_url": fact["url"] if fact else None,
        "checked_at": db.now_utc(),
    }


def check_pending(conn, cfg: dict) -> dict:
    ccfg = cfg.get("credibility") or {}
    counts = {"checked": 0, "lookups": 0, "factchecks": 0, "low": 0, "skipped": None}
    if not ccfg.get("enabled", True):
        counts["skipped"] = "disabled in config.yaml"
        return counts

    pending = db.unchecked_negatives(conn, ccfg.get("max_items_per_run", 120))
    if not pending:
        return counts

    pool = db.items_since(conn, db.hours_ago(ccfg.get("corroboration_window_hours", 48)))
    index, pool_tokens = build_index(pool)
    trusted = ccfg.get("trusted_sources") or []

    checker = None
    if ccfg.get("fact_check", True):
        key = os.getenv("YOUTUBE_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if key:
            checker = FactChecker(key, ccfg.get("fact_check_max_age_days", 0),
                                  ccfg.get("fact_check_min_similarity", 0.30))
        else:
            log.info("Fact-check lookup skipped: no Google API key set")

    skip_well_sourced = ccfg.get("fact_check_skip_well_sourced", True)
    budget = ccfg.get("fact_check_max_lookups", 30)
    # Each lookup costs ~2s, so spend the budget on the doubtful items: comments and unknown
    # sites first, established outlets last
    pending.sort(key=lambda it: source_base(it, trusted)[0])
    rows = []
    for item in pending:
        sources = corroborating_sources(item, index, pool, pool_tokens)
        # A lookup costs ~2s. Mainstream stories carried by several outlets are not what
        # fact-checkers write about, so spend the lookups on the doubtful items instead.
        well_sourced = len(sources) >= 2 and source_base(item, trusted)[0] == BASE_TRUSTED
        fact = None
        if checker and counts["lookups"] < budget and not (skip_well_sourced and well_sourced):
            fact = checker.lookup(item)
            counts["lookups"] += 1
        row = score_item(item, sources, fact, trusted)
        rows.append(row)
        counts["factchecks"] += bool(fact)
        counts["low"] += row["credibility"] < 40
    db.save_credibility(conn, rows)
    counts["checked"] = len(rows)
    return counts
