#!/usr/bin/env python3
"""Turn this monitor's own collection into a seed document a simulation engine can read.

MiroFish (and anything else of its kind) takes "seed materials" as .md, .txt or .pdf and
rejects CSV and JSON, so a database dump is no use to it. What it wants is a written brief: a
situation, the events behind it, the voices arguing about it, and how loud each has been. All
of that is already in osint.db — this writes it out in the order a reader needs it.

The shape is chosen for a *social* simulation rather than a statistical one. Such an engine
builds personas and lets them talk, so the brief carries not only what happened but who said
what and in what temper: news items establish the facts, and the forum and comment rows
establish the public mood those personas start from. A brief of only wire copy produces a
world of wire services.

    python tools/seed_pack.py --country IR --days 30 --out seed-IR.md
    python tools/seed_pack.py --category politics_unrest --days 14 --out seed-unrest.md

Nothing here is specific to one engine: the output is a Markdown briefing, which is also what
a human analyst would want to read first.
"""
from __future__ import annotations

import argparse
import datetime
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from osint import countries                                 # noqa: E402  (after the path fix)
from osint.analyzer import CATEGORIES                       # noqa: E402

DB = Path(__file__).resolve().parent.parent / "data" / "osint.db"

# Which rows count as the public talking rather than a newsroom reporting. The split matters:
# a simulation seeded only on news learns what was reported, not what people made of it.
CROWD = ("reddit_post", "reddit_comment", "youtube_comment", "mastodon_post", "telegram_post")


def label(key: str) -> str:
    return CATEGORIES.get(key, key or "uncategorised")


def rows(conn, sql: str, args: tuple) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(sql, args).fetchall()


def section(title: str) -> str:
    return f"\n## {title}\n\n"


