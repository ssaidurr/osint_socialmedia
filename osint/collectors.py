"""Collectors for public sources. Each returns a list of item dicts ready for db.insert_items."""
from __future__ import annotations

import hashlib
import html
import logging
import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import feedparser
import requests

from .db import get_state, now_utc, set_state

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; osint-socialmedia-monitor/1.0)"}
TAG_RE = re.compile(r"<[^>]+>")
YT_API = "https://www.googleapis.com/youtube/v3"


def clean(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", html.unescape(TAG_RE.sub(" ", s))).strip()


def make_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:20]


def _norm_title(title: str) -> str:
    # Same headline from Google News and the publisher's own feed collapses to one item
    return re.sub(r"[\W_]+", "", title.lower())


def _entry_ts(entry) -> str:
    st = entry.get("published_parsed") or entry.get("updated_parsed")
    if st:
        return datetime(*st[:6], tzinfo=timezone.utc).isoformat(timespec="seconds")
    return now_utc()


def _iso(s: str) -> str:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat(timespec="seconds")


def _fetch_feed(url: str, retries: int = 2):
    # Fetch with requests (certifi CA bundle) rather than feedparser's own urllib,
    # which fails TLS verification on python.org macOS builds.
    for attempt in range(retries + 1):
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code == 429 and attempt < retries:
            wait = min(int(r.headers.get("Retry-After", 0) or 0) or 5 * (attempt + 1), 30)
            log.info("429 from %s, retrying in %ss", url, wait)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return feedparser.parse(r.content)


def _news_items(url: str, source: str, from_google: bool = False) -> list[dict]:
    collected, out = now_utc(), []
    for e in _fetch_feed(url).entries:
        title, publisher, text = clean(e.get("title")), source, clean(e.get("summary"))
        if from_google:
            publisher = (e.get("source") or {}).get("title") or source
            title = title.removesuffix(f" - {publisher}")
            text = ""  # Google News summaries only repeat the title and publisher
        if not title:
            continue
        out.append({
            "id": make_id("news", _norm_title(title)),
            "source": publisher,
            "source_type": "news",
            "title": title,
            "text": text[:2000],
            "url": e.get("link"),
            "author": clean(e.get("author")) or None,
            "ts": _entry_ts(e),
            "collected_at": collected,
        })
    return out


def google_news(query: str, lang: str, country: str, window: str) -> list[dict]:
    q = urllib.parse.quote(f"{query} when:{window}")
    url = f"https://news.google.com/rss/search?q={q}&hl={lang}&gl={country}&ceid={country}:{lang}"
    return _news_items(url, "Google News", from_google=True)


def rss_feed(name: str, url: str) -> list[dict]:
    return _news_items(url, name)


def reddit(subreddit: str, source_type: str) -> list[dict]:
    """source_type: reddit_post (newest posts) or reddit_comment (newest comments)."""
    collected, out = now_utc(), []
    path = "new" if source_type == "reddit_post" else "comments"
    for source_type, url in [(source_type, f"https://www.reddit.com/r/{subreddit}/{path}/.rss")]:
        for e in _fetch_feed(url).entries:
            body = (e.get("content") or [{}])[0].get("value") or e.get("summary")
            text = re.sub(r"submitted by\s+/u/.*$", "", clean(body)).strip()
            out.append({
                "id": make_id(source_type, e.get("link") or e.get("id", "")),
                "source": f"r/{subreddit}",
                "source_type": source_type,
                "title": clean(e.get("title")),
                "text": text[:2000],
                "url": e.get("link"),
                "author": clean(e.get("author")) or None,
                "ts": _entry_ts(e),
                "collected_at": collected,
            })
    time.sleep(3)  # Reddit rate-limits unauthenticated feeds hard; space the requests out
    return out


