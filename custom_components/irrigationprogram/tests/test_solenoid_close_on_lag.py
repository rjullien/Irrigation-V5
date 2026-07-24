"""Regression: laggy controllers must still receive close after unconfirmed open.

Incident 2026-07-24 (Eyguians): latency=5 + Tuya lag caused each zone to abort
~5s after open_valve. async_solenoid_turn_off saw state still ``closed`` and
skipped close_valve — delayed opens then arrived together (multi-valve flood).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_CLOSE_VALVE,
    SERVICE_OPEN_VALVE,
)
from homeassistant.util import dt as dt_util

from custom_components.irrigationprogram.const import CONST_VALVE
from custom_components.irrigationprogram.zone import Zone


async def _make_valve_zone(monkeypatch, *, latency: int = 5):
    hass = MagicMock()
    hass.config.time_zone = "UTC"
    hass.services.async_call = AsyncMock()
    status = MagicMock()
    status.state = "off"
    status.set_value = AsyncMock()
    next_run = MagicMock()
    next_run.state = dt_util.as_local(dt_util.now()) - timedelta(minutes=5)
    zonedata = MagicMock()
    zonedata.zone = "valve.test_1"
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
    programdata.latency = latency
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
    programdata.controller_type = "generic"
    programdata.switch = MagicMock(
        entity_id="switch.prog",
        remaining_zones=[],
        running_zones=[],
    )
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
    zone.remaining_time_set = AsyncMock()
    zone.async_schedule_update_ha_state = MagicMock()
    zone._attr_name = "Zone 1"
    zone._attr_has_entity_name = False
    return zone


@pytest.mark.asyncio
async def test_turn_off_sends_close_when_open_commanded_but_still_closed(
    monkeypatch,
):
    """Even if HA still shows closed, close after we commanded open."""
    zone = await _make_valve_zone(monkeypatch)
    zone.check_switch_state = AsyncMock(return_value=(False, "closed"))

    await zone.async_solenoid_turn_on()
    assert zone._solenoid_commanded_open is True
    zone.hass.services.async_call.assert_any_await(
        CONST_VALVE, SERVICE_OPEN_VALVE, {ATTR_ENTITY_ID: "valve.test_1"}
    )

    zone.hass.services.async_call.reset_mock()
    await zone.async_solenoid_turn_off()
    zone.hass.services.async_call.assert_awaited_once_with(
        CONST_VALVE, SERVICE_CLOSE_VALVE, {ATTR_ENTITY_ID: "valve.test_1"}
    )


@pytest.mark.asyncio
async def test_turn_off_skips_close_when_never_commanded_open(monkeypatch):
    zone = await _make_valve_zone(monkeypatch)
    zone.check_switch_state = AsyncMock(return_value=(False, "closed"))
    zone._solenoid_commanded_open = False

    await zone.async_solenoid_turn_off()
    zone.hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_natural_off_settles_after_unconfirmed_open(monkeypatch):
    """Abort path must settle long enough to catch delayed opens."""
    zone = await _make_valve_zone(monkeypatch, latency=5)
    zone._solenoid_commanded_open = True
    zone._solenoid_open_confirmed = False
    zone._state = "on"
    zone._status = "on"
    zone.check_switch_state = AsyncMock(return_value=(False, "closed"))

    clock = {"t": datetime(2026, 7, 24, 19, 30, 0)}

    async def _sleep(seconds):
        clock["t"] = clock["t"] + timedelta(seconds=seconds)

    import custom_components.irrigationprogram.zone as zone_mod

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["t"]

    monkeypatch.setattr(zone_mod, "datetime", FakeDateTime)
    monkeypatch.setattr(zone_mod.asyncio, "sleep", _sleep)

    await zone.async_turn_off_zone_natural()

    close_calls = [
        c
        for c in zone.hass.services.async_call.await_args_list
        if c.args[:2] == (CONST_VALVE, SERVICE_CLOSE_VALVE)
    ]
    assert close_calls, "expected close_valve during settle"
    assert (clock["t"] - datetime(2026, 7, 24, 19, 30, 0)).total_seconds() >= 29
    assert zone._solenoid_commanded_open is False
