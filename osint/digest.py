"""A short brief of what the monitor saw — the last 24 hours, or the last 7 days, in a few lines.

The numbers are counted in code; the LLM only turns the headlines it is given into sentences.
With no LLM key (or when the call fails) a plain rule-based brief is written instead, so the
dashboard and the daily email always have something to show.
"""
from __future__ import annotations

import html
import json
import logging
import os

from . import db
from .analyzer import CATEGORIES, resolve_provider
from .credibility import _containment, _grams

log = logging.getLogger(__name__)

SYSTEM = """You write a short news brief for a monitoring dashboard.

You are given headlines a monitor collected in one time window, plus counts it already computed.

Rules:
- Write in English, whatever language the headlines are in.
- 5 to 8 bullets, one short line each, most important first.
- Put related headlines in one bullet, and say how many sources carried the story when the input gives that number.
- Use ONLY what the input contains. Never add a number, name, place or event that is not there.
- If an item carries a fact-check verdict, say plainly that it is disputed, and name the fact-checker.
- Do NOT write an overall totals line — the counts are appended separately, in code.
- Output nothing but the bullets, each starting with "- ". No heading, no preamble, no bold."""

# Headlines shorter than this are usually a channel name or a stray fragment
MIN_TITLE = 15
# Two headlines this similar are the same story, so only the first one is sent
SAME_STORY = 0.60


def _window_label(hours: float) -> str:
    return f"last {hours:g} hours" if hours < 48 else f"last {hours / 24:g} days"


def pick_stories(conn, since: str, limit: int) -> list[dict]:
    """The window's distinct stories, widest coverage first — one line per story, not per outlet."""
    rows = conn.execute(
        """SELECT title, text, source, source_type, country, category, sentiment, severity,
                  corroboration, summary, credibility, factcheck_rating, factcheck_publisher
           FROM items
           WHERE sentiment IS NOT NULL AND ts >= ?
           ORDER BY COALESCE(corroboration, 1) DESC, COALESCE(severity, 0) DESC, ts DESC
           LIMIT 600""",
        (since,),
    )
    picked, seen = [], []
    for row in rows:
        item = dict(row)
        title = (item["title"] or item["text"] or "").strip()
        if len(title) < MIN_TITLE:
            continue
        grams = _grams(title)
        if any(_containment(grams, other) > SAME_STORY for other in seen):
            continue
        seen.append(grams)
        picked.append({
            "headline": title[:180],
            "source": item["source"],
            "kind": item["source_type"],
            "country": item["country"],
            "topic": CATEGORIES.get(item["category"], item["category"]) if item["sentiment"] == "negative" else None,
            "sentiment": item["sentiment"],
            "severity": item["severity"],
            "sources_carrying": item["corroboration"],
            "bangla_summary": item["summary"],
            "fact_check": (f"{item['factcheck_rating']} — {item['factcheck_publisher']}"
                           if item["factcheck_rating"] else None),
        })
        if len(picked) >= limit:
            break
    return picked


def window_facts(conn, since: str, hours: float) -> dict:
    stats = db.window_stats(conn, since)
    countries = {r["country"]: r["n"] for r in conn.execute(
        """SELECT country, COUNT(*) n FROM items WHERE sentiment IS NOT NULL AND country IS NOT NULL
           AND ts >= ? GROUP BY country ORDER BY n DESC LIMIT 6""", (since,))}
    disputed = conn.execute(
        "SELECT COUNT(*) FROM items WHERE factcheck_rating IS NOT NULL AND ts >= ?", (since,)).fetchone()[0]
    return {
        "window": _window_label(hours),
        "items": stats["total"],
        "negative": stats["negative"],
        "negative_share": round(stats["ratio"] * 100),
        "positive": stats["positive"],
        "top_topics": {CATEGORIES.get(k, k): n for k, n in list(stats["categories"].items())[:5]},
        "top_countries": countries,
        "fact_checked_as_disputed": disputed,
    }


def _facts_line(facts: dict) -> str:
    """The totals, written in code. An LLM asked to repeat figures will eventually mangle one
    (we watched 57% come back as "5.7%"), and a wrong number in a brief is worse than no number."""
    topics = ", ".join(list(facts["top_topics"])[:3]) or "—"
    countries = ", ".join(list(facts["top_countries"])[:4]) or "—"
    return (f"- Overall: {facts['items']:,} items · {facts['negative_share']}% negative · "
            f"countries: {countries} · topics: {topics}")


def _plain_brief(facts: dict, stories: list[dict]) -> str:
    """The brief without an LLM: the same material, stated plainly."""
    lines = []
    for story in stories[:6]:
        carried = f" ({story['sources_carrying']} sources)" if (story["sources_carrying"] or 0) > 1 else ""
        where = f"[{story['country']}] " if story["country"] else ""
        lines.append(f"- {where}{story['headline'][:120]}{carried}")
        if story["fact_check"]:
            lines.append(f"  ⚠ Fact-check: {story['fact_check']}")
    lines.append(_facts_line(facts))
    return "\n".join(lines)