def build(conn, country: str, category: str, days: int, quotes: int, events: int) -> str:
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    before = (datetime.date.today() - datetime.timedelta(days=days * 2)).isoformat()
    where, args = ["ts >= ?"], [since]
    if country:
        where.append("country = ?")
        args.append(country)
    if category:
        where.append("category = ?")
        args.append(category)
    clause = " AND ".join(where)

    place = countries.NAMES.get(country, country) if country else "worldwide"
    out = [f"# Situation brief — {place}\n",
           f"\nWindow: the {days} days to {datetime.date.today().isoformat()}. ",
           "Compiled from one monitor's own collection of news, forum and video-comment items, ",
           "each already classified by subject, sentiment and severity, and scored for how many ",
           "independent sources carry it.\n"]

    # ─── How much, and whether it is rising ───────────────────────────────────
    now = conn.execute(f"SELECT COUNT(*) FROM items WHERE {clause}", args).fetchone()[0]
    # The same filters over the window before this one, so "busy" can mean busy for here
    earlier = "".join(f" AND {w}" for w in where[1:])
    prior = conn.execute("SELECT COUNT(*) FROM items WHERE ts >= ? AND ts < ?" + earlier,
                         [before, since] + args[1:]).fetchone()[0]
    out.append(section("Volume"))
    move = "no earlier window to compare" if not prior else (
        f"{(now - prior) / prior:+.0%} against the {days} days before it ({prior} items)")
    out.append(f"{now} items in the window; {move}.\n\n")

    out.append("By subject:\n\n")
    for r in rows(conn, f"SELECT category, COUNT(*) n FROM items WHERE {clause} "
                        "GROUP BY category ORDER BY n DESC LIMIT 8", tuple(args)):
        out.append(f"- {label(r['category'])}: {r['n']}\n")

    tone = rows(conn, f"SELECT sentiment, COUNT(*) n FROM items WHERE {clause} "
                      "GROUP BY sentiment", tuple(args))
    total = sum(r["n"] for r in tone) or 1
    out.append("\nTone of the coverage: "
               + ", ".join(f"{r['sentiment']} {r['n'] / total:.0%}" for r in tone if r["sentiment"])
               + ".\n")

    # ─── The events themselves ────────────────────────────────────────────────
    # Ordered by corroboration before severity: a severe claim one outlet makes alone is worth
    # less to a simulation than a moderate one that fourteen outlets carry independently.
    out.append(section("What happened"))
    out.append("Ordered by how widely each is corroborated, then by severity. "
               "`sources` is how many independent outlets carry the story; "
               "`credibility` is this monitor's own 0–100 score for the publisher.\n\n")
    for r in rows(conn, f"""SELECT ts, title, summary, source, corroboration, credibility,
                                   severity, sentiment, category
                            FROM items WHERE {clause} AND source_type = 'news'
                            ORDER BY COALESCE(corroboration, 0) DESC,
                                     COALESCE(severity, 0) DESC, ts DESC LIMIT ?""",
                  tuple(args) + (events,)):
        out.append(f"### {r['ts'][:10]} — {r['title'][:160]}\n\n")
        if r["summary"]:
            out.append(f"{r['summary']}\n\n")
        out.append(f"- subject: {label(r['category'])}; tone: {r['sentiment']}; "
                   f"severity {r['severity']}/5\n"
                   f"- sources: {r['corroboration'] or 1}; credibility {r['credibility'] or '—'}; "
                   f"first seen at {r['source']}\n\n")

    # ─── Who is talking ───────────────────────────────────────────────────────
    out.append(section("Who is carrying it"))
    for r in rows(conn, f"SELECT source, COUNT(*) n, AVG(COALESCE(credibility,0)) c "
                        f"FROM items WHERE {clause} AND source_type = 'news' "
                        "GROUP BY source ORDER BY n DESC LIMIT 12", tuple(args)):
        out.append(f"- {r['source']}: {r['n']} items, mean credibility {r['c']:.0f}\n")

    # ─── And what the public is making of it ──────────────────────────────────
    out.append(section("Public reaction"))
    out.append("Verbatim posts and comments from forums and video comments in the same window. "
               "These are the opinions already in circulation — the priors a simulated "
               "population would start from, not the conclusions it should reach.\n\n")
    marks = ",".join("?" * len(CROWD))
    for r in rows(conn, f"""SELECT ts, title, text, source, sentiment, source_type
                            FROM items WHERE {clause} AND source_type IN ({marks})
                            ORDER BY ts DESC LIMIT ?""",
                  tuple(args) + CROWD + (quotes,)):
        said = (r["text"] or r["title"] or "").strip().replace("\n", " ")
        out.append(f"- [{r['ts'][:10]} · {r['source']} · {r['sentiment']}] {said[:260]}\n")

    # ─── The shape of the week ────────────────────────────────────────────────
    out.append(section("Day by day"))
    out.append("Items per day, and the share of them that read as negative.\n\n")
    for r in rows(conn, f"""SELECT substr(ts,1,10) d, COUNT(*) n,
                                   SUM(sentiment='negative') * 1.0 / COUNT(*) neg
                            FROM items WHERE {clause}
                            GROUP BY d ORDER BY d""", tuple(args)):
        out.append(f"- {r['d']}: {r['n']} items, {r['neg']:.0%} negative\n")

    return "".join(out)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--country", default="", help="two-letter code, e.g. IR; omit for worldwide")
    p.add_argument("--category", default="", help=f"one of: {', '.join(CATEGORIES)}")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--events", type=int, default=40, help="how many news stories to write out")
    p.add_argument("--quotes", type=int, default=60, help="how many public posts to quote")
    p.add_argument("--out", default="seed.md")
    a = p.parse_args()

    if not DB.exists():
        print(f"No database at {DB}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(DB)
    text = build(conn, a.country.upper(), a.category, a.days, a.quotes, a.events)
    conn.close()

    path = Path(a.out)
    path.write_text(text)
    print(f"  {path}  {len(text) / 1024:,.0f} KB  {text.count(chr(10))} lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
