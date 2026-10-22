"""Who asked for demo access, and until when.

Registrations live in a Google Sheet rather than in the app: Streamlit Cloud wipes the
filesystem on every reboot, and the team wants to read, filter and export the leads without
anyone building an admin page for it.
"""
from __future__ import annotations

import logging
import re
import secrets
from datetime import date, datetime, timedelta, timezone

import gspread

log = logging.getLogger(__name__)

COLUMNS = ["email", "name", "phone", "country", "first_seen", "expires",
           "last_seen", "visits", "token", "note"]
# Deliberately loose: the point is to catch typos, not to police exotic but valid addresses.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def clean_email(value: str) -> str:
    return (value or "").strip().lower()


def valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match(clean_email(value)))


def open_sheet(service_account_info: dict, sheet_id: str, worksheet: str = "users"):
    """The worksheet, with its header row in place."""
    client = gspread.service_account_from_dict(service_account_info, scopes=SCOPES)
    book = client.open_by_key(sheet_id)
    try:
        sheet = book.worksheet(worksheet)
    except gspread.WorksheetNotFound:
        sheet = book.add_worksheet(title=worksheet, rows=1000, cols=len(COLUMNS))
    if sheet.row_values(1) != COLUMNS:
        sheet.update(range_name="A1", values=[COLUMNS])
    return sheet


def everyone(sheet) -> list[dict]:
    return sheet.get_all_records(expected_headers=COLUMNS)


def _row_number(sheet, email: str) -> int | None:
    """Sheet row of this email, 1-based and counting the header."""
    column = sheet.col_values(1)
    email = clean_email(email)
    for i, value in enumerate(column[1:], start=2):
        if clean_email(value) == email:
            return i
    return None


def find(sheet, email: str) -> dict | None:
    email = clean_email(email)
    return next((u for u in everyone(sheet) if clean_email(u.get("email", "")) == email), None)


def find_by_token(sheet, token: str) -> dict | None:
    if not token:
        return None
    return next((u for u in everyone(sheet) if str(u.get("token", "")) == token), None)


def clean_phone(value: str) -> str:
    """Digits only, last ten of them: +880 1711-223344, 01711223344 and 1711223344 are one person."""
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if len(digits) >= 10 else digits


def find_by_phone(sheet, phone: str) -> dict | None:
    """The row already holding this number — one trial per phone, whatever the address."""
    wanted = clean_phone(phone)
    if not wanted:
        return None
    return next((u for u in everyone(sheet) if clean_phone(str(u.get("phone", ""))) == wanted), None)


def register(sheet, email: str, name: str, phone: str, country: str,
             trial_days: int = 14, note: str = "") -> dict:
    today = datetime.now(timezone.utc).date()
    user = {
        "email": clean_email(email), "name": name.strip(), "country": country.strip(),
        "phone": phone.strip(), "first_seen": today.isoformat(),
        "expires": (today + timedelta(days=trial_days)).isoformat(),
        "last_seen": today.isoformat(), "visits": 1,
        "token": secrets.token_urlsafe(16), "note": note,
    }
    sheet.append_row([user[c] for c in COLUMNS], value_input_option="USER_ENTERED")
    return user


def touch(sheet, user: dict) -> None:
    """Record the visit. Never let a failed write keep somebody out of the demo."""
    try:
        row = _row_number(sheet, user["email"])
        if not row:
            return
        today = datetime.now(timezone.utc).date().isoformat()
        visits = int(user.get("visits") or 0) + 1
        sheet.update(range_name=f"G{row}:H{row}", values=[[today, visits]])
    except Exception as e:
        log.warning("Could not record the visit for %s: %s", user.get("email"), e)


def days_left(user: dict, today: date | None = None) -> int:
    """Whole days of trial remaining; 0 or less means it has run out."""
    today = today or datetime.now(timezone.utc).date()
    try:
        expires = date.fromisoformat(str(user.get("expires", "")).strip()[:10])
    except ValueError:
        return 0
    return (expires - today).days


def is_active(user: dict, today: date | None = None) -> bool:
    return days_left(user, today) > 0