def _ask_llm(cfg: dict, payload: dict) -> tuple[str | None, str | None]:
    """Returns (text, model). None when there is no key, or the call failed — the caller falls back."""
    provider = resolve_provider(cfg)
    prompt = json.dumps(payload, ensure_ascii=False)
    acfg = cfg.get("analyzer") or {}

    if provider == "gemini":
        from google import genai
        from google.genai import types

        model = acfg.get("gemini_model", "gemini-3.5-flash-lite")
        try:
            client = genai.Client(api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
            response = client.models.generate_content(
                model=model, contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    safety_settings=[
                        types.SafetySetting(category=c, threshold=types.HarmBlockThreshold.BLOCK_NONE)
                        for c in (types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                                  types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                                  types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                                  types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT)],
                ),
            )
        except Exception as e:  # quota, network, bad key — the plain brief still gets written
            log.warning("Digest: Gemini call failed (%s); writing the plain brief", str(e)[:120])
            return None, None
        return ((response.text or "").strip() or None), model

    if provider == "claude":
        import anthropic

        model = acfg.get("claude_model", "claude-opus-5")
        try:
            client = anthropic.Anthropic()
            response = client.beta.messages.create(
                model=model, max_tokens=2000,
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                system=SYSTEM, output_config={"effort": acfg.get("claude_effort", "low")},
                messages=[{"role": "user", "content": prompt}],
            )
            if response.stop_reason == "refusal":
                return None, None
            return next((b.text for b in response.content if b.type == "text"), "").strip() or None, model
        except Exception as e:
            log.warning("Digest: Claude call failed (%s); writing the plain brief", str(e)[:120])
            return None, None

    if provider == "openai":
        import openai

        model = acfg.get("openai_model", "gpt-5.4-mini")
        try:
            client = openai.OpenAI()
            response = client.responses.create(model=model, instructions=SYSTEM, input=prompt)
        except Exception as e:
            log.warning("Digest: OpenAI call failed (%s); writing the plain brief", str(e)[:120])
            return None, None
        return ((response.output_text or "").strip() or None), model

    return None, None


def build(conn, cfg: dict, hours: float) -> dict:
    """Write (and store) the brief for one window."""
    dcfg = cfg.get("digest") or {}
    since = db.hours_ago(hours)
    facts = window_facts(conn, since, hours)
    stories = pick_stories(conn, since, dcfg.get("max_stories", 45))

    if not stories:
        text, model = "- Nothing came in during this window.", None
    else:
        text, model = _ask_llm(cfg, {"facts": facts, "stories": stories})
        if text:
            text = f"{text.rstrip()}\n{_facts_line(facts)}"   # exact totals, not the model's
        else:
            text, model = _plain_brief(facts, stories), None

    db.save_digest(conn, hours, text, model, facts)
    return {"hours": hours, "text": text, "model": model, "facts": facts, "stories": len(stories)}


def send_daily(conn, cfg: dict, dry_run: bool = False) -> dict:
    """Email the 24-hour brief once a day, at the configured local hour."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from .alerts import send_email

    dcfg = cfg.get("digest") or {}
    result = {"sent": False, "reason": None}
    if not dcfg.get("email", False):
        result["reason"] = "digest email is off"
        return result

    local_now = datetime.now(ZoneInfo(cfg.get("timezone", "Asia/Dhaka")))
    today = local_now.date().isoformat()
    if local_now.hour < dcfg.get("email_hour", 9):
        result["reason"] = f"before {dcfg.get('email_hour', 9)}:00 local"
        return result
    if db.get_state(conn, "digest_email_date") == today:
        result["reason"] = "already sent today"
        return result

    brief = db.get_digest(conn, 24) or {}
    if not brief.get("text"):
        brief = build(conn, cfg, 24)
    facts = brief["facts"]
    subject = (f"OSINT brief — {local_now:%d %b %Y} — {facts['items']} items, "
               f"{facts['negative_share']}% negative")
    text_body = f"{_window_label(24).capitalize()}\n\n{brief['text']}\n"
    bullets = "".join(f"<li style='margin:6px 0'>{html.escape(line.lstrip('- ').strip())}</li>"
                      for line in brief["text"].splitlines() if line.strip())
    html_body = f"""<div style="font-family:-apple-system,Segoe UI,Roboto,'Noto Sans Bengali',sans-serif;max-width:680px;color:#101828;background:#ffffff;padding:20px;border-radius:10px">
<div style="font-size:12px;color:#667085">OSINT Monitor · {_window_label(24)}</div>
<h2 style="margin:4px 0 12px;font-size:19px">At a glance</h2>
<ul style="padding-left:18px;font-size:15px;line-height:1.5">{bullets}</ul>
<p style="font-size:12px;color:#667085">{facts['items']} items · {facts['negative_share']}% negative ·
top countries: {', '.join(list(facts['top_countries'])[:4]) or '—'}</p>
</div>"""

    if dry_run:
        result["reason"] = "dry run"
        return result
    if not (os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD")):
        result["reason"] = "SMTP_USER / SMTP_PASSWORD not set"
        return result
    try:
        send_email(cfg["alert"]["recipients"], subject, text_body, html_body)
    except Exception as e:
        log.error("Digest email failed: %s", e)
        result["reason"] = str(e)[:120]
        return result
    db.set_state(conn, "digest_email_date", today)
    result.update(sent=True, reason=subject)
    return result


def due(conn, cfg: dict, hours: float) -> bool:
    """Digests cost an LLM call, so they are refreshed on a schedule rather than every run."""
    dcfg = cfg.get("digest") or {}
    current = db.get_digest(conn, hours)
    if not current:
        return True
    # A 6-hour brief written 6 hours ago describes nothing the reader is looking at, so short
    # windows are rewritten sooner than long ones.
    stale_after = min(dcfg.get("every_hours", 6), max(1.0, hours / 4))
    return db.seconds_since(current["created_at"]) / 3600 >= stale_after