def youtube(query: str, api_key: str, max_results: int, comments_per_video: int, lookback_hours: float) -> list[dict]:
    collected, out = now_utc(), []
    after = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Key goes in a header, not the URL: requests puts the URL in error messages, which end up in logs
    headers = {"X-Goog-Api-Key": api_key}
    r = requests.get(f"{YT_API}/search", timeout=20, headers=headers, params={
        "part": "snippet", "q": query, "type": "video", "order": "date", "regionCode": "BD",
        "maxResults": max_results, "publishedAfter": after,
    })
    r.raise_for_status()
    for v in r.json().get("items", []):
        vid, sn = v["id"]["videoId"], v["snippet"]
        video_url = f"https://www.youtube.com/watch?v={vid}"
        out.append({
            "id": make_id("youtube_video", vid),
            "source": sn.get("channelTitle") or "YouTube",
            "source_type": "youtube_video",
            "title": clean(sn.get("title")),
            "text": clean(sn.get("description"))[:2000],
            "url": video_url,
            "author": sn.get("channelTitle"),
            "ts": _iso(sn["publishedAt"]),
            "collected_at": collected,
        })
        if not comments_per_video:
            continue
        c = requests.get(f"{YT_API}/commentThreads", timeout=20, headers=headers, params={
            "part": "snippet", "videoId": vid, "maxResults": comments_per_video,
            "order": "relevance", "textFormat": "plainText",
        })
        if c.status_code == 403:  # comments disabled on this video
            continue
        c.raise_for_status()
        for t in c.json().get("items", []):
            cs = t["snippet"]["topLevelComment"]["snippet"]
            out.append({
                "id": make_id("youtube_comment", t["id"]),
                "source": sn.get("channelTitle") or "YouTube",
                "source_type": "youtube_comment",
                "title": clean(sn.get("title")),
                "text": clean(cs.get("textDisplay"))[:2000],
                "url": f"{video_url}&lc={t['id']}",
                "author": cs.get("authorDisplayName"),
                "ts": _iso(cs["publishedAt"]),
                "collected_at": collected,
            })
    return out


def _youtube_due(conn, every_minutes: float) -> bool:
    last = get_state(conn, "youtube_last_run") if conn is not None else None
    return not last or datetime.now(timezone.utc) - datetime.fromisoformat(last) >= timedelta(minutes=every_minutes)


def collect_all(cfg: dict, conn=None) -> tuple[list[dict], dict[str, int | str]]:
    """Run every configured collector. A failing source is logged and skipped, not fatal.
    `conn` lets rate-limited sources (YouTube) remember when they last ran."""
    jobs = []
    gn = cfg.get("google_news") or {}
    for q in gn.get("queries", []):
        jobs.append((f"Google News: {q['query']}",
                     lambda q=q: google_news(q["query"], q.get("lang", "en"), gn.get("country", "BD"), gn.get("window", "1d"))))
    for f in cfg.get("rss_feeds") or []:
        jobs.append((f"RSS: {f['name']}", lambda f=f: rss_feed(f["name"], f["url"])))
    rd = cfg.get("reddit") or {}
    # Posts and comments are separate jobs so a rate-limited comments feed doesn't discard the posts
    reddit_kinds = ["reddit_post"] + (["reddit_comment"] if rd.get("include_comments", True) else [])
    for sub in rd.get("subreddits", []):
        for kind in reddit_kinds:
            label = "posts" if kind == "reddit_post" else "comments"
            jobs.append((f"Reddit: r/{sub} {label}", lambda sub=sub, kind=kind: reddit(sub, kind)))
    yt, yt_key = cfg.get("youtube") or {}, os.getenv("YOUTUBE_API_KEY")
    summary: dict[str, int | str] = {}
    if yt.get("queries") and yt_key:
        # search.list has its own 100-calls/day quota, so YouTube runs less often than the rest
        every = yt.get("every_minutes", 60)
        if _youtube_due(conn, every):
            for q in yt["queries"]:
                jobs.append((f"YouTube: {q}", lambda q=q: youtube(
                    q, yt_key, yt.get("max_results", 15), yt.get("comments_per_video", 20), yt.get("lookback_hours", 24))))
            if conn is not None:
                set_state(conn, "youtube_last_run", now_utc())
        else:
            summary["YouTube"] = f"skipped (runs every {every:g} min to stay within quota)"
    elif yt.get("queries"):
        log.info("YouTube skipped: set YOUTUBE_API_KEY in .env to enable it")

    items = []
    for name, job in jobs:
        try:
            got = job()
            items.extend(got)
            summary[name] = len(got)
        except Exception as e:  # network errors, bad feeds, quota — keep the other sources going
            log.warning("%s failed: %s", name, e)
            summary[name] = f"error: {e}"
    return items, summary
