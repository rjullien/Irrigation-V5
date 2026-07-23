"""Tests for stale zone-status cache vs live solenoid (valve) state.

See docs/design-zone-status-valve-cache.md.

Run:
    PYTHONPATH=. python -m pytest \\
        custom_components/irrigationprogram/tests/test_zone_status_cache.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.irrigationprogram.const import (
    CONST_OFF,
    CONST_UNAVAILABLE,
)
from custom_components.irrigationprogram.zone import Zone


def _make_zone(*, status_state: str, next_run=None) -> Zone:
    """Minimal Zone with mocked helpers (no full HA)."""
    hass = MagicMock()
    hass.config.time_zone = "UTC"

    status = MagicMock()
    status.state = status_state
    status.set_value = AsyncMock()

    next_run_sensor = MagicMock()
    if next_run is None:
        next_run = datetime.now(timezone.utc) - timedelta(minutes=1)
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


def test_program_monitor_includes_solenoid_entity():
    """Solenoid entity_id must be on the next-run monitor list."""
    import ast
    from pathlib import Path

    src = Path("custom_components/irrigationprogram/program.py").read_text()
    # Structural guard: monitor_append(zone.zone, "solenoid", ...) present
    assert 'monitor_append(zone.zone, "solenoid"' in src or \
           "monitor_append(zone.zone, 'solenoid'" in src
    tree = ast.parse(src)
    assert tree  # file parses
