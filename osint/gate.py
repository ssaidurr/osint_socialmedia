"""The demo's front door: a short form, then 14 days of access.

Nobody sees the dashboard without leaving a way to be contacted, and an address that has had
its run is told so plainly rather than quietly shown stale data.
"""
from __future__ import annotations

import logging
import os

import streamlit as st

from . import registry

log = logging.getLogger(__name__)


def _secret(name: str):
    try:
        return st.secrets.get(name)
    except Exception:      # no secrets file at all, which is normal on a laptop
        return None


@st.cache_resource(ttl=3600, show_spinner=False)
def _sheet(sheet_id: str, worksheet: str):
    return registry.open_sheet(dict(st.secrets["gcp_service_account"]), sheet_id, worksheet)


def _admin_user(email: str) -> dict:
    """Us, not a lead: no sheet row, no trial, nothing to fill in."""
    return {"email": registry.clean_email(email), "name": "Admin", "admin": True,
            "expires": "2099-12-31", "token": "", "visits": 0}


def _is_admin(email: str, conf: dict) -> bool:
    admins = [registry.clean_email(a) for a in (conf.get("admin_emails") or [])]
    return registry.clean_email(email) in admins


def _admit(user: dict, sheet) -> dict:
    st.session_state["demo_user"] = user
    if user.get("token"):
        st.query_params["k"] = str(user["token"])   # survives a refresh
    if not user.get("admin"):
        registry.touch(sheet, user)
    return user


def _expired(user: dict, contact: str) -> None:
    st.warning(f"The demo for **{user['email']}** ended on {user.get('expires', '—')}.")
    reach_us = f"write to [{contact}](mailto:{contact})" if contact else "get in touch with us"
    st.markdown(f"To keep access, {reach_us} and we will extend it.")
    st.stop()


def _form(sheet, conf: dict) -> dict:
    trial_days = conf.get("trial_days", 14)
    contact = conf.get("contact_email", "")

    st.markdown("#### Get access to the demo")
    st.caption(f"{trial_days} days, free. We ask who you are so we can follow up — nothing else. "
               f"Already registered? Your email alone is enough.")

    with st.form("gate_form"):
        email = st.text_input("Work email", placeholder="you@company.com")
        name = st.text_input("Your name")
        phone = st.text_input("Phone")
        country = st.text_input("Country")
        submitted = st.form_submit_button(f"Start my {trial_days} days", type="primary")

    if submitted:
        if not registry.valid_email(email):
            st.error("That does not look like an email address.")
            st.stop()
        if _is_admin(email, conf):
            _admit(_admin_user(email), sheet)
            st.rerun()

        user = registry.find(sheet, email)
        if user and registry.is_active(user):
            _admit(user, sheet)               # already registered: the other fields are not needed
            st.rerun()
        if user:
            _expired(user, contact)

        if not (name.strip() and phone.strip() and country.strip()):
            st.error("First time here — please fill in your name, phone and country too.")
            st.stop()
        # One trial per phone as well as per address, or a second address buys another 14 days
        already = registry.find_by_phone(sheet, phone)
        if already:
            st.error(f"This phone number is already registered under {already['email']}. "
                     f"Sign in with that address, or get in touch with us.")
            st.stop()
        user = registry.register(sheet, email, name, phone, country, trial_days)
        _admit(user, sheet)
        st.rerun()
    st.stop()


def require_access(cfg: dict) -> dict | None:
    """The signed-in user. Renders the form and stops the script when there isn't one."""
    conf = cfg.get("demo_access") or {}
    if not conf.get("enabled", True):
        return None                            # the gate is switched off in config.yaml
    sheet_id = _secret("gsheet_id")
    if not sheet_id:
        if os.getenv("DEMO_OPEN") == "1":
            return None                        # working on it locally, deliberately
        if conf.get("require", True):
            # Fail closed: a misconfigured deployment must not hand the demo to everyone
            st.error("Demo access is switched on but not configured — this app needs `gsheet_id` "
                     "and `[gcp_service_account]` in its secrets.")
            st.stop()
        return None

    try:
        sheet = _sheet(sheet_id, conf.get("worksheet", "users"))
    except Exception as e:
        log.exception("Cannot reach the registration sheet")
        st.error("Sign-up is unavailable right now. Please try again in a few minutes.")
        # gspread hides the real cause behind a bare PermissionError, and without the detail
        # there is nothing to go on: "not shared with the service account", "API not enabled"
        # and "wrong sheet id" all look identical from here.
        cause = e.__cause__ or e
        detail = " ".join(str(cause).split())[:300] or type(cause).__name__
        st.caption(f"Reason: {detail}")
        st.stop()

    user = st.session_state.get("demo_user")
    if user and registry.is_active(user):
        return user

    token = st.query_params.get("k")
    if token:
        user = registry.find_by_token(sheet, token)
        if user and registry.is_active(user):
            return _admit(user, sheet)
        if user:
            _expired(user, conf.get("contact_email", ""))
        st.query_params.pop("k", None)

    return _form(sheet, conf)


def sidebar_note(user: dict | None) -> None:
    """Who is signed in, and how long they have left."""
    if not user:
        return
    if user.get("admin"):
        st.caption(f"Signed in as {user['email']} · admin")
        return
    left = registry.days_left(user)
    st.caption(f"Signed in as {user['email']} · {left} day{'s' if left != 1 else ''} of demo access left")
