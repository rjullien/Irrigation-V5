"""Rotation slot (créneau) helpers for multi-program staggered schedules.

User-facing: créneau 1..N (1er / 2e / … jour du cycle).
Engine: (today - ROTATION_EPOCH).days % freq == (créneau - 1).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

ROTATION_EPOCH = date(2026, 1, 1)
CRENEAU_OPTIONS = ["1", "2", "3", "4", "5", "6", "7"]


def creneau_to_start_date(creneau: int | str) -> str:
    """Map créneau (1-based) to ISO date stored as freq_start_date."""
    slot = max(1, int(creneau))
    return (ROTATION_EPOCH + timedelta(days=slot - 1)).isoformat()


def start_date_to_creneau(start_date_str: str, max_slot: int = 7) -> str:
    """Derive créneau option from an existing freq_start_date."""
    if not start_date_str or not str(start_date_str).strip():
        return "1"
    try:
        raw = str(start_date_str).strip()[:10]
        d = date.fromisoformat(raw)
    except (ValueError, TypeError):
        return "1"
    days = (d - ROTATION_EPOCH).days
    slot = (days % max_slot) + 1
    return str(slot)


def days_until_creneau(today: date, freq: int, creneau: int | str) -> int:
    """Days until next run for this créneau (0 = today)."""
    if freq <= 1:
        return 0
    slot = max(1, min(int(creneau), freq))
    target_mod = slot - 1
    current_mod = (today - ROTATION_EPOCH).days % freq
    return (target_mod - current_mod) % freq


def parse_local_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip()[:10])
