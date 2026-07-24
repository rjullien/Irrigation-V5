"""Persist mid-cycle watering state across Home Assistant restarts.

When HA reboots during a cycle the asyncio watering tasks are cancelled, but
hardware valves stay open. Without a checkpoint the integration would force
solenoids off at startup and never finish the remaining queue.

This module stores a compact snapshot (Store) so the program can resume with
remaining time adjusted for downtime.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = f"{DOMAIN}.runtime_checkpoint"
STORAGE_VERSION = 1

# Reject resume when downtime exceeds (sum of checkpoint remainings) + grace.
# Avoids resurrecting a cycle days later after HA was offline.
MAX_RESUME_OVERSHOOT_S = 300

_CHECKPOINT_LOCK_KEY = "_runtime_checkpoint_lock"


def checkpoint_store(hass: HomeAssistant) -> Store:
    """Return the shared Store for all irrigation programs."""
    return Store(hass, STORAGE_VERSION, STORAGE_KEY)


def checkpoint_lock(hass: HomeAssistant) -> asyncio.Lock:
    """Process-wide lock for read-modify-write on the shared Store."""
    domain = hass.data.setdefault(DOMAIN, {})
    lock = domain.get(_CHECKPOINT_LOCK_KEY)
    if lock is None:
        lock = asyncio.Lock()
        domain[_CHECKPOINT_LOCK_KEY] = lock
    return lock


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
    """Build a serialisable checkpoint payload.

    ``program_unique_id`` is also the Store dict key (``entry.entry_id``).
    """
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
    checkpoint: dict[str, Any],
    now: datetime | None = None,
    *,
    sequential: bool = True,
) -> dict[str, Any] | None:
    """Subtract elapsed downtime from remaining seconds.

    Returns None if nothing left to run, checkpoint is invalid, or too stale
    (downtime > total remaining at checkpoint + ``MAX_RESUME_OVERSHOOT_S``).

    Sequential programs (``sequential=True``, parallel=1): excess downtime
    after running zones finish spills into the queued zones in order, so the
    schedule timeline stays coherent (later zones are shortened / skipped if
    the outage covered their slot). Note: during the outage only the running
    solenoid stays open in hardware — queued valves stay closed — so this is
    a schedule-correctness choice, not a perfect water-volume reconstruction.

    Parallel programs: each running zone loses the full downtime independently;
    queued zones keep full remaining (they never started in hardware).
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
    # Paused cycles (user pause or interlock wait) are not watering — do not
    # consume remaining time for wall-clock downtime while paused.
    if checkpoint.get("paused"):
        delta = 0
    else:
        delta = max(0, int((now_utc - checkpoint_ts).total_seconds()))

    running_src = list(checkpoint.get("running") or [])
    remaining_src = list(checkpoint.get("remaining") or [])

    total_at_cp = sum(max(0, int(i.get("remaining_s", 0))) for i in running_src) + sum(
        max(0, int(i.get("remaining_s", 0))) for i in remaining_src
    )
    # Stale guard still applies to paused checkpoints using wall-clock age so a
    # week-old paused snapshot is not resurrected after a long HA outage.
    age_s = max(0, int((now_utc - checkpoint_ts).total_seconds()))
    if age_s > total_at_cp + MAX_RESUME_OVERSHOOT_S:
        _LOGGER.warning(
            "Irrigation checkpoint stale for %s: age=%ss > remaining=%ss + grace=%ss; discarding",
            checkpoint.get("program_name") or checkpoint.get("program_unique_id"),
            age_s,
            total_at_cp,
            MAX_RESUME_OVERSHOOT_S,
        )
        return None

    leftover = delta
    running_out: list[dict[str, Any]] = []
    if sequential:
        # Consume downtime across running zones in order, then spill to queue.
        for item in running_src:
            rem = max(0, int(item.get("remaining_s", 0)))
            if rem > leftover:
                running_out.append({**item, "remaining_s": rem - leftover})
                leftover = 0
            else:
                leftover -= rem
                _LOGGER.info(
                    "Zone %s finished during HA downtime (%ss of its remaining consumed)",
                    item.get("solenoid"),
                    rem,
                )
    else:
        # Parallel: each running zone loses the full downtime independently.
        leftover = 0
        for item in running_src:
            rem = max(0, int(item.get("remaining_s", 0)))
            new_rem = rem - delta
            if new_rem > 0:
                running_out.append({**item, "remaining_s": new_rem})
            else:
                _LOGGER.info(
                    "Zone %s finished during HA downtime (parallel, had %ss remaining)",
                    item.get("solenoid"),
                    rem,
                )

    remaining_out: list[dict[str, Any]] = []
    for item in remaining_src:
        rem = max(0, int(item.get("remaining_s", 0)))
        if rem <= 0:
            continue
        if sequential and leftover > 0:
            if rem > leftover:
                remaining_out.append({**item, "remaining_s": rem - leftover})
                _LOGGER.info(
                    "Queued zone %s shortened by %ss (sequential downtime spill)",
                    item.get("solenoid"),
                    leftover,
                )
                leftover = 0
            else:
                _LOGGER.info(
                    "Queued zone %s skipped (fully covered by sequential downtime)",
                    item.get("solenoid"),
                )
                leftover -= rem
        else:
            remaining_out.append({**item, "remaining_s": rem})

    if not running_out and not remaining_out:
        return None

    out = dict(checkpoint)
    out["running"] = running_out
    out["remaining"] = remaining_out
    out["downtime_s"] = delta
    # Keep original checkpoint_ts — callers must not re-save this dict as a
    # fresh checkpoint (use build_checkpoint for writes).
    return out


