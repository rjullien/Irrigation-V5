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


@pytest.mark.asyncio
async def test_restore_interlock_queue_once():
    """Queue is rebuilt from Store exactly once across programs."""
    from custom_components.irrigationprogram.const import DOMAIN
    from custom_components.irrigationprogram.globals import PROGRAMS, QUEUEDPROGRAMS
    from custom_components.irrigationprogram.runtime_checkpoint import (
        async_restore_interlock_queue,
    )

    QUEUEDPROGRAMS.clear()
    PROGRAMS.clear()
    hass = MagicMock()
    hass.data = {DOMAIN: {}}

    head = MagicMock()
    head._attr_unique_id = "prog-a"
    head.name = "A"
    tail = MagicMock()
    tail._attr_unique_id = "prog-b"
    tail.name = "B"
    PROGRAMS["A"] = head
    PROGRAMS["B"] = tail

    stored = {"interlock_queue": ["prog-a", "prog-b"], "programs": {}}

    class _Store:
        async def async_load(self):
            return stored

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "custom_components.irrigationprogram.runtime_checkpoint.checkpoint_store",
            lambda _hass: _Store(),
        )
        q1 = await async_restore_interlock_queue(hass)
        q2 = await async_restore_interlock_queue(hass)

    try:
        assert q1 == [head, tail]
        assert q2 == [head, tail]
        assert QUEUEDPROGRAMS == [head, tail]
        assert hass.data[DOMAIN]["_interlock_queue_restored"] is True
    finally:
        QUEUEDPROGRAMS.clear()
        PROGRAMS.clear()
        hass.data[DOMAIN].pop("_interlock_queue_restored", None)


@pytest.mark.asyncio
async def test_ha_stopping_turn_off_keeps_valves_and_checkpoint(monkeypatch):
    """On HA stop: save checkpoint, do not close zone switches / clear Store."""
    from custom_components.irrigationprogram.const import DOMAIN
    from custom_components.irrigationprogram.program import IrrigationProgram

    hass = MagicMock()
    hass.config.time_zone = "UTC"
    hass.loop = MagicMock()
    hass.loop.time = MagicMock(return_value=100.0)
    hass.data = {DOMAIN: {"uid": {}}}
    hass.bus = MagicMock()
    hass.async_create_task = MagicMock()

    program_data = MagicMock()
    program_data.name = "Arrosage"
    program_data.low_power = False
    program_data.interlock = True
    program_data.pause = MagicMock()
    program_data.pause.async_turn_off = AsyncMock()
    program_data.pump = None

    zone_switch = MagicMock()
    zone_switch.state = "on"
    zone_switch.async_turn_off = AsyncMock()
    zone_data = MagicMock()
    zone_data.zone = "valve.z1"
    zone_data.switch = zone_switch
    zone_data.remaining_time = MagicMock(numeric_value=120)
    zone_data.default_run_time = MagicMock(numeric_value=120)

    runtime = MagicMock()
    runtime.program = program_data
    runtime.zone_data = [zone_data]

    with monkeypatch.context() as mp:
        mp.setattr(
            "custom_components.irrigationprogram.program.async_generate_entity_id",
            lambda *a, **k: "switch.arrosage",
        )
        prog = IrrigationProgram(hass, "uid", "arrosage", runtime)

    prog._state = True
    prog._finished = False
    prog._running_zones = [zone_data]
    prog._remaining_zones = []
    prog._ha_stopping = True

    saved = {}

    async def _fake_save(*, force=False):
        saved["force"] = force
        saved["called"] = True

    cleared = {"called": False}

    async def _fake_clear():
        cleared["called"] = True

    prog.async_save_checkpoint = _fake_save
    prog.async_clear_checkpoint = _fake_clear
    prog.async_schedule_update_ha_state = MagicMock()

    await prog.async_turn_off()

    assert saved.get("called") is True
    assert saved.get("force") is True
    assert cleared["called"] is False
    zone_switch.async_turn_off.assert_not_awaited()
    program_data.pause.async_turn_off.assert_not_awaited()
    assert prog._state is False
    assert prog._finished is True


@pytest.mark.asyncio
async def test_time_respects_remaining_override(monkeypatch):
    """Resume path waters only remaining_override seconds."""
    zone = await _make_minimal_zone(monkeypatch)
    zone.async_solenoid_turn_on = AsyncMock()
    zone.handle_state_change = AsyncMock()
    zone.check_switch_state = AsyncMock(return_value=(True, "on"))
    zone.get_status = AsyncMock(return_value="on")
    zone.remaining_time_set = AsyncMock()
    zone._latency = 1
    zone._water_adjust_prior = 1
    zone._scheduled = False
    zone._remaining_override = 2
    zone._zonedata.water.value = 9999  # unused when override set

    from datetime import datetime, timezone

    t0 = datetime(2026, 7, 23, 20, 0, 0, tzinfo=timezone.utc)
    clock = {"t": t0}

    def _now():
        return clock["t"]

    async def _sleep(_seconds):
        clock["t"] = clock["t"] + timedelta(seconds=1)

    monkeypatch.setattr(
        "custom_components.irrigationprogram.zone.dt_util.now", _now
    )
    monkeypatch.setattr(
        "custom_components.irrigationprogram.zone.asyncio.sleep",
        _sleep,
    )

    await zone.time(1.0, 0, 1, last=False)
    assert zone._remaining_override is None  # consumed
    assert zone._resume_segment_s == 2
    zone.async_solenoid_turn_on.assert_awaited()
    # ~2s window; clock should have advanced a few ticks, not 9999*60
    assert (clock["t"] - t0).total_seconds() < 30


async def _make_minimal_zone(monkeypatch):
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
    zonedata.remaining_time.set_value = AsyncMock()
    zonedata.water = MagicMock(entity_id="number.water", value=30)
    zonedata.last_ran = MagicMock(entity_id="sensor.last")
    zonedata.enabled = MagicMock(entity_id="switch.enable")
    zonedata.eco = False
    zonedata.frequency = None
    zonedata.watering_type = "time"
    zonedata.adjustment = None
    zonedata.wait = None
    zonedata.repeat = None
    zonedata.type = "valve"
    programdata = MagicMock()
    programdata.latency = 1
    programdata.pump = None
    programdata.pump_delay = 0
    programdata.continue_on_unexpected_state = False
    programdata.flow_sensor = None
    programdata.water_source = None
    programdata.rain_delay = None
    programdata.enabled = MagicMock(is_on=True)
    programdata.pause = MagicMock(is_on=False)
    programdata.unique_id = "uid"
    programdata.min_sec = "minutes"
    programdata.switch = MagicMock(entity_id="switch.prog")
    monkeypatch.setattr(Zone, "async_added_to_hass", AsyncMock())
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
    return zone
