"""Tests for mid-cycle resume after Home Assistant reboot."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.util import dt as dt_util

from custom_components.irrigationprogram.const import CONST_PENDING
from custom_components.irrigationprogram.runtime_checkpoint import (
    MAX_RESUME_OVERSHOOT_S,
    apply_downtime,
    build_checkpoint,
    zone_should_skip_startup_off,
)
from custom_components.irrigationprogram.zone import Zone


def _cp(*, running, remaining, age_s: int, now=None):
    now = now or dt_util.utcnow()
    cp = build_checkpoint(
        program_unique_id="uid",
        program_name="Arrosage",
        scheduled=True,
        start_time=now - timedelta(minutes=10),
        paused=False,
        running=running,
        remaining=remaining,
    )
    cp["checkpoint_ts"] = (now - timedelta(seconds=age_s)).isoformat()
    return cp, now


def test_apply_downtime_subtracts_elapsed_from_running():
    cp, now = _cp(
        running=[{"solenoid": "valve.z2", "remaining_s": 600}],
        remaining=[{"solenoid": "valve.z3", "remaining_s": 1800}],
        age_s=90,
    )
    adjusted = apply_downtime(cp, now=now, sequential=True)
    assert adjusted is not None
    assert adjusted["downtime_s"] == 90
    assert adjusted["running"][0]["remaining_s"] == 510
    # No spill into queue — running still absorbing downtime
    assert adjusted["remaining"][0]["remaining_s"] == 1800


def test_apply_downtime_sequential_spills_into_queue():
    """Running finishes mid-outage → leftover downtime shortens next queued zone."""
    cp, now = _cp(
        running=[{"solenoid": "valve.z2", "remaining_s": 30}],
        remaining=[{"solenoid": "valve.z3", "remaining_s": 600}],
        age_s=90,
    )
    adjusted = apply_downtime(cp, now=now, sequential=True)
    assert adjusted is not None
    assert adjusted["running"] == []
    # 90 - 30 = 60 spilled into z3
    assert adjusted["remaining"][0]["remaining_s"] == 540


def test_apply_downtime_parallel_does_not_spill_into_queue():
    cp, now = _cp(
        running=[{"solenoid": "valve.z2", "remaining_s": 30}],
        remaining=[{"solenoid": "valve.z3", "remaining_s": 600}],
        age_s=90,
    )
    adjusted = apply_downtime(cp, now=now, sequential=False)
    assert adjusted is not None
    assert adjusted["running"] == []
    assert adjusted["remaining"][0]["remaining_s"] == 600


def test_apply_downtime_rejects_stale_checkpoint():
    # total remaining 100s, grace 300 → stale if age > 400
    cp, now = _cp(
        running=[{"solenoid": "valve.z2", "remaining_s": 50}],
        remaining=[{"solenoid": "valve.z3", "remaining_s": 50}],
        age_s=100 + MAX_RESUME_OVERSHOOT_S + 1,
    )
    assert apply_downtime(cp, now=now) is None


def test_apply_downtime_drops_when_everything_consumed():
    cp, now = _cp(
        running=[{"solenoid": "valve.z2", "remaining_s": 30}],
        remaining=[],
        age_s=60,
    )
    assert apply_downtime(cp, now=now) is None


def test_zone_should_skip_startup_off():
    cp, now = _cp(
        running=[{"solenoid": "valve.arroseur_eyguians_valve_2", "remaining_s": 400}],
        remaining=[],
        age_s=0,
    )
    adjusted = apply_downtime(cp, now=now)
    assert (
        zone_should_skip_startup_off(
            cp, "valve.arroseur_eyguians_valve_2", adjusted=adjusted
        )
        is True
    )
    assert (
        zone_should_skip_startup_off(
            cp, "valve.arroseur_eyguians_valve_3", adjusted=adjusted
        )
        is False
    )


@pytest.mark.asyncio
async def test_zone_set_resume_state():
    """async_set_resume_state is the public resume API (no private attr poking)."""
    hass = MagicMock()
    hass.config.time_zone = "UTC"

    status = MagicMock()
    status.state = "off"
    status.set_value = AsyncMock()
    next_run = MagicMock()
    next_run.state = dt_util.as_local(dt_util.now()) - timedelta(minutes=5)

    zonedata = MagicMock()
    zonedata.zone = "valve.test"
    zonedata.status = status
    zonedata.next_run = next_run
    zonedata.config = MagicMock(entity_id="switch.cfg")
    zonedata.default_run_time = MagicMock(entity_id="sensor.drt")
    zonedata.remaining_time = MagicMock(entity_id="sensor.rem")
    zonedata.water = MagicMock(entity_id="number.water")
    zonedata.last_ran = MagicMock(entity_id="sensor.last")
    zonedata.enabled = MagicMock(entity_id="switch.enable")
    zonedata.eco = False
    zonedata.frequency = None
    zonedata.watering_type = "fixed"
    zonedata.adjustment = None

    programdata = MagicMock()
    programdata.latency = 5
    programdata.pump = None
    programdata.continue_on_unexpected_state = False
    programdata.flow_sensor = None
    programdata.water_source = None
    programdata.rain_delay = None
    programdata.enabled = MagicMock(is_on=True)
    programdata.pause = MagicMock(is_on=False)
    programdata.unique_id = "uid"

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Zone, "async_added_to_hass", AsyncMock())
        zone = Zone(
            unique_id="uid",
            pname="prog",
            zname="z1",
            zfriendly_name="Zone 1",
            zonedata=zonedata,
            programdata=programdata,
        )
    zone.hass = hass
    zone.status_sensor_set = AsyncMock()
    zone.remaining_time_set = AsyncMock()

    await zone.async_set_resume_state(123, status=CONST_PENDING)
    assert zone._remaining_time == 123
    assert zone._status == CONST_PENDING
    zone.status_sensor_set.assert_awaited()
    zone.remaining_time_set.assert_awaited()


def test_current_interlock_queue_ids_multi_program():
    from custom_components.irrigationprogram.globals import QUEUEDPROGRAMS
    from custom_components.irrigationprogram.runtime_checkpoint import (
        current_interlock_queue_ids,
    )

    QUEUEDPROGRAMS.clear()
    a = MagicMock()
    a._attr_unique_id = "prog-a"
    b = MagicMock()
    b._attr_unique_id = "prog-b"
    QUEUEDPROGRAMS.extend([a, b])
    try:
        assert current_interlock_queue_ids() == ["prog-a", "prog-b"]
    finally:
        QUEUEDPROGRAMS.clear()
