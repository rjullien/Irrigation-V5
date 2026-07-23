"""Tests for mid-cycle resume after Home Assistant reboot."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.util import dt as dt_util

from custom_components.irrigationprogram.runtime_checkpoint import (
    apply_downtime,
    build_checkpoint,
    zone_should_skip_startup_off,
)


def test_apply_downtime_subtracts_elapsed():
    now = dt_util.utcnow()
    cp = build_checkpoint(
        program_unique_id="uid",
        program_name="Arrosage",
        scheduled=True,
        start_time=now - timedelta(minutes=10),
        paused=False,
        running=[{"solenoid": "valve.z2", "remaining_s": 600}],
        remaining=[{"solenoid": "valve.z3", "remaining_s": 1800}],
    )
    # Pretend checkpoint was written 90s ago
    cp["checkpoint_ts"] = (now - timedelta(seconds=90)).isoformat()

    adjusted = apply_downtime(cp, now=now)
    assert adjusted is not None
    assert adjusted["downtime_s"] == 90
    assert adjusted["running"][0]["remaining_s"] == 510
    # Queued zones keep full remaining (not yet watering)
    assert adjusted["remaining"][0]["remaining_s"] == 1800


def test_apply_downtime_drops_finished_running_zone():
    now = dt_util.utcnow()
    cp = build_checkpoint(
        program_unique_id="uid",
        program_name="Arrosage",
        scheduled=False,
        start_time=now,
        paused=False,
        running=[{"solenoid": "valve.z2", "remaining_s": 30}],
        remaining=[],
    )
    cp["checkpoint_ts"] = (now - timedelta(seconds=60)).isoformat()

    assert apply_downtime(cp, now=now) is None


def test_apply_downtime_keeps_queue_when_running_finished():
    now = dt_util.utcnow()
    cp = build_checkpoint(
        program_unique_id="uid",
        program_name="Arrosage",
        scheduled=True,
        start_time=now,
        paused=False,
        running=[{"solenoid": "valve.z2", "remaining_s": 20}],
        remaining=[{"solenoid": "valve.z3", "remaining_s": 120}],
    )
    cp["checkpoint_ts"] = (now - timedelta(seconds=60)).isoformat()

    adjusted = apply_downtime(cp, now=now)
    assert adjusted is not None
    assert adjusted["running"] == []
    assert adjusted["remaining"][0]["solenoid"] == "valve.z3"


def test_zone_should_skip_startup_off():
    now = dt_util.utcnow()
    cp = build_checkpoint(
        program_unique_id="uid",
        program_name="Arrosage",
        scheduled=True,
        start_time=now,
        paused=False,
        running=[{"solenoid": "valve.arroseur_eyguians_valve_2", "remaining_s": 400}],
        remaining=[],
    )
    cp["checkpoint_ts"] = now.isoformat()

    assert zone_should_skip_startup_off(cp, "valve.arroseur_eyguians_valve_2") is True
    assert zone_should_skip_startup_off(cp, "valve.arroseur_eyguians_valve_3") is False
    assert zone_should_skip_startup_off(None, "valve.arroseur_eyguians_valve_2") is False
