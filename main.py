"""OSINT Social Media Monitor — command line.

    python main.py run            collect + analyze + check threshold (+ email ticket)
    python main.py run --dry-run  same, but never send email
    python main.py watch          repeat `run` every watch.interval_minutes
    python main.py report         negative-news analytics in the terminal
    python main.py test-email     send a test email to verify SMTP settings
"""
from __future__ import annotations

import argparse
import logging
import time

from osint import db
from osint.alerts import category_label, check_and_alert, send_test_email
from osint.analyzer import analyze_pending, backfill_country
from osint.collectors import collect_all
from osint.credibility import check_pending
from osint.config import load_config, to_local


def collect(conn, cfg) -> None:
    items, per_source = collect_all(cfg, conn)
    for name, n in per_source.items():
        print(f"  {name:<34} {n}")
    print(f"Collected {len(items)} items, {db.insert_items(conn, items)} new")


def analyze(conn, cfg, redo: bool = False, redo_all: bool = False, countries: bool = False) -> None:
    if countries:
        print(f"Filled country on {backfill_country(conn)} already-analyzed items")
        return
    if redo_all:
        print(f"Re-analyzing {db.reset_analysis(conn, None)} analyzed items")
    elif redo:
        print(f"Re-analyzing {db.reset_analysis(conn, 'lexicon')} lexicon-analyzed items")
    redo = redo or redo_all
    while True:  # one pass handles analyzer.max_items_per_run items; keep going until none are pending
        c = analyze_pending(conn, cfg)
        if not c["analyzed"]:
            break
        print(f"Analyzed {c['analyzed']} items (provider: {c['provider']}; "
              f"LLM {c['by_llm']}, lexicon {c['by_lexicon']})")
        if not redo:
            break


def credibility(conn, cfg, recheck: bool = False) -> None:
    if recheck:
        print(f"Re-checking {db.reset_credibility(conn)} negative items")
    c = check_pending(conn, cfg)
    if c["skipped"]:
        print(f"Credibility check off ({c['skipped']})")
        return
    print(f"Checked {c['checked']} negative items ({c['lookups']} fact-check lookups) — "
          f"published fact-check matched: {c['factchecks']}, low credibility: {c['low']}")


def check(conn, cfg, dry_run: bool) -> None:
    d = check_and_alert(conn, cfg, dry_run=dry_run)
    s = d["stats"]
    baseline = (f", normal {s['baseline_ratio']:.0%} over {cfg['alert'].get('baseline_days', 7)}d"
                if s.get("baseline_ratio") is not None else "")
    print(f"Last {cfg['alert']['window_hours']}h: {s['negative']}/{s['total']} negative "
          f"({s['ratio']:.0%}), bar {s.get('required_ratio', 0):.0%}{baseline}")
    if not d["triggered"]:
        print(f"No ticket: {d['reason']}")
        return
    t = d["ticket"]
    print(f"TICKET {t['id']} [{t['level']}] — status: {t['status']}")
    print(f"  {t['subject']}")
    print(f"  report: {t['report_path']}")
    if t.get("error"):
        print(f"  {t['error']}")


def run(conn, cfg, dry_run: bool) -> None:
    print("── collect"); collect(conn, cfg)
    print("── analyze"); analyze(conn, cfg)
    # Items analyzed before the country feature (or by an LLM that skipped it) get one offline pass
    if filled := backfill_country(conn):
        print(f"── country filled offline on {filled} older items")
    print("── credibility"); credibility(conn, cfg)
    print("── check");   check(conn, cfg, dry_run)
    if days := cfg.get("retention_days"):
        if n := db.prune(conn, days):
            print(f"── pruned {n} items collected more than {days} days ago")


