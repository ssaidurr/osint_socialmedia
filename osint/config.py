from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TICKETS_DIR = DATA_DIR / "tickets"


def load_config(path: str | os.PathLike | None = None) -> dict:
    load_dotenv(ROOT / ".env")
    with open(Path(path) if path else ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    TICKETS_DIR.mkdir(parents=True, exist_ok=True)
    return cfg


def to_local(iso_utc: str, cfg: dict, fmt: str = "%d %b %Y, %I:%M %p") -> str:
    tz = ZoneInfo(cfg.get("timezone", "Asia/Dhaka"))
    return datetime.fromisoformat(iso_utc).astimezone(tz).strftime(fmt)
