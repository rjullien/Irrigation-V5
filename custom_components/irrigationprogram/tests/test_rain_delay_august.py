"""Tests for August rain-delay next-run correction (upstream V2026.08.01)."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.util import dt as dt_util

from custom_components.irrigationprogram.const import CONST_ON
from custom_components.irrigationprogram.zone import Zone


def _make_zone(hass, *, rain_on=True, delay_days=3, delay_time=None, freq="2"):
    """Minimal Zone with rain-delay wiring for get_numeric_frq / calc paths."""
    programdata = SimpleNamespace(
        rain_delay=SimpleNamespace(state=CONST_ON if rain_on else "off"),
        rain_delay_days=SimpleNamespace(state=delay_days),
        delay_time=delay_time,
        freq_start_date="",
        frequency=None,
        freq_options=["1", "2", "3"],
        pump_delay=0,
        enabled=SimpleNamespace(is_on=True),
        pause=SimpleNamespace(is_on=False),
        water_source_pause=False,
    )
    zonedata = SimpleNamespace(
        frequency=SimpleNamespace(current_option=freq, state=freq),
        repeat=None,
        water=None,
        wait=None,
        eco=False,
        ignore_sensors=None,
        enabled=SimpleNamespace(state="on"),
        status=SimpleNamespace(state="off"),
        next_run=None,
        last_ran=None,
        remaining_time=None,
        default_run_time=None,
        config=None,
        zone="switch.zone1",
        name="zone1",
        rain_sensor=None,
        adjustment=None,
    )
    zone = Zone.__new__(Zone)
    zone.hass = hass
    zone._programdata = programdata
    zone._zonedata = zonedata
    zone._name = "zone1"
    return zone


def test_numeric_frq_rain_delay_uses_delay_time_not_last_updated(mock_home_assistant):
    """With rain delay on, epoch-less path returns last_ran-based next run (no last_updated)."""
    activated = dt_util.as_local(dt_util.now()) - timedelta(days=1)
    delay_time = SimpleNamespace(state=activated.isoformat())
    zone = _make_zone(mock_home_assistant, delay_time=delay_time, freq="2")

    first = dt_util.as_local(dt_util.now()).replace(
        hour=8, minute=0, second=0, microsecond=0
    )
    today_midnight = dt_util.start_of_local_day()
    # last_ran recent → without delay would be last_ran + 2 days
    last_ran = first - timedelta(days=1)
    last_ran_midnight = last_ran.replace(hour=0, minute=0, second=0, microsecond=0)

    result = zone.get_numeric_frq(first, last_ran_midnight, today_midnight, last_ran)
    assert isinstance(result, datetime)
    # Legacy path must NOT clamp with rain_delay.last_updated (removed upstream).
    assert result == last_ran + timedelta(days=2)


def test_numeric_frq_freq_start_date_clamps_to_delay_time(mock_home_assistant):
    """Créneau/freq_start_date path still respects delay_time + days."""
    activated = dt_util.as_local(dt_util.now()) - timedelta(hours=1)
    delay_days = 5
    delay_time = SimpleNamespace(state=activated.isoformat())
    zone = _make_zone(mock_home_assistant, delay_time=delay_time, delay_days=delay_days, freq="2")
    zone._programdata.freq_start_date = (
        dt_util.start_of_local_day() - timedelta(days=1)
    ).date().isoformat()

    first = dt_util.as_local(dt_util.now()).replace(
        hour=8, minute=0, second=0, microsecond=0
    )
    today_midnight = dt_util.start_of_local_day()
    last_ran = first - timedelta(days=10)

    result = zone.get_numeric_frq(first, today_midnight, today_midnight, last_ran)
    delay_until = activated + timedelta(days=delay_days)
    assert result >= delay_until


def test_odd_even_no_longer_mutates_from_last_updated(mock_home_assistant):
    """Odd/Even helpers ignore rain_delay.last_updated (August cleanup)."""
    zone = _make_zone(mock_home_assistant)
    # Fake a last_updated that would have shifted start_date previously
    zone._programdata.rain_delay.last_updated = dt_util.as_local(dt_util.now()) + timedelta(
        days=10
    )
    start = dt_util.as_local(dt_util.now()).replace(
        hour=8, minute=0, second=0, microsecond=0
    )
    even = zone.get_next_even_day(start)
    odd = zone.get_next_odd_day(start)
    assert even.day % 2 == 0
    assert odd.day % 2 == 1
    # Must not jump ~10 days ahead solely due to last_updated
    assert even < start + timedelta(days=3)
    assert odd < start + timedelta(days=3)
