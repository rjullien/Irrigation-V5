"""Test configuration for Irrigation Program component."""

import pytest

# Pytest configuration
pytest_plugins = ["pytest_asyncio"]


@pytest.fixture(autouse=True)
def mock_globals():
    """Reset mutable globals in place (do not replace the list/dict objects).

    ``program.py`` binds ``from .globals import QUEUEDPROGRAMS`` at import time.
    Replacing the module attribute with a new ``[]`` leaves that binding on the
    old list and breaks interlock tests.
    """
    from custom_components.irrigationprogram import globals as g

    g.ZONES.clear()
    g.PROGRAMS.clear()
    g.QUEUEDPROGRAMS.clear()
    g.RUNNINGPROGRAM = False
    yield
    g.ZONES.clear()
    g.PROGRAMS.clear()
    g.QUEUEDPROGRAMS.clear()
    g.RUNNINGPROGRAM = False


@pytest.fixture
def mock_home_assistant():
    """Create a mock HomeAssistant instance for testing."""
    from unittest.mock import MagicMock

    hass = MagicMock()
    hass.data = {}
    hass.config_entries = MagicMock()
    hass.states = MagicMock()
    hass.async_available = MagicMock(return_value=True)
    hass.config = MagicMock()
    hass.config.time_zone = "UTC"
    return hass


@pytest.fixture
def mock_config_entry(mock_home_assistant):
    """Create a mock ConfigEntry for testing."""
    from unittest.mock import MagicMock
    from homeassistant.config_entries import ConfigEntry

    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = "test_entry_id"
    entry.title = "Test Program"
    entry.data = {}
    entry.options = {
        "device_type": "Generic",
        "start_type": "selector",
        "rain_delay": False,
        "rain_behaviour": "stop",
        "interlock": "strict",
        "min_sec": "minutes",
        "latency": 5,
        "start_latency": 60,
        "pause_water_source": False,
        "zones": [
            {
                "zone": "switch.zone1",
                "eco": False,
                "watering_type": "fixed",
                "freq": False,
                "rain_sensor": None,
                "water_adjust": None,
            }
        ],
    }
    entry.runtime_data = None
    return entry
