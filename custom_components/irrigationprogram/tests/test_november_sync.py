"""Regression coverage for the November upstream ports."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.core import SupportsResponse
from homeassistant.helpers.restore_state import RestoreEntity

from custom_components.irrigationprogram import async_setup
from custom_components.irrigationprogram import globals as g
from custom_components.irrigationprogram.config_flow import IrrigationFlowHandler, OptionsFlowHandler
from custom_components.irrigationprogram.number import Wait
from custom_components.irrigationprogram.program import IrrigationProgram


@pytest.mark.parametrize(
    ("unit", "eco_max", "expected"),
    [("minutes", 1, 10), ("minutes", 10, 10), ("minutes", 90, 90),
     ("seconds", 10, 120), ("seconds", 120, 120), ("seconds", 300, 300)],
)
def test_wait_maximum(unit, eco_max, expected):
    wait = Wait("id", "Garden", "zone", unit, eco_max, "box")
    assert wait.native_max_value == expected
    assert wait.mode == "box"


@pytest.mark.parametrize("state", ["on", "off", None])
async def test_restart_event(state):
    program = IrrigationProgram.__new__(IrrigationProgram)
    program.hass = program._hass = MagicMock()
    program.entity_id = "switch.garden"
    program._program = SimpleNamespace(card_yaml=False)
    program.get_next_interval = MagicMock()
    program.async_get_last_state = AsyncMock(
        return_value=None if state is None else SimpleNamespace(state=state)
    )
    program.async_resume_from_checkpoint = AsyncMock()
    with (
        patch("custom_components.irrigationprogram.program.async_track_point_in_utc_time"),
        patch("custom_components.irrigationprogram.program.async_at_started") as started,
        patch("custom_components.irrigationprogram.program.asyncio.sleep", AsyncMock()),
        patch.object(RestoreEntity, "async_added_to_hass", AsyncMock()),
    ):
        await program.async_added_to_hass()
    started.assert_called_once()
    program.async_resume_from_checkpoint.assert_not_awaited()
    if state == "on":
        program.hass.bus.async_fire.assert_called_once_with(
            "irrigation_event",
            {"action": "Program_running_on_restarted", "program": "switch.garden"},
        )
    else:
        program.hass.bus.async_fire.assert_not_called()


@pytest.mark.parametrize(
    ("paused", "running", "queued", "expected"),
    [(False, False, False, "Stopped"), (False, True, False, "Running"),
     (False, False, True, "Queued"), (True, True, True, "Paused")],
)
def test_current_state(paused, running, queued, expected):
    name = "Garden program"
    equal_name = name.encode().decode()
    assert equal_name == name and equal_name is not name
    program = SimpleNamespace(_paused=paused, _state=running)
    g.PROGRAMS[name] = program
    if queued:
        g.QUEUEDPROGRAMS.append(program)
    for status in ("on", "pending", "eco", "paused", "off", None):
        g.ZONES[f"id.{status}"] = SimpleNamespace(
            _programdata=SimpleNamespace(name=equal_name), _status=status
        )
    g.ZONES["id.missing"] = SimpleNamespace()
    g.ZONES["id.none"] = SimpleNamespace(_programdata=None)
    g.ZONES["id.other"] = SimpleNamespace(_programdata=SimpleNamespace(name="Other"))
    result = g.update_current_state()
    assert result == {name: {"status": expected, "zones": {
        "on": "Running", "pending": "Running", "eco": "Running",
        "paused": "Paused", "off": "Stopped", "None": "Stopped",
    }}}
    assert json.loads(g.current_state_json()) == result


async def test_current_state_service():
    hass = MagicMock()
    hass.data = {}
    hass.services.has_service.return_value = False
    hass.http.async_register_static_paths = AsyncMock()
    with patch("custom_components.irrigationprogram.utils.init_resource", AsyncMock()):
        assert await async_setup(hass, {})
    args, kwargs = hass.services.async_register.call_args
    assert args[:2] == ("irrigationprogram", "get_current_state")
    assert kwargs == {"supports_response": SupportsResponse.ONLY}
    assert await args[2](MagicMock()) == {}
    g.PROGRAMS["Garden"] = SimpleNamespace(_state=True)
    assert await args[2](MagicMock()) == {"Garden": {"status": "Running", "zones": {}}}
    g.PROGRAMS.clear()
    assert await args[2](MagicMock()) == {}
    hass.services.async_register.reset_mock()
    hass.services.has_service.return_value = True
    with patch("custom_components.irrigationprogram.utils.init_resource", AsyncMock()):
        await async_setup(hass, {})
    hass.services.async_register.assert_not_called()


@pytest.mark.parametrize("options", [False, True])
async def test_advanced_eco_max(options):
    if options:
        flow = OptionsFlowHandler(SimpleNamespace(
            data={"eco_max": 90, "low_power_mode": True},
            options={"eco_max": 150}, entry_id="id",
        ))
        flow.async_step_init = AsyncMock()
        flow.get_er = AsyncMock()
        expected = 150
    else:
        flow = IrrigationFlowHandler()
        flow.async_step_menu = AsyncMock()
        expected = 10
    flow.async_show_form = MagicMock()
    await flow.async_step_advanced()
    schema = flow.async_show_form.call_args.kwargs["data_schema"]
    field = next(key for key in schema.schema if key.schema == "eco_max")
    assert field.description["suggested_value"] == expected
    selector = schema.schema[field]
    assert selector(300) == 300
    with pytest.raises(Exception):
        selector("not a number")
    data = {
        "eco_max": 300, "interlock": True, "start_type": "selector",
        "rain_behaviour": "stop", "water_max": 30, "water_step": 1,
        "zone_delay_max": 120, "latency": 5, "start_latency": 60,
        "parallel": 1, "pump_delay": 0, "card_yaml": False, "low_power_mode": True,
        "pause_on_water_source": False, "continue_on_unexpected_state": False,
        "rain_delay_on": False, "min_sec": "minutes",
    }
    await flow.async_step_advanced(data)
    assert flow._data["eco_max"] == 300
    assert flow._data["low_power_mode"] is True
