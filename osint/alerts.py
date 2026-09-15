"""Negative-news threshold check -> ticket -> email to the configured recipients."""
from __future__ import annotations

import html
import logging
import os
import secrets
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import certifi

from . import db
from .analyzer import CATEGORIES
from .config import TICKETS_DIR, to_local

log = logging.getLogger(__name__)

# Tickets that count for the cooldown. Failed sends are retried on the next run;
# dry runs never block a real ticket.
COOLDOWN_STATUSES = ("sent", "no_smtp")


def category_label(key: str | None) -> str:
    return CATEGORIES.get(key or "other", key or "other")


def ticket_level(stats: dict, threshold: float) -> str:
    if stats["ratio"] >= threshold + 0.25 or stats["avg_severity"] >= 4:
        return "CRITICAL"
    if stats["ratio"] >= threshold + 0.10 or stats["avg_severity"] >= 3:
        return "HIGH"
    return "MEDIUM"


def evaluate(conn, cfg: dict) -> dict:
    """Decide whether the current window deserves a ticket. Pure read, no side effects."""
    a = cfg["alert"]
    stats = db.window_stats(conn, db.hours_ago(a["window_hours"]))
    decision = {"stats": stats, "triggered": False}
    if stats["total"] < a["min_items"]:
        decision["reason"] = f"not enough data ({stats['total']} < {a['min_items']} items)"
    elif stats["ratio"] < a["negative_ratio_threshold"]:
        decision["reason"] = f"negative share {stats['ratio']:.0%} below {a['negative_ratio_threshold']:.0%}"
    elif stats["negative"] < a["min_negative_count"]:
        decision["reason"] = f"only {stats['negative']} negative items (< {a['min_negative_count']})"
    else:
        last = db.last_ticket(conn, COOLDOWN_STATUSES)
        cooldown_start = datetime.now(timezone.utc) - timedelta(hours=a["cooldown_hours"])
        in_cooldown = last and datetime.fromisoformat(last["created_at"]) > cooldown_start
        if in_cooldown and stats["ratio"] < last["ratio"] + a["escalation_delta"]:
            decision["reason"] = f"cooldown: ticket {last['id']} already raised at {last['ratio']:.0%}"
        else:
            decision["triggered"] = True
            decision["reason"] = "escalation" if in_cooldown else "threshold crossed"
    return decision


def check_and_alert(conn, cfg: dict, dry_run: bool = False) -> dict:
    decision = evaluate(conn, cfg)
    if decision["triggered"]:
        decision["ticket"] = create_ticket(conn, cfg, decision["stats"], dry_run)
    return decision


def create_ticket(conn, cfg: dict, stats: dict, dry_run: bool) -> dict:
    a = cfg["alert"]
    created = db.now_utc()
    ticket_id = f"OSINT-{datetime.now(timezone.utc):%Y%m%d-%H%M}-{secrets.token_hex(2).upper()}"
    items = db.top_negative(conn, stats["since"], a.get("top_items_in_email", 10))
    ticket = {
        "id": ticket_id, "created_at": created, "window_hours": a["window_hours"],
        "total": stats["total"], "negative": stats["negative"], "ratio": stats["ratio"],
        "level": ticket_level(stats, a["negative_ratio_threshold"]),
        "categories": stats["categories"], "item_ids": [i["id"] for i in items],
        "status": "dry_run", "error": None,
    }
    subject, text_body, html_body = render_email(cfg, ticket, stats, items)
    report_path = TICKETS_DIR / f"{ticket_id}.html"
    report_path.write_text(html_body, encoding="utf-8")
    ticket["report_path"] = str(report_path)

    if not dry_run:
        if not (os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD")):
            ticket["status"] = "no_smtp"
            ticket["error"] = "SMTP_USER / SMTP_PASSWORD not set in .env — email not sent"
            log.warning(ticket["error"])
        else:
            try:
                send_email(a["recipients"], subject, text_body, html_body)
                ticket["status"] = "sent"
            except (smtplib.SMTPException, OSError) as e:
                ticket["status"] = "failed"
                ticket["error"] = str(e)
                log.error("Email for %s failed: %s", ticket_id, e)
    db.save_ticket(conn, ticket)
    ticket["subject"] = subject
    return ticket


def send_email(recipients: list[str], subject: str, text_body: str, html_body: str) -> None:
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    user, password = os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"]
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.getenv("ALERT_FROM") or user
    msg["To"] = ", ".join(recipients)
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context(cafile=certifi.where())
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=context)
            s.login(user, password)
            s.send_message(msg)


