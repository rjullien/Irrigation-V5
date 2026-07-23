"""Tests for stale zone-status cache vs live solenoid (valve) state.

See docs/design-zone-status-valve-cache.md.

Run from repo root:
    PYTHONPATH=. python -m pytest \\
        custom_components/irrigationprogram/tests/test_zone_status_cache.py -v
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components.irrigationprogram.const import (
    CONST_OFF,
    CONST_UNAVAILABLE,
    CONST_UNKNOWN,
)
from custom_components.irrigationprogram.program import IrrigationProgram
from custom_components.irrigationprogram.zone import Zone

# Absolute path so the test is CWD-independent
_PROGRAM_PY = (
    Path(__file__).resolve().parents[1] / "program.py"
)


def _make_zone(*, status_state: str, next_run=None) -> Zone:
    """Minimal Zone with mocked helpers (no full HA)."""
    hass = MagicMock()
    hass.config.time_zone = "UTC"

    status = MagicMock()
    status.state = status_state
    status.set_value = AsyncMock()

    # Past local datetime — stable under dt_util.as_local comparisons
    next_run_sensor = MagicMock()
    if next_run is None:
        next_run = dt_util.as_local(dt_util.now()) - timedelta(minutes=5)
    next_run_sensor.state = next_run

    zonedata = MagicMock()
    zonedata.zone = "valve.test_zone"
    zonedata.status = status
    zonedata.next_run = next_run_sensor
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

    with patch.object(Zone, "async_added_to_hass", new=AsyncMock()):
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


@pytest.mark.asyncio
async def test_should_run_rechecks_live_when_cache_unavailable():
    """Cached unavailable + live off → allow scheduled run and refresh cache."""
    zone = _make_zone(status_state=CONST_UNAVAILABLE)
    zone.get_status = AsyncMock(return_value=CONST_OFF)

    assert await zone.should_run_ex(scheduled=True) is True
    zone.get_status.assert_awaited_once()
    zone.status_sensor_set.assert_awaited_once()
    assert zone._status_sensor == CONST_OFF


@pytest.mark.asyncio
async def test_should_run_rechecks_live_when_cache_unknown():
    """Cached unknown (Tuya recovery) + live off → allow and refresh."""
    zone = _make_zone(status_state=CONST_UNKNOWN)
    zone.get_status = AsyncMock(return_value=CONST_OFF)

    assert await zone.should_run_ex(scheduled=True) is True
    zone.get_status.assert_awaited_once()
    assert zone._status_sensor == CONST_OFF


@pytest.mark.asyncio
async def test_should_run_still_skips_when_live_unavailable():
    """Cached unavailable + live unavailable → skip."""
    zone = _make_zone(status_state=CONST_UNAVAILABLE)
    zone.get_status = AsyncMock(return_value=CONST_UNAVAILABLE)

    assert await zone.should_run_ex(scheduled=True) is False
    zone.status_sensor_set.assert_not_awaited()


@pytest.mark.asyncio
async def test_should_run_off_cache_does_not_call_get_status():
    """Healthy cache: no extra live probe."""
    zone = _make_zone(status_state=CONST_OFF)
    zone.get_status = AsyncMock(return_value=CONST_OFF)

    assert await zone.should_run_ex(scheduled=True) is True
    zone.get_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_up_entity_monitoring_registers_solenoid():
    """Behavioral: set_up_entity_monitoring tracks zone.zone (solenoid entity_id)."""
    hass = MagicMock()
    hass.config.time_zone = "UTC"
    hass.states.get = MagicMock(return_value=MagicMock())  # entities "available"

    program_data = MagicMock()
    program_data.name = "Test"
    program_data.start_time = MagicMock(entity_id="text.start")
    program_data.sunrise_offset = None
    program_data.sunset_offset = None
    program_data.enabled = MagicMock(entity_id="switch.enable_prog")
    program_data.rain_delay = None
    program_data.frequency = None
    program_data.inter_zone_delay = None
    program_data.repeats = None
    program_data.water_source = None
    program_data.water_source_pause = False
    program_data.pause = MagicMock(entity_id="switch.pause")
    program_data.low_power = False
    program_data.card_yaml = False
    program_data.pump = None

    zone_data = MagicMock()
    zone_data.zone = "valve.arroseur_eyguians_valve_1"  # solenoid entity_id
    zone_data.switch = MagicMock(entity_id="switch.zone1")
    zone_data.enabled = MagicMock(entity_id="switch.enable_z1")
    zone_data.frequency = None
    zone_data.rain_sensor = None
    zone_data.ignore_sensors = None
    zone_data.adjustment = None
    zone_data.water = None
    zone_data.repeat = None
    zone_data.wait = None

    runtime = MagicMock()
    runtime.program = program_data
    runtime.zone_data = [zone_data]

    tracked: list[str] = []

    def _track(hass_arg, entity_ids, callback):
        tracked.extend(entity_ids)
        return MagicMock()

    with (
        patch(
            "custom_components.irrigationprogram.program.async_track_state_change_event",
            side_effect=_track,
        ),
        patch(
            "custom_components.irrigationprogram.program.async_generate_entity_id",
            return_value="switch.test_program",
        ),
        patch.object(IrrigationProgram, "generate_card", MagicMock()),
    ):
        prog = IrrigationProgram(hass, "uid", "test_program", runtime)
        prog.hass = hass
        await prog.set_up_entity_monitoring()

    assert "valve.arroseur_eyguians_valve_1" in tracked
    assert "switch.zone1" in tracked
    assert _PROGRAM_PY.is_file()  # path helper sanity (CWD-independent)
