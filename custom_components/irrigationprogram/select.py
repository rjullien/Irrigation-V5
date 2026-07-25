import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import slugify

from . import IrrigationData, IrrigationProgram
from .const import ATTR_FREQ_START_DATE
from .rotation import (
    CRENEAU_OPTIONS,
    creneau_to_start_date,
    start_date_to_creneau,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Initialize config entry. form config flow."""
    data: IrrigationData = config_entry.runtime_data
    unique_id = config_entry.entry_id
    p: IrrigationProgram = config_entry.runtime_data.program
    freq_options = p.freq_options

    entities: list[SelectEntity] = []
    if p.freq:
        sensor = Frequency(unique_id, p.name, None, freq_options)
        entities.append(sensor)
        config_entry.runtime_data.program.frequency = sensor

        creneau = Creneau(hass, config_entry, unique_id, p)
        entities.append(creneau)
        config_entry.runtime_data.program.creneau = creneau

    zones = data.zone_data
    for i, zone in enumerate(zones):
        # if zone freq selected or program level not selected
        if zone.freq or not p.freq:
            sensor = Frequency(unique_id, p.name, zone.name, freq_options)
            entities.append(sensor)
            config_entry.runtime_data.zone_data[i].frequency = sensor
    async_add_entities(entities)


class Frequency(SelectEntity, RestoreEntity):
    _attr_translation_key = "frequency"
    _attr_has_entity_name = True
    _unrecorded_attributes = frozenset({MATCH_ALL})

    def __init__(self, unique_id, pname, name, freq_options):
        if name:
            self._attr_unique_id = slugify(f"{unique_id}_{name}_frequency")
            self._attr_attribution = f"Irrigation Controller: {pname}, {name}"
        else:
            self._attr_unique_id = slugify(f"{unique_id}_frequency")
            self._attr_attribution = f"Irrigation Controller: {pname}"

        self._current_option = None
        self._extended_options = ()
        self._options = freq_options
        if freq_options is None:
            self._options = ["1"]

    async def async_added_to_hass(self):
        """HA has started."""
        last_state = await self.async_get_last_state()
        if last_state is None:
            self._current_option = self._options[0]
        else:
            self._current_option = last_state.state

    @property
    def options(self):
        return self._options

    @property
    def current_option(self):
        return self._current_option

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        self._current_option = option
        self.async_write_ha_state()


class Creneau(SelectEntity, RestoreEntity):
    """Rotation slot: 1er / 2e / … jour du cycle (maps to freq_start_date)."""

    _attr_translation_key = "creneau"
    _attr_has_entity_name = True
    _unrecorded_attributes = frozenset({MATCH_ALL})

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        unique_id: str,
        program: IrrigationProgram,
    ) -> None:
        self.hass = hass
        self._config_entry = config_entry
        self._program = program
        self._attr_unique_id = slugify(f"{unique_id}_creneau")
        self._attr_attribution = f"Irrigation Controller: {program.name}"
        self._options = list(CRENEAU_OPTIONS)
        self._current_option = start_date_to_creneau(program.freq_start_date or "")

    async def async_added_to_hass(self) -> None:
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in self._options:
            self._current_option = last_state.state
            # Restored créneau owns the schedule (epoch mapping)
            self._program.freq_start_date = creneau_to_start_date(self._current_option)
        elif self._program.freq_start_date:
            # Keep legacy start date; créneau is display-only until user changes it
            self._current_option = start_date_to_creneau(self._program.freq_start_date)
        else:
            self._current_option = "1"
            self._program.freq_start_date = creneau_to_start_date("1")

    @property
    def options(self) -> list[str]:
        return self._options

    @property
    def current_option(self) -> str | None:
        return self._current_option

    async def async_select_option(self, option: str) -> None:
        if option not in self._options:
            _LOGGER.warning("Invalid créneau option %s", option)
            return
        self._current_option = option
        start = creneau_to_start_date(option)
        self._program.freq_start_date = start

        # Persist without relying on reload for scheduling (runtime already updated)
        entry = self._config_entry
        base: dict[str, Any] = dict(entry.options) if entry.options else dict(entry.data)
        if base.get(ATTR_FREQ_START_DATE) != start:
            base[ATTR_FREQ_START_DATE] = start
            if entry.options:
                self.hass.config_entries.async_update_entry(entry, options=base)
            else:
                self.hass.config_entries.async_update_entry(entry, data=base)
        _LOGGER.info(
            "Créneau %s → freq_start_date=%s (program %s)",
            option,
            start,
            self._program.name,
        )
        self.async_write_ha_state()
