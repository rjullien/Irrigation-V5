"""Tests for deterministic freq_start_date scheduling."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util

from custom_components.irrigationprogram.zone import Zone


@pytest.mark.asyncio
async def test_freq_start_date_alternates_two_programs(monkeypatch):
    """Two programs freq=2 with start dates 1 day apart never collide."""
    tz = ZoneInfo("Europe/Paris")
    # Freeze "today" to 2026-07-24 (local)
    today = datetime(2026, 7, 24, 10, 0, 0, tzinfo=tz)

    monkeypatch.setattr(
        "custom_components.irrigationprogram.zone.dt_util.now",
        lambda: today,
    )
    monkeypatch.setattr(
        "custom_components.irrigationprogram.zone.dt_util.as_local",
        lambda d: d.replace(tzinfo=tz) if d.tzinfo is None else d.astimezone(tz),
    )
    monkeypatch.setattr(
        "custom_components.irrigationprogram.zone.dt_util.start_of_local_day",
        lambda: today.replace(hour=0, minute=0, second=0, microsecond=0),
    )

    async def _zone(start_date: str):
        hass = MagicMock()
        hass.config.time_zone = "Europe/Paris"
        zonedata = MagicMock()
        zonedata.zone = "valve.x"
        zonedata.status = MagicMock(state="off", set_value=AsyncMock())
        zonedata.next_run = MagicMock()
        zonedata.config = MagicMock(entity_id="switch.cfg")
        zonedata.default_run_time = MagicMock(entity_id="sensor.drt")
        zonedata.remaining_time = MagicMock(entity_id="sensor.rem", set_value=AsyncMock())
        zonedata.water = MagicMock(entity_id="number.water", value=30)
        zonedata.last_ran = MagicMock(entity_id="sensor.last")
        zonedata.enabled = MagicMock(entity_id="switch.enable")
        zonedata.eco = False
        zonedata.frequency = MagicMock(state="2", value="2")
        zonedata.watering_type = "time"
        zonedata.adjustment = None
        zonedata.wait = None
        zonedata.repeat = None
        zonedata.type = "valve"
        programdata = MagicMock()
        programdata.latency = 5
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
        programdata.freq_start_date = start_date
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
        # frequency property reads zone or program select
        type(zone).frequency = property(lambda self: "2")
        return zone

    z_a = await _zone("2026-07-23")  # odd offset → run on 23,25,27...
    z_b = await _zone("2026-07-24")  # even → run on 24,26,28...

    first = today.replace(hour=22, minute=10, second=0, microsecond=0)
    last_ran = today - timedelta(days=10)
    midnight = today.replace(hour=0, minute=0, second=0, microsecond=0)

    next_a = z_a.get_numeric_frq(first, midnight, midnight, last_ran)
    next_b = z_b.get_numeric_frq(first, midnight, midnight, last_ran)

    # 24 Jul 2026: B runs today (22:10), A runs tomorrow
    assert next_b.date() == today.date()
    assert next_a.date() == (today + timedelta(days=1)).date()
