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


def test_apply_downtime_parallel_subtracts_independently():
    """Each parallel running zone loses the full downtime (not serial leftover)."""
    cp, now = _cp(
        running=[
            {"solenoid": "valve.z1", "remaining_s": 600},
            {"solenoid": "valve.z2", "remaining_s": 600},
        ],
        remaining=[],
        age_s=90,
    )
    adjusted = apply_downtime(cp, now=now, sequential=False)
    assert adjusted is not None
    assert len(adjusted["running"]) == 2
    assert adjusted["running"][0]["remaining_s"] == 510
    assert adjusted["running"][1]["remaining_s"] == 510


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


def test_apply_downtime_paused_does_not_consume_remaining():
    """Paused checkpoints keep full remaining (pause ≠ watering)."""
    cp, now = _cp(
        running=[{"solenoid": "valve.z2", "remaining_s": 600}],
        remaining=[{"solenoid": "valve.z3", "remaining_s": 1800}],
        age_s=900,
    )
    cp["paused"] = True
    adjusted = apply_downtime(cp, now=now, sequential=True)
    assert adjusted is not None
    assert adjusted["downtime_s"] == 0
    assert adjusted["running"][0]["remaining_s"] == 600
    assert adjusted["remaining"][0]["remaining_s"] == 1800


