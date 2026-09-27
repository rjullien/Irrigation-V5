"""Tests for August pause-on-start / offline-zone ports (upstream V2026.08.01)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.config_entries import ConfigEntry

from custom_components.irrigationprogram import (
    IrrigationData,
    IrrigationProgram,
    IrrigationZoneData,
)
from custom_components.irrigationprogram.const import CONST_OFF, CONST_ON
from custom_components.irrigationprogram.program import IrrigationProgram as ProgramEntity
from custom_components.irrigationprogram.switch import async_setup_entry
from custom_components.irrigationprogram.zone import Zone


def _bare_program(*, water_source="binary_sensor.well", water_source_pause=True, ws_state=CONST_OFF):
    """IrrigationProgram shell for run_monitor_zones pause-on-start path."""
    p = ProgramEntity.__new__(ProgramEntity)
    p._stop = False
    p._paused = False
    p._running_zones = []
    p._remaining_zones = []
    p._program_remaining = 0
    p._resume_overrides = {}
    p.hass = MagicMock()
    ws = MagicMock()
    ws.state = ws_state
    p.hass.states.get.return_value = ws
    p._program = MagicMock()
    p._program.water_source = water_source
    p._program.water_source_pause = water_source_pause
    p._program.pause = MagicMock()
    p._program.pause.async_turn_on = AsyncMock()
    p._program.parallel = 1
    p.calculate_program_remaining = AsyncMock(return_value=0)
    p.async_save_checkpoint = AsyncMock()
    p.remaining_time_set = AsyncMock()
    return p


@pytest.mark.asyncio
async def test_run_monitor_zones_pauses_on_start_when_water_source_off():
    """water_source_pause + source OFF → pause.async_turn_on; skip checkpoint while paused."""
    p = _bare_program(ws_state=CONST_OFF)

    async def _turn_on():
        p._paused = True

    p._program.pause.async_turn_on.side_effect = _turn_on

    with patch("custom_components.irrigationprogram.program.asyncio.sleep", AsyncMock()):
        result = await p.run_monitor_zones()

    p._program.pause.async_turn_on.assert_awaited_once()
    p.async_save_checkpoint.assert_not_awaited()
    assert result == []


@pytest.mark.asyncio
async def test_run_monitor_zones_no_pause_when_water_source_on():
    """water_source_pause but source ON → do not pause."""
    p = _bare_program(ws_state=CONST_ON)

    with patch("custom_components.irrigationprogram.program.asyncio.sleep", AsyncMock()):
        await p.run_monitor_zones()

    p._program.pause.async_turn_on.assert_not_awaited()
    p.async_save_checkpoint.assert_awaited()


@pytest.mark.asyncio
async def test_run_monitor_zones_no_pause_when_option_disabled():
    """Without water_source_pause, off source does not auto-pause."""
    p = _bare_program(water_source_pause=False, ws_state=CONST_OFF)

    with patch("custom_components.irrigationprogram.program.asyncio.sleep", AsyncMock()):
        await p.run_monitor_zones()

    p._program.pause.async_turn_on.assert_not_awaited()


@pytest.mark.asyncio
async def test_switch_setup_offline_zone_uses_entity_id_as_friendly_name():
    """When the zone entity is offline (states.get → None), setup still builds Zone."""
    hass = MagicMock()
    hass.data = {}
    hass.config = MagicMock()
    hass.config.time_zone = "UTC"
    hass.states.get.return_value = None  # offline

    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = "test_entry_id"
    entry.runtime_data = IrrigationData(
        program=IrrigationProgram(
            name="Test Program",
            switch=None,
            modified="",
            pause=None,
            rain_delay_on=False,
            pump=None,
            flow_sensor=None,
            water_source=None,
            rain_delay=None,
            rain_delay_days=None,
            unique_id="test_id",
            config=None,
            start_time=None,
            remaining_time=None,
            default_run_time=None,
            multitime=None,
            sunrise_offset=None,
            sunset_offset=None,
            start_type="selector",
            frequency=None,
            freq_options=[],
            freq=False,
            freq_start_date="",
            repeat=False,
            repeats=None,
            rain_behaviour="stop",
            enabled=None,
            controller_type="Generic",
            inter_zone_delay=None,
            interlock="strict",
            zone_count=1,
            min_sec="minutes",
            water_max=30,
            water_step=1,
            zone_delay_max=120,
            parallel=1,
            pump_delay=1,
            card_yaml=False,
        ),
        zone_data=[
            IrrigationZoneData(
                zone="switch.offline_zone",
                switch=None,
                type="switch",
                name="offline_zone",
                config=None,
                eco=False,
                watering_type="fixed",
                water=None,
                wait=None,
                repeat=None,
                frequency=None,
                freq=False,
                ignore_sensors=None,
                enabled=None,
                status=None,
                next_run=None,
                last_ran=None,
                remaining_time=None,
                default_run_time=None,
                rain_sensor=None,
                adjustment=None,
                flow_rate=None,
            )
        ],
    )

    async_add_entities = AsyncMock()
    await async_setup_entry(hass, entry, async_add_entities)

    switches = async_add_entities.call_args_list[0][0][0]
    zone_entity = next(s for s in switches if isinstance(s, Zone))
    # Offline fallback: entity_id used as zone_name placeholder
    assert zone_entity.translation_placeholders == {"zone_name": "switch.offline_zone"}