def render_email(cfg: dict, ticket: dict, stats: dict, items: list[dict]) -> tuple[str, str, str]:
    level, ratio = ticket["level"], stats["ratio"]
    window = f"last {ticket['window_hours']:g}h"
    subject = (f"[OSINT ALERT][{level}] Negative news {ratio:.0%} "
               f"({stats['negative']}/{stats['total']}) — {window} — {ticket['id']}")
    created = to_local(ticket["created_at"], cfg)
    cats = sorted(stats["categories"].items(), key=lambda kv: -kv[1])

    # Plain-text part
    lines = [
        f"Ticket: {ticket['id']}   Level: {level}   Created: {created}",
        f"Window: {window}   Negative: {stats['negative']} of {stats['total']} ({ratio:.0%})"
        f"   Avg severity: {stats['avg_severity']:.1f}/5",
        "", "Negative news by type:",
        *[f"  - {category_label(k)}: {n} ({n / stats['negative']:.0%})" for k, n in cats],
        "", "Top negative items:",
    ]
    for i, it in enumerate(items, 1):
        lines.append(f"{i}. [{category_label(it['category'])} · severity {it['severity']}] "
                     f"{it['title'] or it['text'][:120]} — {it['source']}")
        if it.get("summary"):
            lines.append(f"   {it['summary']}")
        lines.append(f"   {it['url']}")
    text_body = "\n".join(lines)

    # HTML part (inline styles — email clients ignore <style> blocks)
    e = html.escape

    def safe_url(url: str | None) -> str:  # feed-supplied links: never allow javascript:/data: hrefs
        return url if url and url.startswith(("https://", "http://")) else "#"

    level_color = {"CRITICAL": "#b42318", "HIGH": "#c4320a", "MEDIUM": "#b54708"}[level]
    cat_rows = "".join(
        f"<tr><td style='padding:6px 12px 6px 0'>{e(category_label(k))}</td>"
        f"<td style='padding:6px 12px 6px 0;width:220px'>"
        f"<div style='background:#fde2dc;border-radius:4px;height:10px;width:200px'>"
        f"<div style='background:#d92d20;border-radius:4px;height:10px;width:{round(200 * n / cats[0][1])}px'></div></div></td>"
        f"<td style='padding:6px 0;text-align:right'><b>{n}</b> ({n / stats['negative']:.0%})</td></tr>"
        for k, n in cats)
    item_rows = "".join(
        f"<tr><td style='padding:10px 0;border-top:1px solid #eee'>"
        f"<a href='{e(safe_url(it['url']))}' style='color:#1d4ed8;font-weight:600;text-decoration:none'>"
        f"{e(it['title'] or it['text'][:160])}</a><br>"
        + (f"<span style='color:#333'>{e(it['summary'])}</span><br>" if it.get("summary") else "")
        + f"<span style='color:#667085;font-size:12px'>{e(it['source'])} · {e(category_label(it['category']))}"
        f" · severity {it['severity']}/5 · {e(to_local(it['ts'], cfg))}</span></td></tr>"
        for it in items)
    html_body = f"""<div style="font-family:-apple-system,Segoe UI,Roboto,'Noto Sans Bengali',sans-serif;max-width:680px;color:#101828">
<div style="border-left:6px solid {level_color};padding:12px 16px;background:#fff6f5">
  <div style="font-size:12px;color:#667085">OSINT Social Media Monitor · Ticket</div>
  <div style="font-size:20px;font-weight:700">{e(ticket['id'])} <span style="color:{level_color}">[{level}]</span></div>
  <div style="font-size:13px;color:#475467">{e(created)} · {window}</div>
</div>
<p style="font-size:15px">Negative content crossed the alert threshold:
<b style="font-size:22px">{ratio:.0%}</b> negative ({stats['negative']} of {stats['total']} items),
average severity <b>{stats['avg_severity']:.1f}/5</b>.</p>
<h3 style="margin:20px 0 6px">কী ধরনের নেগেটিভ নিউজ (by type)</h3>
<table style="border-collapse:collapse;font-size:14px">{cat_rows}</table>
<h3 style="margin:20px 0 6px">Top negative items</h3>
<table style="border-collapse:collapse;font-size:14px;width:100%">{item_rows}</table>
<p style="font-size:12px;color:#667085;margin-top:24px">Generated automatically. Open the dashboard
(<code>streamlit run dashboard.py</code>) for the full breakdown.</p>
</div>"""
    return subject, text_body, html_body


def send_test_email(cfg: dict) -> None:
    recipients = cfg["alert"]["recipients"]
    send_email(recipients, "[OSINT] Test email — SMTP is working",
               "This is a test from the OSINT Social Media Monitor. Alerts will arrive like this.",
               "<p>This is a test from the <b>OSINT Social Media Monitor</b>. Alerts will arrive like this.</p>")
