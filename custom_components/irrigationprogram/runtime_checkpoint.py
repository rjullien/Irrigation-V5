"""Persist mid-cycle watering state across Home Assistant restarts.

When HA reboots during a cycle the asyncio watering tasks are cancelled, but
hardware valves stay open. Without a checkpoint the integration would force
solenoids off at startup and never finish the remaining queue.

This module stores a compact snapshot (Store) so the program can resume with
remaining time adjusted for downtime.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

STORAGE_KEY = f"{DOMAIN}.runtime_checkpoint"
STORAGE_VERSION = 1

# Drop resume if downtime exceeds remaining + this grace (seconds)
MAX_RESUME_OVERSHOOT_S = 300


def checkpoint_store(hass: HomeAssistant) -> Store:
    """Return the shared Store for all irrigation programs."""
    return Store(hass, STORAGE_VERSION, STORAGE_KEY)


def _iso(now: datetime | None = None) -> str:
    return dt_util.as_utc(now or dt_util.utcnow()).isoformat()


def build_checkpoint(
    *,
    program_unique_id: str,
    program_name: str,
    scheduled: bool,
    start_time: datetime | None,
    paused: bool,
    running: list[dict[str, Any]],
    remaining: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a serialisable checkpoint payload."""
    return {
        "version": STORAGE_VERSION,
        "program_unique_id": program_unique_id,
        "program_name": program_name,
        "scheduled": bool(scheduled),
        "paused": bool(paused),
        "start_time": _iso(start_time) if start_time else None,
        "checkpoint_ts": _iso(),
        "running": running,
        "remaining": remaining,
    }


def apply_downtime(
    checkpoint: dict[str, Any], now: datetime | None = None
) -> dict[str, Any] | None:
    """Subtract elapsed downtime from remaining seconds.

    Returns None if nothing left to run (or checkpoint is stale/invalid).
    """
    if not checkpoint or checkpoint.get("version") != STORAGE_VERSION:
        return None

    ts_raw = checkpoint.get("checkpoint_ts")
    if not ts_raw:
        return None
    checkpoint_ts = dt_util.parse_datetime(ts_raw)
    if checkpoint_ts is None:
        return None
    checkpoint_ts = dt_util.as_utc(checkpoint_ts)
    now_utc = dt_util.as_utc(now or dt_util.utcnow())
    delta = max(0, int((now_utc - checkpoint_ts).total_seconds()))

    running_out: list[dict[str, Any]] = []
    for item in checkpoint.get("running") or []:
        rem = max(0, int(item.get("remaining_s", 0)) - delta)
        if rem > 0:
            running_out.append({**item, "remaining_s": rem})
        # rem == 0 → zone finished during downtime; skip (do not re-water)

    remaining_out: list[dict[str, Any]] = []
    for item in checkpoint.get("remaining") or []:
        # Queued zones have not started watering — keep full remaining
        rem = max(0, int(item.get("remaining_s", 0)))
        if rem > 0:
            remaining_out.append({**item, "remaining_s": rem})

    if not running_out and not remaining_out:
        return None

    # Safety: absurdly old checkpoint with huge remaining still OK to resume,
    # but if downtime alone exceeds all running remainings + grace and queue
    # empty, apply_downtime already returned empty running.
    out = dict(checkpoint)
    out["running"] = running_out
    out["remaining"] = remaining_out
    out["downtime_s"] = delta
    out["checkpoint_ts"] = _iso(now_utc)
    return out


def zone_should_skip_startup_off(
    checkpoint: dict[str, Any] | None, solenoid: str | None
) -> bool:
    """True if this solenoid was watering at checkpoint — keep valve open."""
    if not checkpoint or not solenoid:
        return False
    adjusted = apply_downtime(checkpoint)
    if not adjusted:
        return False
    for item in adjusted.get("running") or []:
        if item.get("solenoid") == solenoid and int(item.get("remaining_s", 0)) > 0:
            return True
    return False