def zone_should_skip_startup_off(
    checkpoint: dict[str, Any] | None,
    solenoid: str | None,
    *,
    adjusted: dict[str, Any] | None = None,
) -> bool:
    """True if this solenoid was watering at checkpoint — keep valve open."""
    if not checkpoint or not solenoid:
        return False
    # Paused = valves should be closed (user pause / interlock wait).
    if checkpoint.get("paused"):
        return False
    data = adjusted if adjusted is not None else apply_downtime(checkpoint)
    if not data:
        return False
    for item in data.get("running") or []:
        if item.get("solenoid") == solenoid and int(item.get("remaining_s", 0)) > 0:
            return True
    return False


_UNSET = object()


async def async_update_program_checkpoint(
    hass: HomeAssistant,
    program_unique_id: str,
    payload: dict[str, Any] | None,
    *,
    interlock_queue: Any = _UNSET,
) -> None:
    """Atomically set or clear one program's checkpoint in the shared Store.

    Optionally refresh ``interlock_queue`` (list of program unique_ids) in the
    same write so multi-program interlock order survives reboot.
    """
    async with checkpoint_lock(hass):
        store = checkpoint_store(hass)
        data = await store.async_load() or {}
        programs = dict(data.get("programs") or {})
        if payload is None:
            programs.pop(program_unique_id, None)
        else:
            programs[program_unique_id] = payload
        data["programs"] = programs
        if interlock_queue is not _UNSET:
            data["interlock_queue"] = list(interlock_queue or [])
        await store.async_save(data)


def find_program_by_unique_id(unique_id: str):
    """Resolve a live IrrigationProgram entity from PROGRAMS by unique_id."""
    from .globals import PROGRAMS

    for prog in PROGRAMS.values():
        if getattr(prog, "_attr_unique_id", None) == unique_id:
            return prog
    return None


async def async_restore_interlock_queue(
    hass: HomeAssistant,
    *,
    force_partial: bool = False,
) -> list:
    """Rebuild QUEUEDPROGRAMS once from the persisted interlock order.

    Safe to call from every program at startup — only marks restored when every
    queue member that still has a loaded config entry is resolvable in
    ``PROGRAMS``. Otherwise returns the current queue without setting the flag
    so a later program can complete the restore (avoids boot-order races).

    ``force_partial=True`` accepts whatever is resolvable now (timeout path).
    """
    from .globals import QUEUEDPROGRAMS

    domain = hass.data.setdefault(DOMAIN, {})
    if domain.get("_interlock_queue_restored"):
        return list(QUEUEDPROGRAMS)

    async with checkpoint_lock(hass):
        store = checkpoint_store(hass)
        data = await store.async_load() or {}
        queue_ids = list(data.get("interlock_queue") or [])

    if not queue_ids:
        domain["_interlock_queue_restored"] = True
        QUEUEDPROGRAMS.clear()
        return []

    # Config-entry keys in hass.data[DOMAIN] (exclude internal _* keys).
    active_entry_ids = {
        key for key in domain if isinstance(key, str) and not key.startswith("_")
    }
    required_ids = [uid for uid in queue_ids if uid in active_entry_ids]

    rebuilt = []
    missing_required = []
    for uid in queue_ids:
        prog = find_program_by_unique_id(uid)
        if prog is not None:
            rebuilt.append(prog)
        elif uid in active_entry_ids:
            missing_required.append(uid)
        else:
            _LOGGER.warning(
                "Interlock queue references missing program unique_id=%s; skipping",
                uid,
            )

    if missing_required and not force_partial:
        _LOGGER.debug(
            "Interlock queue incomplete; waiting for programs: %s",
            ", ".join(missing_required),
        )
        return list(QUEUEDPROGRAMS)

    if missing_required and force_partial:
        _LOGGER.warning(
            "Interlock queue restored partially; unresolved programs: %s",
            ", ".join(missing_required),
        )

    domain["_interlock_queue_restored"] = True
    QUEUEDPROGRAMS.clear()
    QUEUEDPROGRAMS.extend(rebuilt)
    if rebuilt:
        _LOGGER.info(
            "Restored interlock queue (%d programs): %s",
            len(rebuilt),
            ", ".join(getattr(p, "name", str(p)) for p in rebuilt),
        )
    return list(QUEUEDPROGRAMS)


# How long resume waits for sibling programs before accepting a partial queue.
INTERLOCK_RESTORE_WAIT_S = 5.0
INTERLOCK_RESTORE_POLL_S = 0.1


async def async_restore_interlock_queue_ready(hass: HomeAssistant) -> list:
    """Restore interlock queue, waiting briefly for sibling programs to register."""
    domain = hass.data.setdefault(DOMAIN, {})
    if domain.get("_interlock_queue_restored"):
        from .globals import QUEUEDPROGRAMS

        return list(QUEUEDPROGRAMS)

    deadline = asyncio.get_running_loop().time() + INTERLOCK_RESTORE_WAIT_S
    while True:
        queue = await async_restore_interlock_queue(hass)
        if domain.get("_interlock_queue_restored"):
            return queue
        if asyncio.get_running_loop().time() >= deadline:
            return await async_restore_interlock_queue(hass, force_partial=True)
        await asyncio.sleep(INTERLOCK_RESTORE_POLL_S)


def current_interlock_queue_ids() -> list[str]:
    """Unique ids of programs currently in QUEUEDPROGRAMS."""
    from .globals import QUEUEDPROGRAMS

    return [
        p._attr_unique_id  # noqa: SLF001
        for p in QUEUEDPROGRAMS
        if getattr(p, "_attr_unique_id", None)
    ]