def test_zone_should_not_skip_startup_off_when_paused():
    cp, now = _cp(
        running=[{"solenoid": "valve.z2", "remaining_s": 400}],
        remaining=[],
        age_s=0,
    )
    cp["paused"] = True
    adjusted = apply_downtime(cp, now=now)
    assert (
        zone_should_skip_startup_off(cp, "valve.z2", adjusted=adjusted) is False
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
async def test_resume_preserves_user_pause(monkeypatch):
    """User-paused mid-cycle must stay paused after reboot resume (N1)."""
    from custom_components.irrigationprogram.const import (
        ATTR_RUNTIME_CHECKPOINT,
        DOMAIN,
    )
    from custom_components.irrigationprogram.globals import QUEUEDPROGRAMS
    from custom_components.irrigationprogram.program import IrrigationProgram

    now = dt_util.utcnow()
    cp = build_checkpoint(
        program_unique_id="uid",
        program_name="Arrosage",
        scheduled=True,
        start_time=now,
        paused=True,  # user had paused before reboot
        running=[{"solenoid": "valve.z1", "remaining_s": 300}],
        remaining=[],
    )
    cp["checkpoint_ts"] = now.isoformat()

    hass = MagicMock()
    hass.config.time_zone = "UTC"
    hass.loop = MagicMock()
    hass.loop.time = MagicMock(return_value=100.0)
    hass.data = {
        DOMAIN: {
            "uid": {ATTR_RUNTIME_CHECKPOINT: cp},
            "_interlock_queue_restored": True,
        }
    }
    hass.bus = MagicMock()
    hass.async_create_task = MagicMock()

    pause = MagicMock()
    pause.async_turn_on = AsyncMock()
    pause.async_turn_off = AsyncMock()
    program_data = MagicMock()
    program_data.name = "Arrosage"
    program_data.low_power = False
    program_data.interlock = False  # not waiting on queue
    program_data.pause = pause
    program_data.pump = None
    program_data.parallel = 1

    zone_switch = MagicMock()
    zone_switch.async_set_resume_state = AsyncMock()
    zone_switch.async_solenoid_turn_off = AsyncMock()
    zone_data = MagicMock()
    zone_data.zone = "valve.z1"
    zone_data.switch = zone_switch
    zone_data.remaining_time = MagicMock(numeric_value=300)
    zone_data.default_run_time = MagicMock(numeric_value=300)
    zone_data.status = MagicMock(state="pending")

    runtime = MagicMock()
    runtime.program = program_data
    runtime.zone_data = [zone_data]

    QUEUEDPROGRAMS.clear()
    with monkeypatch.context() as mp:
        mp.setattr(
            "custom_components.irrigationprogram.program.async_generate_entity_id",
            lambda *a, **k: "switch.arrosage",
        )
        mp.setattr(
            "custom_components.irrigationprogram.program.async_restore_interlock_queue_ready",
            AsyncMock(return_value=[]),
        )
        prog = IrrigationProgram(hass, "uid", "arrosage", runtime)
        prog.hass = hass
        prog.async_schedule_update_ha_state = MagicMock()
        prog.remaining_time_set = AsyncMock()
        # degree_of_parallel reads program.parallel
        program_data.inter_zone_delay = None

        ok = await prog.async_resume_from_checkpoint()

    try:
        assert ok is True
        assert prog._paused is True
        assert prog._program_remaining > 0  # seeded — runner must not exit early
        pause.async_turn_on.assert_awaited()
        pause.async_turn_off.assert_not_awaited()
    finally:
        QUEUEDPROGRAMS.clear()


@pytest.mark.asyncio
async def test_paused_resume_runner_does_not_turn_off(monkeypatch):
    """Paused resume runner must spin on pause, not call async_turn_off."""
    from custom_components.irrigationprogram.const import (
        ATTR_RUNTIME_CHECKPOINT,
        DOMAIN,
    )
    from custom_components.irrigationprogram.globals import QUEUEDPROGRAMS
    from custom_components.irrigationprogram.program import IrrigationProgram

    now = dt_util.utcnow()
    cp = build_checkpoint(
        program_unique_id="uid",
        program_name="Arrosage",
        scheduled=True,
        start_time=now,
        paused=True,
        running=[{"solenoid": "valve.z1", "remaining_s": 300}],
        remaining=[],
    )
    cp["checkpoint_ts"] = now.isoformat()

    hass = MagicMock()
    hass.config.time_zone = "UTC"
    hass.loop = MagicMock()
    hass.loop.time = MagicMock(return_value=100.0)
    hass.data = {
        DOMAIN: {
            "uid": {ATTR_RUNTIME_CHECKPOINT: cp},
            "_interlock_queue_restored": True,
        }
    }
    hass.bus = MagicMock()
    created = []

    def _capture_task(coro):
        created.append(coro)
        return MagicMock()

    hass.async_create_task = _capture_task

    pause = MagicMock()
    pause.async_turn_on = AsyncMock()
    pause.async_turn_off = AsyncMock()
    program_data = MagicMock()
    program_data.name = "Arrosage"
    program_data.low_power = False
    program_data.interlock = False
    program_data.pause = pause
    program_data.pump = None
    program_data.parallel = 1
    program_data.inter_zone_delay = None

    zone_switch = MagicMock()
    zone_switch.async_set_resume_state = AsyncMock()
    zone_switch.async_solenoid_turn_off = AsyncMock()
    zone_data = MagicMock()
    zone_data.zone = "valve.z1"
    zone_data.switch = zone_switch
    zone_data.remaining_time = MagicMock(numeric_value=300)
    zone_data.default_run_time = MagicMock(numeric_value=300)
    zone_data.status = MagicMock(state="pending")

    runtime = MagicMock()
    runtime.program = program_data
    runtime.zone_data = [zone_data]

    QUEUEDPROGRAMS.clear()
    with monkeypatch.context() as mp:
        mp.setattr(
            "custom_components.irrigationprogram.program.async_generate_entity_id",
            lambda *a, **k: "switch.arrosage",
        )
        mp.setattr(
            "custom_components.irrigationprogram.program.async_restore_interlock_queue_ready",
            AsyncMock(return_value=[]),
        )
        prog = IrrigationProgram(hass, "uid", "arrosage", runtime)
        prog.hass = hass
        prog.async_schedule_update_ha_state = MagicMock()
        prog.remaining_time_set = AsyncMock()
        prog.async_turn_off = AsyncMock()

        ok = await prog.async_resume_from_checkpoint()
        assert ok is True
        assert created, "resume runner was not scheduled"
        assert prog._program_remaining >= 300

        spins = {"n": 0}

        async def _sleep(_seconds):
            spins["n"] += 1
            if spins["n"] >= 3:
                prog._stop = True

        mp.setattr(
            "custom_components.irrigationprogram.program.asyncio.sleep",
            _sleep,
        )
        await created[0]

    try:
        assert spins["n"] >= 3
        prog.async_turn_off.assert_not_awaited()
    finally:
        QUEUEDPROGRAMS.clear()


@pytest.mark.asyncio
async def test_force_save_clears_stale_when_no_zones(monkeypatch):
    """HA stop with empty zone lists must clear Store, not re-save old CP."""
    from custom_components.irrigationprogram.const import (
        ATTR_RUNTIME_CHECKPOINT,
        DOMAIN,
    )
    from custom_components.irrigationprogram.program import IrrigationProgram

    hass = MagicMock()
    hass.config.time_zone = "UTC"
    hass.loop = MagicMock()
    hass.loop.time = MagicMock(return_value=100.0)
    hass.data = {
        DOMAIN: {
            "uid": {
                ATTR_RUNTIME_CHECKPOINT: {
                    "version": 1,
                    "running": [{"solenoid": "valve.old", "remaining_s": 10}],
                }
            }
        }
    }
    hass.bus = MagicMock()

    program_data = MagicMock()
    program_data.name = "Arrosage"
    program_data.low_power = False
    program_data.interlock = False
    program_data.pause = MagicMock()
    program_data.pump = None

    runtime = MagicMock()
    runtime.program = program_data
    runtime.zone_data = []

    update = AsyncMock()
    with monkeypatch.context() as mp:
        mp.setattr(
            "custom_components.irrigationprogram.program.async_generate_entity_id",
            lambda *a, **k: "switch.arrosage",
        )
        mp.setattr(
            "custom_components.irrigationprogram.program.async_update_program_checkpoint",
            update,
        )
        prog = IrrigationProgram(hass, "uid", "arrosage", runtime)
        prog._state = False
        prog._running_zones = []
        prog._remaining_zones = []
        await prog.async_save_checkpoint(force=True)

    update.assert_awaited()
    assert update.await_args.args[2] is None  # payload cleared
    assert hass.data[DOMAIN]["uid"][ATTR_RUNTIME_CHECKPOINT] is None


@pytest.mark.asyncio
async def test_restore_interlock_waits_for_missing_program():
    """Do not mark queue restored while a loaded entry's program is missing."""
    from custom_components.irrigationprogram.const import DOMAIN
    from custom_components.irrigationprogram.globals import PROGRAMS, QUEUEDPROGRAMS
    from custom_components.irrigationprogram.runtime_checkpoint import (
        async_restore_interlock_queue,
    )

    QUEUEDPROGRAMS.clear()
    PROGRAMS.clear()
    hass = MagicMock()
    # Both entries loaded, but only head registered in PROGRAMS yet
    hass.data = {DOMAIN: {"prog-a": {}, "prog-b": {}}}

    head = MagicMock()
    head._attr_unique_id = "prog-a"
    head.name = "A"
    PROGRAMS["A"] = head

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
        assert hass.data[DOMAIN].get("_interlock_queue_restored") is not True
        assert q1 == []

        tail = MagicMock()
        tail._attr_unique_id = "prog-b"
        tail.name = "B"
        PROGRAMS["B"] = tail

        q2 = await async_restore_interlock_queue(hass)
        assert hass.data[DOMAIN]["_interlock_queue_restored"] is True
        assert q2 == [head, tail]

    QUEUEDPROGRAMS.clear()
    PROGRAMS.clear()
    hass.data[DOMAIN].pop("_interlock_queue_restored", None)


@pytest.mark.asyncio
async def test_reconcile_closes_orphan_solenoid(monkeypatch):
    """Valve kept open at T0 but finished by T1 must be closed at resume."""
    from custom_components.irrigationprogram.const import DOMAIN
    from custom_components.irrigationprogram.program import IrrigationProgram

    hass = MagicMock()
    hass.config.time_zone = "UTC"
    hass.data = {DOMAIN: {"uid": {}}}
    hass.bus = MagicMock()

    program_data = MagicMock()
    program_data.name = "Arrosage"
    program_data.low_power = False
    program_data.interlock = False
    program_data.pause = MagicMock()
    program_data.pump = None

    z1 = MagicMock()
    z1.zone = "valve.z1"
    z1.switch = MagicMock()
    z1.switch.async_solenoid_turn_off = AsyncMock()
    z2 = MagicMock()
    z2.zone = "valve.z2"
    z2.switch = MagicMock()
    z2.switch.async_solenoid_turn_off = AsyncMock()

    runtime = MagicMock()
    runtime.program = program_data
    runtime.zone_data = [z1, z2]

    with monkeypatch.context() as mp:
        mp.setattr(
            "custom_components.irrigationprogram.program.async_generate_entity_id",
            lambda *a, **k: "switch.arrosage",
        )
        prog = IrrigationProgram(hass, "uid", "arrosage", runtime)

    raw = {
        "running": [
            {"solenoid": "valve.z1", "remaining_s": 10},
            {"solenoid": "valve.z2", "remaining_s": 100},
        ]
    }
    adjusted = {
        "running": [{"solenoid": "valve.z2", "remaining_s": 40}],
        "remaining": [],
    }
    await prog.async_reconcile_solenoids(raw, adjusted)
    z1.switch.async_solenoid_turn_off.assert_awaited_once()
    z2.switch.async_solenoid_turn_off.assert_not_awaited()

    await prog.async_reconcile_solenoids(raw, None)
    assert z1.switch.async_solenoid_turn_off.await_count >= 2
    z2.switch.async_solenoid_turn_off.assert_awaited()


@pytest.mark.asyncio
async def test_failed_resume_hands_off_interlock(monkeypatch):
    """Discarded head checkpoint must unpause the next queued program."""
    from custom_components.irrigationprogram.const import DOMAIN
    from custom_components.irrigationprogram.globals import QUEUEDPROGRAMS
    from custom_components.irrigationprogram.program import IrrigationProgram

    hass = MagicMock()
    hass.config.time_zone = "UTC"
    hass.data = {DOMAIN: {"uid-a": {}, "uid-b": {}}}
    hass.bus = MagicMock()

    program_data = MagicMock()
    program_data.name = "A"
    program_data.low_power = False
    program_data.interlock = True
    program_data.pause = MagicMock()
    program_data.pause.async_turn_off = AsyncMock()
    program_data.pump = None

    runtime = MagicMock()
    runtime.program = program_data
    runtime.zone_data = []

    next_prog = MagicMock()
    next_prog.name = "B"
    next_prog._attr_unique_id = "uid-b"
    next_prog.pause_switch = MagicMock()
    next_prog.pause_switch.async_turn_off = AsyncMock()

    QUEUEDPROGRAMS.clear()
    with monkeypatch.context() as mp:
        mp.setattr(
            "custom_components.irrigationprogram.program.async_generate_entity_id",
            lambda *a, **k: "switch.a",
        )
        mp.setattr(
            "custom_components.irrigationprogram.program.async_update_program_checkpoint",
            AsyncMock(),
        )
        prog = IrrigationProgram(hass, "uid-a", "a", runtime)
        QUEUEDPROGRAMS.extend([prog, next_prog])
        mp.setattr(
            "custom_components.irrigationprogram.program.async_restore_interlock_queue_ready",
            AsyncMock(return_value=list(QUEUEDPROGRAMS)),
        )
        await prog.async_hand_off_interlock_after_failed_resume()

    try:
        # Entity.__eq__ is unreliable — assert by identity.
        assert not any(p is prog for p in QUEUEDPROGRAMS)
        assert QUEUEDPROGRAMS == [next_prog]
        next_prog.pause_switch.async_turn_off.assert_awaited_once()
    finally:
        QUEUEDPROGRAMS.clear()


@pytest.mark.asyncio
async def test_calculate_remaining_uses_resume_overrides(monkeypatch):
    """Queued resume zones must not inflate program remaining via default_run_time."""
    from custom_components.irrigationprogram.const import DOMAIN
    from custom_components.irrigationprogram.program import IrrigationProgram

    hass = MagicMock()
    hass.config.time_zone = "UTC"
    hass.data = {DOMAIN: {"uid": {}}}
    hass.bus = MagicMock()

    program_data = MagicMock()
    program_data.name = "Arrosage"
    program_data.low_power = False
    program_data.interlock = False
    program_data.pause = MagicMock()
    program_data.pump = None
    program_data.parallel = 1
    program_data.inter_zone_delay = 0

    zone = MagicMock()
    zone.zone = "valve.z1"
    zone.switch = MagicMock()
    zone.switch.default_run_time = 9999  # full config — must NOT win
    zone.remaining_time = MagicMock(numeric_value=0)

    runtime = MagicMock()
    runtime.program = program_data
    runtime.zone_data = [zone]

    with monkeypatch.context() as mp:
        mp.setattr(
            "custom_components.irrigationprogram.program.async_generate_entity_id",
            lambda *a, **k: "switch.arrosage",
        )
        prog = IrrigationProgram(hass, "uid", "arrosage", runtime)
        prog.remaining_time_set = AsyncMock()
        prog.async_schedule_update_ha_state = MagicMock()
        prog._resume_overrides = {"valve.z1": 120}

        total = await prog.calculate_program_remaining([], [zone], 0, False)

    assert total == 120


@pytest.mark.asyncio
async def test_pause_program_force_saves_checkpoint(monkeypatch):
    """Entering/leaving pause must flush checkpoint (paused flag)."""
    from custom_components.irrigationprogram.const import DOMAIN
    from custom_components.irrigationprogram.program import IrrigationProgram

    hass = MagicMock()
    hass.config.time_zone = "UTC"
    hass.data = {DOMAIN: {"uid": {}}}
    hass.bus = MagicMock()

    pause = MagicMock()
    pause.is_on = True
    pause.async_turn_off = AsyncMock()
    program_data = MagicMock()
    program_data.name = "Arrosage"
    program_data.low_power = False
    program_data.interlock = False
    program_data.pause = pause
    program_data.pump = None

    zone_switch = MagicMock()
    zone_switch.async_toggle = AsyncMock()
    zone = MagicMock()
    zone.switch = zone_switch

    runtime = MagicMock()
    runtime.program = program_data
    runtime.zone_data = [zone]

    with monkeypatch.context() as mp:
        mp.setattr(
            "custom_components.irrigationprogram.program.async_generate_entity_id",
            lambda *a, **k: "switch.arrosage",
        )
        mp.setattr(
            "custom_components.irrigationprogram.program.asyncio.sleep",
            AsyncMock(),
        )
        prog = IrrigationProgram(hass, "uid", "arrosage", runtime)
        prog._state = True
        prog._zones = [zone]
        prog.async_save_checkpoint = AsyncMock()

        event = MagicMock()
        await prog.pause_program(event)

    assert prog._paused is True
    prog.async_save_checkpoint.assert_awaited_once_with(force=True)


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