def report(conn, cfg, hours: float) -> None:
    s = db.window_stats(conn, db.hours_ago(hours))
    print(f"Last {hours:g}h: {s['total']} analyzed items — negative {s['negative']} ({s['ratio']:.0%}), "
          f"neutral {s['neutral']}, positive {s['positive']}, avg negative severity {s['avg_severity']:.1f}/5")
    if s["negative"]:
        doubtful, checked, factchecked = conn.execute(
            """SELECT COALESCE(SUM(credibility < 40), 0), COUNT(credibility), COUNT(factcheck_rating)
               FROM items WHERE sentiment = 'negative' AND ts >= ?""", (s["since"],)).fetchone()
        print(f"Credibility: {doubtful} doubtful (< 40) of {checked} checked · "
              f"published fact-check matched: {factchecked}")
        print("\nNegative news by type:")
        top = max(s["categories"].values())
        for k, n in s["categories"].items():
            print(f"  {category_label(k):<48} {'█' * max(1, round(24 * n / top)):<24} {n:>4} ({n / s['negative']:.0%})")
        top_countries = conn.execute(
            """SELECT country, COUNT(*) n FROM items WHERE sentiment = 'negative' AND country IS NOT NULL
               AND ts >= ? GROUP BY country ORDER BY n DESC LIMIT 8""", (s["since"],)).fetchall()
        if top_countries:
            print("\nNegative items by country:")
            print("  " + " · ".join(f"{r['country']} {r['n']}" for r in top_countries))
        print("\nMost severe:")
        for it in db.top_negative(conn, s["since"], 8):
            print(f"  [{it['severity']}] {(it['title'] or it['text'])[:90]} — {it['source']}")
    tickets = db.recent_tickets(conn, 5)
    if tickets:
        print("\nRecent tickets:")
        for t in tickets:
            print(f"  {t['id']}  {t['level']:<8} {t['ratio']:.0%}  {t['status']:<8} {to_local(t['created_at'], cfg)}")


def main() -> None:
    p = argparse.ArgumentParser(description="OSINT social media & news monitor")
    p.add_argument("--config", help="path to config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("collect")
    ap = sub.add_parser("analyze")
    ap.add_argument("--redo", action="store_true",
                    help="re-analyze items the lexicon handled (after adding ANTHROPIC_API_KEY or editing keywords)")
    ap.add_argument("--redo-all", action="store_true", help="re-analyze every item, LLM-analyzed ones included")
    ap.add_argument("--countries", action="store_true",
                    help="fill the country of already-analyzed items offline (free, no LLM calls)")
    cp = sub.add_parser("credibility", help="score negative items: fact-check matches, corroboration, source quality")
    cp.add_argument("--recheck", action="store_true",
                    help="redo already-checked items (e.g. after enabling the Fact Check Tools API)")
    for name in ("check", "run", "watch"):
        sp = sub.add_parser(name)
        sp.add_argument("--dry-run", action="store_true", help="create the ticket report but do not email it")
        if name == "watch":
            sp.add_argument("--interval", type=float, help="minutes between runs")
    rp = sub.add_parser("report")
    rp.add_argument("--hours", type=float, default=24)
    sub.add_parser("test-email")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    conn = db.connect()

    if args.cmd == "collect":
        collect(conn, cfg)
    elif args.cmd == "analyze":
        analyze(conn, cfg, args.redo, args.redo_all, args.countries)
    elif args.cmd == "credibility":
        credibility(conn, cfg, args.recheck)
    elif args.cmd == "check":
        check(conn, cfg, args.dry_run)
    elif args.cmd == "run":
        run(conn, cfg, args.dry_run)
    elif args.cmd == "report":
        report(conn, cfg, args.hours)
    elif args.cmd == "test-email":
        send_test_email(cfg)
        print(f"Test email sent to {', '.join(cfg['alert']['recipients'])}")
    elif args.cmd == "watch":
        interval = args.interval or cfg.get("watch", {}).get("interval_minutes", 30)
        print(f"Watching every {interval:g} min — Ctrl+C to stop")
        try:
            while True:
                print(f"\n=== {to_local(db.now_utc(), cfg)} ===")
                try:
                    run(conn, cfg, args.dry_run)
                except Exception as e:  # one bad cycle must not kill the watcher
                    logging.exception("run failed: %s", e)
                time.sleep(interval * 60)
        except KeyboardInterrupt:
            print("\nStopped")


if __name__ == "__main__":
    main()
