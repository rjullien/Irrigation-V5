"""Switch entity definition."""

import asyncio
from datetime import UTC, datetime, timedelta
import logging
from zoneinfo import ZoneInfo

from homeassistant.components.persistent_notification import async_create, async_dismiss
from homeassistant.components.switch import ENTITY_ID_FORMAT, SwitchEntity
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, MATCH_ALL
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.entity import async_generate_entity_id
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_utc_time,
    async_track_state_change_event,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util, slugify

from . import (
    IrrigationData,
    IrrigationProgram as ProgramData,
    IrrigationZoneData as ZoneData,
    async_queue_program as queue_program,
)
from .const import (
    ATTR_DEFAULT_RUN_TIME,
    ATTR_DELAY,
    ATTR_IRRIGATION_ON,
    ATTR_PAUSE,
    ATTR_REMAINING,
    ATTR_RUNTIME_CHECKPOINT,
    ATTR_RUN_FREQ,
    ATTR_SHOW_CONFIG,
    ATTR_START,
    CONST_CHECKPOINT_INTERVAL,
    CONST_NEXT_RUN_DEBOUNCE,
    CONST_NEXT_RUN_DEBOUNCE_LOW_POWER,
    CONST_OFF,
    CONST_ON,
    CONST_PENDING,
    DOMAIN,
    TIME_STR_FORMAT,
)
from .globals import PROGRAMS, QUEUEDPROGRAMS
from .pump import PumpClass
from .runtime_checkpoint import (
    apply_downtime,
    async_restore_interlock_queue_ready,
    async_update_program_checkpoint,
    build_checkpoint,
    checkpoint_store,
    current_interlock_queue_ids,
)

_LOGGER = logging.getLogger(__name__)


class IrrigationProgram(SwitchEntity, RestoreEntity):
    """Representation of an Irrigation program."""

    _attr_has_entity_name = True
    _attr_attribution = "Irrigation Program"
    _unrecorded_attributes = frozenset({MATCH_ALL})
    _attr_translation_key = "program"
    _attr_should_poll = False

    def __init__(
        self, hass: HomeAssistant, unique_id, device_id, runtime_data: IrrigationData
    ) -> None:
        """Initialize a Irrigation program."""
        self._attr_unique_id = unique_id
        self._hass = hass

        self._name = runtime_data.program.name
        self._program: ProgramData = runtime_data.program
        self._zones: list[ZoneData] = runtime_data.zone_data

        self.entity_id = async_generate_entity_id(
            ENTITY_ID_FORMAT, device_id, hass=hass
        )
        self._scheduled = False
        self._state = False
        self._finished = True
        self._paused = False
        self._stop = False

        self._program_remaining = 0

        self._unsub_point_in_time = None
        self._unsub_start = None
        self._unsub_monitor = None
        self._unsub_pause = None
        self._unsub_pause_water = None
        self._unsub_next_run_debounce = None
        self._unsub_ha_stop = None
        self._start_time = dt_util.as_local(dt_util.now())
        self._last_checkpoint_monotonic = 0.0
        self._resume_overrides: dict[str, int] = {}
        self._ha_stopping = False

        self._pumps = []
        self._run_zones = []  # list of zones to run
        # per-program queues: module level globals corrupted each other
        # when two programs ran concurrently (interlock disabled)
        self._remaining_zones: list = []
        self._running_zones: list = []
        self._running_zone: ZoneData | None = (
            None  # []  # list of currently running zones
        )
        self._extra_attrs = {}
        self._default_run_time = 0
        self._localtimezone = ZoneInfo(self._hass.config.time_zone)
        self._low_power = bool(getattr(self._program, "low_power", False))

        PROGRAMS.update({self._name: self})

    def generate_card(self):
        """Create card config yaml."""
        if self._low_power:
            # skip the (string-heavy) manual card generation on low power hosts
            return
        modified = None
        if self._program.modified:
            # dt_util.parse_datetime handles ISO strings and returns aware objects if tz info is present
            if type(self._program.modified) is datetime:
                modified: datetime | None = self._program.modified
            else:
                modified: datetime | None = dt_util.parse_datetime(
                    self._program.modified
                )

        # only generate the card if recently modified
        now = dt_util.now()
        if modified:
            modified_local = dt_util.as_local(modified)
            if now - modified_local > timedelta(seconds=30):
                return

        def add_entity(object, conditions, simple=False):
            if object:
                data = ""
                data += "- type: conditional" + chr(10)
                data += "  conditions:" + chr(10)
                for condition in conditions:
                    x = 1
                    for k, v in condition.items():
                        if x == 1:
                            data += "  - "
                            x = 2
                        else:
                            data += "    "
                        data += k + ": " + v + chr(10)
                data += "  row:" + chr(10)
                if simple:
                    data += "    type: " + "simple-entity" + chr(10)
                data += "    entity: " + object.entity_id + chr(10)

                return data
            return ""

        def add_entity_2(object, conditions, simple=False):
            if object:
                data = ""
                data += "- type: conditional" + chr(10)
                data += "  conditions:" + chr(10)
                for condition in conditions:
                    x = 1
                    for k, v in condition.items():
                        if x == 1:
                            data += "  - "
                            x = 2
                        else:
                            data += "    "
                        data += k + ": " + v + chr(10)
                data += "  row:" + chr(10)
                if simple:
                    data += "    type: " + "simple-entity" + chr(10)
                data += "    entity: " + object + chr(10)

                return data
            return ""

        card: str = "### Copy into manual card" + chr(10)
        card += "```" + chr(10)

        card += "state_color: true" + chr(10)
        card += "show_header_toggle: false" + chr(10)

        card += "type: entities" + chr(10)
        card += "entities:" + chr(10)
        card += "- type: conditional" + chr(10)
        card += "  conditions:" + chr(10)
        card += "  - entity: " + self.entity_id + chr(10)
        card += "    state: off" + chr(10)
        card += "  row:" + chr(10)
        card += "    type: buttons" + chr(10)
        card += "    entities: " + chr(10)
        card += "    - entity: " + self.entity_id + chr(10)
        card += "      show_name: true" + chr(10)
        card += "    - entity: " + self._program.config.entity_id + chr(10)
        card += "      show_name: true" + chr(10)
        card += "- type: conditional" + chr(10)
        card += "  conditions:" + chr(10)
        card += "  - entity: " + self.entity_id + chr(10)
        card += "    state: on" + chr(10)
        card += "  row:" + chr(10)
        card += "    type: buttons" + chr(10)
        card += "    entities: " + chr(10)
        card += "    - entity: " + self.entity_id + chr(10)
        card += "      show_name: true" + chr(10)
        card += "    - entity: " + self._program.config.entity_id + chr(10)
        card += "      show_name: true" + chr(10)
        card += "    - entity: " + self._program.pause.entity_id + chr(10)
        card += "      show_name: true" + chr(10)

        condition = [
            {"entity": self.entity_id, "state_not": "on"},
            {"entity": self._program.config.entity_id, "state_not": "on"},
            {"entity": self._program.enabled.entity_id, "state": "on"},
        ]
        card += add_entity(self._program.start_time, condition, True)
        card += add_entity(self._program.default_run_time, condition, True)
        condition = [
            {"entity": self.entity_id, "state_not": "on"},
            {"entity": self._program.config.entity_id, "state_not": "on"},
            {"entity": self._program.enabled.entity_id, "state_not": "on"},
        ]
        card += add_entity(self._program.enabled, condition, True)
        card += add_entity(self._program.default_run_time, condition, True)

        condition = [{"entity": self._program.config.entity_id, "state": "on"}]
        if self._program.sunrise_offset or self._program.sunset_offset:
            card += add_entity(self._program.start_time, condition, True)
            card += add_entity(self._program.default_run_time, condition, True)
        else:
            card += add_entity(self._program.start_time, condition)
            card += add_entity(self._program.default_run_time, condition)
        card += add_entity(self._program.sunrise_offset, condition)
        card += add_entity(self._program.sunset_offset, condition)

        condition = [{"entity": self.entity_id, "state": "on"}]
        card += add_entity(self._program.remaining_time, condition)

        condition = [{"entity": self._program.config.entity_id, "state": "on"}]
        card += add_entity(self._program.enabled, condition)
        card += add_entity(self._program.frequency, condition)
        card += add_entity(self._program.inter_zone_delay, condition)
        card += add_entity(self._program.repeats, condition)
        if self._program.rain_delay_on:
            card += add_entity(self._program.rain_delay, condition)
            card += add_entity(self._program.rain_delay_days, condition)

        # now process the zones
        for zone in self._zones:
            card += "- type: section" + chr(10)
            card += "  label: ''" + chr(10)
            card += "- type: conditional" + chr(10)
            card += "  conditions:" + chr(10)
            card += "  - entity: " + zone.switch.entity_id + chr(10)
            card += "    state: off" + chr(10)
            card += "  row:" + chr(10)
            card += "    type: buttons" + chr(10)
            card += "    entities: " + chr(10)
            card += "    - entity: " + zone.switch.entity_id + chr(10)
            card += "      show_name: true" + chr(10)
            card += "      show_icon: true" + chr(10)
            card += "      tap_action: " + chr(10)
            card += "        action: call-service" + chr(10)
            card += "        service: switch.toggle" + chr(10)
            card += "        service_data:" + chr(10)
            card += "          entity_id: " + zone.switch.entity_id + chr(10)
            card += "    - entity: " + zone.config.entity_id + chr(10)
            card += "      show_name: true" + chr(10)

            card += "- type: conditional" + chr(10)
            card += "  conditions:" + chr(10)
            card += "  - entity: " + zone.switch.entity_id + chr(10)
            card += "    state_not: off" + chr(10)
            card += "  row:" + chr(10)
            card += "    type: buttons" + chr(10)
            card += "    entities: " + chr(10)
            card += "    - entity: " + zone.switch.entity_id + chr(10)
            card += "      show_name: true" + chr(10)
            card += "      show_icon: true" + chr(10)
            card += "      tap_action: " + chr(10)
            card += "        action: call-service" + chr(10)
            card += "        service: switch.toggle" + chr(10)
            card += "        service_data:" + chr(10)
            card += "          entity_id: " + zone.switch.entity_id + chr(10)
            card += "    - entity: " + zone.config.entity_id + chr(10)
            card += "      show_name: true" + chr(10)
            card += "    - entity: " + zone.status.entity_id + chr(10)
            card += "      show_name: false" + chr(10)

            condition = [{"entity": zone.status.entity_id, "state": '["off"]'}]
            card += add_entity(zone.next_run, condition)

            condition = [
                {
                    "entity": zone.status.entity_id,
                    "state_not": '["off", "on", "pending", "eco"]',
                }
            ]
            card += add_entity(zone.status, condition)

            condition = [
                {"entity": zone.status.entity_id, "state": '["on","eco","pending"]'}
            ]
            card += add_entity(zone.remaining_time, condition)

            condition = [
                {
                    "entity": zone.status.entity_id,
                    "state_not": '["on", "eco", "pending"]',
                },
                {"entity": zone.config.entity_id, "state": "on"},
            ]
            card += add_entity(zone.last_ran, condition)

            condition = [{"entity": zone.config.entity_id, "state": "on"}]
            card += add_entity(zone.enabled, condition)
            card += add_entity(zone.frequency, condition)
            card += add_entity(zone.default_run_time, condition)
            card += add_entity(zone.water, condition)
            card += add_entity(zone.wait, condition)
            card += add_entity(zone.repeat, condition)
            card += add_entity_2(self._program.flow_sensor, condition)
            card += add_entity_2(zone.adjustment, condition)
            card += add_entity_2(zone.rain_sensor, condition)
            card += add_entity_2(self._program.water_source, condition)
            card += add_entity(zone.ignore_sensors, condition)

        card += "```" + chr(10)

        # create the persistent notification
        if self._program.card_yaml is True:
            async_dismiss(self.hass, "irrigation_card")
            async_create(
                self._hass,
                message=card,
                title="Irrigation Controller",
                notification_id="irrigation_card",
            )

    async def async_will_remove_from_hass(self) -> None:
        """Cancel next update."""

        await self.async_turn_off()

        if self._unsub_point_in_time:
            self._unsub_point_in_time()
            self._unsub_point_in_time = None
        if self._unsub_start:
            self._unsub_start()
            self._unsub_start = None
        # stop monitoring
        if self._unsub_monitor:
            self._unsub_monitor()
            self._unsub_monitor = None
        if self._unsub_pause:
            self._unsub_pause()
            self._unsub_pause = None
        if self._unsub_pause_water:
            self._unsub_pause_water()
            self._unsub_pause_water = None
        if self._unsub_next_run_debounce:
            self._unsub_next_run_debounce()
            self._unsub_next_run_debounce = None
        if self._unsub_ha_stop:
            self._unsub_ha_stop()
            self._unsub_ha_stop = None

    def get_next_interval(self):
        """Next time an update should occur."""
        now = datetime.now(UTC)
        timestamp = datetime.timestamp(datetime.now())
        interval = 60
        delta = interval - (timestamp % interval)
        return now + timedelta(seconds=delta)

    def format_attr(self, part_a, part_b):
        """Format attribute names."""
        return slugify(f"{part_a}_{part_b}")

    @callback
    def point_in_time_listener(self, time_date):
        """Get the latest time and check if irrigation should start."""
        self._unsub_point_in_time = async_track_point_in_utc_time(
            self._hass, self.point_in_time_listener, self.get_next_interval()
        )

        time = datetime.now(self._localtimezone).strftime(TIME_STR_FORMAT)
        self._start_time = dt_util.as_local(dt_util.now())
        string_times = self.start_time_value
        if string_times:
            string_times = (
                string_times.replace(" ", "")
                .replace("\n", "")
                .replace("'", "")
                .replace('"', "")
                .strip("[]'")
                .split(",")
            )

            if (
                self._state is False
                and time in string_times
                and self.irrigation_on_value == "on"
            ):
                self._running_zone = None
                self._scheduled = True
                self.hass.async_create_task(self.async_turn_on())
            self.async_write_ha_state()

    async def default_run_time_set(self):
        """Update the default run time sensor (direct, no service call)."""
        await self._program.default_run_time.set_value(self._default_run_time)

    async def remaining_time_set(self):
        """Update the remaining time sensor (direct, no service call)."""
        await self._program.remaining_time.set_value(self._program_remaining)

    async def update_next_run(self, entity=None, old_status=None, new_status=None):
        """Update the next run callback."""

        d = timedelta(0)
        if self._program.sunrise_offset:
            # 1. Get the state string safely
            sun_state = self._hass.states.get("sensor.sun_next_rising")

            if sun_state and sun_state.state not in ("unknown", "unavailable"):
                # 2. Parse using dt_util (handles ISO strings better than strptime)
                sunrise = dt_util.parse_datetime(sun_state.state)

                # 3. Convert string offset to a number (float or int)
                try:
                    if self._program.sunrise_offset.state:
                        offset_minutes = float(self._program.sunrise_offset.state)
                        d = timedelta(minutes=offset_minutes)

                    # 4. Apply offset and convert to local time
                    # guard the whole block: if parsing failed, sunrise is
                    # None and adjusted_sunrise was previously unbound
                    if sunrise:
                        adjusted_sunrise = dt_util.as_local(sunrise + d)

                        # 5. Extract the time component without seconds/micros
                        target_time = adjusted_sunrise.replace(
                            second=0, microsecond=0
                        ).time()

                        self.hass.async_create_task(
                            self._program.start_time.async_set_value(target_time)
                        )
                except ValueError:
                    # Handle case where offset state isn't a valid number
                    pass

        if self._program.sunset_offset:
            # 1. Safely get the sun setting state
            sunset_state = self._hass.states.get("sensor.sun_next_setting")

            if sunset_state and sunset_state.state not in ("unknown", "unavailable"):
                # 2. Parse the ISO string to an aware datetime
                sunset = dt_util.parse_datetime(sunset_state.state)

                if sunset:
                    try:
                        # 3. Convert the string offset to a float/int
                        if self._program.sunset_offset.state:
                            offset_minutes = float(self._program.sunset_offset.state)
                            d = timedelta(minutes=offset_minutes)

                        # 4. Apply offset and convert to local time
                        adjusted_sunset = dt_util.as_local(sunset + d)

                        # 5. Extract time and update the value
                        target_time = adjusted_sunset.replace(
                            second=0, microsecond=0
                        ).time()

                        self.hass.async_create_task(
                            self._program.start_time.async_set_value(target_time)
                        )
                    except ValueError:
                        # Handle case where offset is not a valid number
                        pass

        if self._paused:
            # don't process changes to when attributes change
            return

        for zone in self._zones:
            kwargs = {}
            kwargs["action"] = "update_next_run"
            await zone.switch.async_toggle(**kwargs)

        # calculate the duration of the program
        if self._program.enabled.state == CONST_OFF:
            #program is disabled
            self._default_run_time = 0
            await self.default_run_time_set()
        elif self.state != CONST_ON:
            #Don't update while the program is running
            zones = []
            for _ in range(self.repeats_value - 1, -1, -1):
                zones += await self.build_run_script(True)

            await self.calculate_program_remaining(
                [], zones,0, default_run_time=True
            )

        self.async_schedule_update_ha_state()

    async def update_next_run_debounced(self, event=None):
        """Coalesce bursts of monitored entity changes into one recalculation.

        update_next_run loops over every zone and refreshes several sensors;
        chatty monitored entities (adjustment, water source...) triggered a
        full recalculation on every state change which is expensive on
        low-power hosts.
        """
        if self._unsub_next_run_debounce:
            self._unsub_next_run_debounce()
            self._unsub_next_run_debounce = None

        async def _run(_now):
            self._unsub_next_run_debounce = None
            await self.update_next_run()

        debounce = (
            CONST_NEXT_RUN_DEBOUNCE_LOW_POWER
            if self._low_power
            else CONST_NEXT_RUN_DEBOUNCE
        )
        self._unsub_next_run_debounce = async_call_later(
            self._hass, debounce, _run
        )

    def _zone_remaining_seconds(self, zone: ZoneData) -> int:
        """Best-effort remaining seconds for a zone (sensor or switch)."""
        try:
            val = zone.remaining_time.numeric_value
            if val is not None:
                return max(0, int(val))
        except Exception:  # noqa: BLE001
            pass
        try:
            return max(0, int(zone.switch.remaining_time_value))
        except Exception:  # noqa: BLE001
            return 0

    def _find_zone_by_solenoid(self, solenoid: str) -> ZoneData | None:
        for zone in self._zones:
            if zone.zone == solenoid:
                return zone
        return None

    async def async_reconcile_solenoids(
        self,
        raw: dict | None,
        adjusted: dict | None,
    ) -> None:
        """Close solenoids that must not stay open after resume accounting.

        Boot may have skipped solenoid_turn_off using a T0 downtime snapshot.
        Resume recomputes at T1 — close any valve that was running in ``raw``
        but is no longer in ``adjusted['running']`` (finished during the gap,
        or resume discarded entirely).
        """
        keep: set[str] = set()
        if adjusted:
            for item in adjusted.get("running") or []:
                sol = item.get("solenoid")
                if sol and int(item.get("remaining_s", 0)) > 0:
                    keep.add(sol)

        raw_running = {
            item.get("solenoid")
            for item in (raw or {}).get("running") or []
            if item.get("solenoid")
        }
        # If resume is discarded, close every previously-running solenoid.
        to_close = raw_running if adjusted is None else (raw_running - keep)
        for solenoid in to_close:
            zone = self._find_zone_by_solenoid(solenoid)
            if zone and zone.switch:
                _LOGGER.info(
                    "Reconciling solenoid %s closed after resume (no longer active)",
                    solenoid,
                )
                await zone.switch.async_solenoid_turn_off()

    async def async_hand_off_interlock_after_failed_resume(self) -> None:
        """Remove self from interlock queue and wake the next program."""
        # Wait for siblings — same barrier as successful resume — so we do not
        # persist an empty/partial queue when another program is still loading.
        await async_restore_interlock_queue_ready(self._hass)
        # Entity.__eq__ is unreliable — always compare by identity.
        if not any(p is self for p in QUEUEDPROGRAMS):
            return
        QUEUEDPROGRAMS[:] = [p for p in QUEUEDPROGRAMS if p is not self]
        await async_update_program_checkpoint(
            self._hass,
            self._attr_unique_id,
            None,
            interlock_queue=current_interlock_queue_ids(),
        )
        entry = self._hass.data.get(DOMAIN, {}).get(self._attr_unique_id)
        if entry is not None:
            entry[ATTR_RUNTIME_CHECKPOINT] = None
        if QUEUEDPROGRAMS:
            _LOGGER.info(
                "Resume failed for %s; unpausing next interlock program %s",
                self._name,
                QUEUEDPROGRAMS[0].name,
            )
            await QUEUEDPROGRAMS[0].pause_switch.async_turn_off()

    async def async_save_checkpoint(self, *, force: bool = False) -> None:
        """Persist mid-cycle state so a reboot can resume watering."""
        queue_ids = current_interlock_queue_ids()

        if not self._state and not self._running_zones and not self._remaining_zones:
            if force:
                # HA stop with nothing active: clear any stale checkpoint for
                # this program (do NOT re-save an old payload) but flush queue.
                await async_update_program_checkpoint(
                    self._hass,
                    self._attr_unique_id,
                    None,
                    interlock_queue=queue_ids,
                )
                entry = self._hass.data.get(DOMAIN, {}).get(self._attr_unique_id)
                if entry is not None:
                    entry[ATTR_RUNTIME_CHECKPOINT] = None
            return

        now_mono = self._hass.loop.time()
        if (
            not force
            and now_mono - self._last_checkpoint_monotonic < CONST_CHECKPOINT_INTERVAL
        ):
            return
        self._last_checkpoint_monotonic = now_mono

        running = []
        for zone in list(self._running_zones):
            if not zone.zone:
                continue
            rem = self._zone_remaining_seconds(zone)
            if rem <= 0:
                try:
                    rem = max(0, int(zone.default_run_time.numeric_value or 0))
                except Exception:  # noqa: BLE001
                    rem = 0
            if rem <= 0:
                _LOGGER.warning(
                    "Skip checkpoint running entry for %s: remaining_s=0",
                    zone.zone,
                )
                continue
            running.append({"solenoid": zone.zone, "remaining_s": rem})
        remaining = []
        for zone in list(self._remaining_zones):
            if not zone.zone:
                continue
            rem = self._zone_remaining_seconds(zone)
            if rem <= 0:
                try:
                    rem = max(0, int(zone.default_run_time.numeric_value or 0))
                except Exception:  # noqa: BLE001
                    rem = 0
            if rem <= 0:
                continue
            remaining.append({"solenoid": zone.zone, "remaining_s": rem})

        if not running and not remaining:
            if force:
                # Active lists empty (cycle just finished) — clear stale Store
                # entry rather than resurrecting the previous checkpoint.
                await async_update_program_checkpoint(
                    self._hass,
                    self._attr_unique_id,
                    None,
                    interlock_queue=queue_ids,
                )
                entry = self._hass.data.get(DOMAIN, {}).get(self._attr_unique_id)
                if entry is not None:
                    entry[ATTR_RUNTIME_CHECKPOINT] = None
            return

        payload = build_checkpoint(
            program_unique_id=self._attr_unique_id,
            program_name=self._name,
            scheduled=self._scheduled,
            start_time=self._start_time,
            paused=self._paused,
            running=running,
            remaining=remaining,
        )

        await async_update_program_checkpoint(
            self._hass,
            self._attr_unique_id,
            payload,
            interlock_queue=queue_ids,
        )

        # Keep in-memory copy for zone startup checks on next boot path
        entry = self._hass.data.get(DOMAIN, {}).get(self._attr_unique_id)
        if entry is not None:
            entry[ATTR_RUNTIME_CHECKPOINT] = payload

        _LOGGER.debug(
            "Irrigation checkpoint saved for %s (%d running, %d queued)",
            self._name,
            len(running),
            len(remaining),
        )

    async def async_clear_checkpoint(self) -> None:
        """Remove persisted mid-cycle state after a clean finish/stop."""
        await async_update_program_checkpoint(
            self._hass,
            self._attr_unique_id,
            None,
            interlock_queue=current_interlock_queue_ids(),
        )
        entry = self._hass.data.get(DOMAIN, {}).get(self._attr_unique_id)
        if entry is not None:
            entry[ATTR_RUNTIME_CHECKPOINT] = None
        self._resume_overrides = {}

    async def async_resume_from_checkpoint(self) -> bool:
        """Resume a cycle interrupted by HA restart. Returns True if resumed.

        Multi-program safe: restores the interlock queue from Store, then only
        the head of the queue (or any program when interlock is off) starts
        watering immediately. Programs behind the head resume paused and wait
        to be unpaused when the previous program finishes.
        """
        entry = self._hass.data.get(DOMAIN, {}).get(self._attr_unique_id, {})
        raw = entry.get(ATTR_RUNTIME_CHECKPOINT)
        if not raw:
            # Fallback: load from Store in case platforms started without it
            stored = await checkpoint_store(self._hass).async_load() or {}
            raw = (stored.get("programs") or {}).get(self._attr_unique_id)
        sequential = self.degree_of_parallel <= 1
        # Single clock at resume time (T1) — boot skip used T0; reconcile closes
        # any valve that finished between T0 and T1.
        adjusted = (
            apply_downtime(raw, sequential=sequential) if raw else None
        )
        if not adjusted:
            await self.async_reconcile_solenoids(raw, None)
            await self.async_hand_off_interlock_after_failed_resume()
            await self.async_clear_checkpoint()
            return False

        if self._state or not self._finished:
            _LOGGER.warning(
                "Skip resume for %s: program already active", self._name
            )
            return False

        running_items = list(adjusted.get("running") or [])
        remaining_items = list(adjusted.get("remaining") or [])
        if not running_items and not remaining_items:
            await self.async_reconcile_solenoids(raw, None)
            await self.async_hand_off_interlock_after_failed_resume()
            await self.async_clear_checkpoint()
            return False

        # Close valves that boot kept open but T1 says are done
        await self.async_reconcile_solenoids(raw, adjusted)

        # Rebuild interlock order before deciding whether we may water now.
        # Waits briefly so sibling programs can register (boot-order race).
        await async_restore_interlock_queue_ready(self._hass)
        if self.interlock and not any(p is self for p in QUEUEDPROGRAMS):
            # We were mid-cycle; ensure we appear in the live queue
            QUEUEDPROGRAMS.append(self)
            # Never persist ``adjusted`` (already downtime-reduced) — keep raw
            keep_payload = entry.get(ATTR_RUNTIME_CHECKPOINT) or raw
            await async_update_program_checkpoint(
                self._hass,
                self._attr_unique_id,
                keep_payload,
                interlock_queue=current_interlock_queue_ids(),
            )

        must_wait = bool(
            self.interlock
            and QUEUEDPROGRAMS
            and QUEUEDPROGRAMS[0] is not self
        )

        _LOGGER.info(
            "Resuming irrigation %s after reboot (downtime=%ss, running=%d, queued=%d, wait_interlock=%s)",
            self._name,
            adjusted.get("downtime_s", 0),
            len(running_items),
            len(remaining_items),
            must_wait,
        )

        self._stop = False
        self._state = True
        self._finished = False
        self._scheduled = bool(adjusted.get("scheduled", False))
        self._paused = must_wait or bool(adjusted.get("paused", False))
        start_raw = adjusted.get("start_time")
        if start_raw:
            parsed = dt_util.parse_datetime(start_raw)
            if parsed:
                self._start_time = dt_util.as_local(parsed)

        self._run_zones = []
        self._remaining_zones = []
        self._running_zones = []
        self._resume_overrides = {}

        # Queued zones first (not yet started)
        for item in remaining_items:
            zone = self._find_zone_by_solenoid(item.get("solenoid"))
            if not zone or not zone.switch:
                continue
            rem = int(item.get("remaining_s", 0))
            await zone.switch.async_set_resume_state(rem, status=CONST_PENDING)
            self._remaining_zones.append(zone)
            self._run_zones.append(zone)
            self._resume_overrides[zone.zone] = rem

        # Currently watering zones — put at front of queue and launch via monitor
        for item in reversed(running_items):
            zone = self._find_zone_by_solenoid(item.get("solenoid"))
            if not zone or not zone.switch:
                continue
            rem = int(item.get("remaining_s", 0))
            if rem <= 0:
                continue
            await zone.switch.async_set_resume_state(rem, status=CONST_PENDING)
            self._remaining_zones.insert(0, zone)
            if zone not in self._run_zones:
                self._run_zones.insert(0, zone)
            self._resume_overrides[zone.zone] = rem

        if not self._remaining_zones:
            await self.async_reconcile_solenoids(raw, None)
            await self.async_hand_off_interlock_after_failed_resume()
            await self.async_clear_checkpoint()
            self._state = False
            self._finished = True
            return False

        if must_wait or self._paused:
            # Keep pause ON for interlock waiters OR user-paused mid-cycle
            # (turning pause OFF would fire pause_program and clear _paused).
            await self.pause_switch.async_turn_on()
        else:
            await self.pause_switch.async_turn_off()

        # Seed remaining from resume overrides so a paused runner does not see
        # ``_program_remaining == 0`` and call async_turn_off immediately.
        seeded = sum(max(0, int(v)) for v in self._resume_overrides.values())
        if seeded <= 0:
            seeded = sum(
                max(0, self._zone_remaining_seconds(z)) for z in self._remaining_zones
            )
        self._program_remaining = max(seeded, 1 if self._remaining_zones else 0)
        await self.remaining_time_set()

        self.async_schedule_update_ha_state()

        async def _resume_runner(_now=None):
            # Same control loop as async_turn_on after build_run_script, but
            # keep spinning while paused (interlock wait / user pause) even
            # when remaining has not been recalculated yet.
            try:
                while not self._stop and (
                    self._paused
                    or self._program_remaining > 0
                    or self._remaining_zones
                    or self._running_zones
                ):
                    await self.run_monitor_zones()
                if self._stop:
                    return
                event_data = {
                    "action": "program_turned_off",
                    "device_id": self.entity_id,
                    "program": self.name,
                }
                self._hass.bus.async_fire("irrigation_event", event_data)
                await self.async_turn_off()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Resume runner failed for %s", self._name)

        self.hass.async_create_task(_resume_runner())
        return True

    async def async_added_to_hass(self):
        """Add listener."""
        self._unsub_point_in_time = async_track_point_in_utc_time(
            self._hass, self.point_in_time_listener, self.get_next_interval()
        )

        async def hass_started(event):
            """HA has started."""
            # build the zone to pump relationships
            pumps = {}
            if self._program.pump:
                for zone in self._zones:
                    # create pump - zone list
                    if self._program.pump not in pumps:
                        pumps[self._program.pump] = [zone]
                    else:
                        pumps[self._program.pump].append(zone)

            # Build Zone Attributes to support the custom card
            self.hass.async_create_task(self.define_program_attributes())
            # create pump class to start/stop pumps
            for pump, zones in pumps.items():
                # pass pump_switch, list of zones, off_delay
                pumpobj = PumpClass(self._hass, pump, zones, self)
                self._pumps.append(pumpobj)

            # calculate the next run
            await self.update_next_run()
            # set up to monitor these entities
            await asyncio.sleep(0)
            await self.set_up_entity_monitoring()

            # Resume mid-cycle watering interrupted by a reboot
            await self.async_resume_from_checkpoint()

        # setup the callback to kick in when HASS has started
        # listen for config_flow change and apply the updates
        self._unsub_start = async_at_started(self._hass, hass_started)

        @callback
        def _on_ha_stop(_event: Event) -> None:
            """Flush checkpoint on shutdown — leave valves open for resume.

            Fire-and-forget: HA may exit before the Store write completes.
            That is OK — the periodic ~10s checkpoint is the durable baseline;
            this flush only tries to shrink the last gap. Store uses atomic
            write+rename so a torn write will not corrupt the previous file.
            """
            self._ha_stopping = True
            self._hass.async_create_task(self.async_save_checkpoint(force=True))

        self._unsub_ha_stop = self._hass.bus.async_listen(
            EVENT_HOMEASSISTANT_STOP, _on_ha_stop
        )

        await super().async_added_to_hass()

        # generate the entities card yaml to replicate the custom card
        await asyncio.sleep(1)
        if self._program.card_yaml:
            self.generate_card()

    async def set_up_entity_monitoring(self):
        """Set up to monitor these entities to change the next run data."""

        async def monitor_append(object, name=None, table=None):

            # wait for the entity_id to be available before trying to monitor it,
            # otherwise the monitoring will stop working if the entity is renamed or
            # unavailable at startup
            timeout = 5
            starttime = datetime.now()
            # if the entity is not available after the timeout,
            # it will be skipped and a notification will be created to alert the user
            while self.hass.states.get(object) is None and datetime.now() - starttime < timedelta(seconds=timeout):
                await asyncio.sleep(0.1)

            if object not in table:
                try:
                    table.append(object)
                except AttributeError:
                    async_dismiss(self.hass, "irrigation_device_error1")
                    async_create(
                        self.hass,
                        message=f"Warning, configured monitor {name} item is no longer available or has been renamed",
                        title="Irrigation Controller",
                        notification_id="irrigation_device_error1",
                    )

        monitor = []

        await monitor_append(self._program.start_time.entity_id, "start_time", monitor)
        if self._program.sunrise_offset:
            await monitor_append(self._program.sunrise_offset.entity_id, "sunrise_offset", monitor)
            await monitor_append("sensor.sun_next_rising", None, monitor)
        if self._program.sunset_offset:
            await monitor_append(self._program.sunset_offset.entity_id, "sunset_offset", monitor)
            await monitor_append("sensor.sun_next_setting", None, monitor)
        await monitor_append(self._program.enabled.entity_id, "enabled", monitor)
        if self._program.rain_delay:
            await monitor_append(self._program.rain_delay.entity_id, "rain_delay", monitor)
            await monitor_append(self._program.rain_delay_days.entity_id, "rain_delay_days", monitor)
        if self._program.frequency:
            await monitor_append(self._program.frequency.entity_id, "frequency", monitor)
        if self._program.inter_zone_delay:
            await monitor_append(self._program.inter_zone_delay.entity_id, "inter_zone_delay", monitor)
        if self._program.repeats:
            await monitor_append(self._program.repeats.entity_id, "repeats", monitor)
        if self._program.water_source:
            await monitor_append(self._program.water_source, "water_source", monitor)

        for zone in self._zones:
            await monitor_append(zone.switch.entity_id, "zone", monitor)
            await monitor_append(zone.enabled.entity_id, "enabled", monitor)
            # zone.zone = IrrigationZoneData.zone (solenoid entity_id str),
            # not Zone.zone — refresh status cache when the valve recovers.
            # See docs/design-zone-status-valve-cache.md
            if zone.zone:
                await monitor_append(zone.zone, "solenoid", monitor)
            if zone.frequency:
                await monitor_append(zone.frequency.entity_id, "frequency", monitor)
            if zone.rain_sensor:
                await monitor_append(zone.rain_sensor, "rain_sensor", monitor)
            if zone.ignore_sensors:
                await monitor_append(zone.ignore_sensors.entity_id, "ignore_sensors", monitor)
            if zone.adjustment:
                await monitor_append(zone.adjustment, "adjustment", monitor)
            if zone.water:
                await monitor_append(zone.water.entity_id, "water", monitor)
            if zone.repeat:
                await monitor_append(zone.repeat.entity_id, "repeat", monitor)
            if zone.wait:
                await monitor_append(zone.wait.entity_id, "wait", monitor)

        self._unsub_monitor = async_track_state_change_event(
            self._hass, tuple(monitor), self.update_next_run_debounced
        )

        monitor2 = []

        if self._program.water_source and self._program.water_source_pause:
            await monitor_append(self._program.water_source, "water_source", monitor2)
        self._unsub_pause_water = async_track_state_change_event(
            self._hass, tuple(monitor2), self.pause_program_water_source
        )

        monitor3 = []
        await monitor_append(self._program.pause.entity_id, "pause", monitor3)
        self._unsub_pause = async_track_state_change_event(
            self._hass, tuple(monitor3), self.pause_program
        )

    async def define_program_attributes(self):
        """Build attributes in run order."""

        # Program attributes
        self._extra_attrs = {}
        self._extra_attrs[ATTR_START] = self._program.start_time.entity_id
        if self._program.start_type == "sunrise":
            self._extra_attrs["sunrise"] = self._program.sunrise_offset.entity_id
        elif self._program.start_type == "sunset":
            self._extra_attrs["sunset"] = self._program.sunset_offset.entity_id
        if self._program.frequency:
            self._extra_attrs[ATTR_RUN_FREQ] = self._program.frequency.entity_id
        self._extra_attrs[ATTR_IRRIGATION_ON] = self._program.enabled.entity_id
        if self._program.inter_zone_delay:
            self._extra_attrs[ATTR_DELAY] = self._program.inter_zone_delay.entity_id
        if self._program.repeats:
            self._extra_attrs["repeats"] = self._program.repeats.entity_id
        if self._program.rain_delay:
            self._extra_attrs["enable_rain_delay"] = self._program.rain_delay.entity_id
            self._extra_attrs["rain_delay_days"] = (
                self._program.rain_delay_days.entity_id
            )
        self._extra_attrs[ATTR_REMAINING] = self._program.remaining_time.entity_id
        self._extra_attrs[ATTR_DEFAULT_RUN_TIME] = (
            self._program.default_run_time.entity_id
        )
        self._extra_attrs[ATTR_SHOW_CONFIG] = self._program.config.entity_id
        self._extra_attrs[ATTR_PAUSE] = self._program.pause.entity_id

        # zone loop to initialise the attributes
        zones = []
        for zone in self._zones:
            try:
                # wait for the entity_id to be available before trying to access it
                timeout = 5
                starttime = datetime.now()
                while zone.switch.entity_id is None and datetime.now() - starttime < timedelta(seconds=timeout):
                    await asyncio.sleep(.1)
                zones.append(zone.switch.entity_id)
            except AttributeError:
                _LOGGER.error(zone.switch.entity_id)
                async_dismiss(self.hass, "irrigation_device_error2")
                async_create(
                    self.hass,
                    message=f"Warning, configured zone item {zone} is no longer available or has been renamed",
                    title="Irrigation Controller",
                    notification_id="irrigation_device_error2",
                )
        self._extra_attrs["zones"] = zones
        self.async_schedule_update_ha_state()

    async def entity_toggle_zone(self, zone) -> None:
        """Toggle a specific zone."""
        # called from the zone to ensure the program is notified
        # when the zone is stopped manually while it is running
        # built to handle a list but only one
        checkzone = None
        # index the switch to process
        for czone in self._zones:
            if czone.switch == zone.switch:
                checkzone = czone
                break

        if self._run_zones == []:
            # program is not already running
            self._running_zone = zone
            self._scheduled = False
            self._run_zones.append(zone)
            self.hass.async_create_task(self.async_turn_on())
        elif self._run_zones.count(zone) == 0:
            # program is running add the zone to the list to run
            self._run_zones.append(zone)
            self._remaining_zones.append(zone)
            if checkzone:
                kwargs = {}
                kwargs["action"] = "prepare_to_run"
                kwargs["scheduled"] = self._scheduled
                await checkzone.switch.async_toggle(**kwargs)
        else:
            # zone is running/queued turn it off
            if checkzone:
                await checkzone.switch.async_turn_off()
            if self._run_zones.count(zone) > 0:
                self._run_zones.remove(zone)

            if len(self._run_zones) == 0:
                await self.async_turn_off()

        self.async_schedule_update_ha_state()

    @property
    def default_run_time_value(self):
        """Next run value for sensor."""
        return self._default_run_time

    @property
    def remaining_time_value(self):
        """Next run value for sensor."""
        return self._program_remaining

    @property
    def inter_zone_delay(self):
        """Return interzone delay value."""
        if self.degree_of_parallel > 1:
            return 0
        if self._program.inter_zone_delay and self._program.inter_zone_delay.state:
            return int(self._program.inter_zone_delay.state)
        return 0

    @property
    def name(self):
        """Return the name of the variable."""
        return self._name

    @property
    def is_on(self):
        """Return true if switch is on."""
        return self._state

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return self._extra_attrs

    @property
    def irrigation_on_value(self):
        """Zone  entity value."""
        return self._program.enabled.state

    @property
    def interlock(self):
        """Zone  entity value."""
        return self._program.interlock

    @property
    def pause_switch(self):
        """Zone  entity value."""
        return self._program.pause

    @property
    def remaining_zones(self) -> list:
        """Zones queued to run for this program."""
        return self._remaining_zones

    @property
    def running_zones(self) -> list:
        """Zones currently running for this program."""
        return self._running_zones

    @property
    def repeats_value(self):
        """Get the value of program repeats."""
        value = 1
        if self._program.repeat is True:
            value = self._program.repeats.native_value
        return max(1, int(value))

    @property
    def start_time_value(self) -> int:
        """Start time entity value."""
        value = None
        if self._program.start_time is not None:
            value = self._program.start_time.state
        return value

    @property
    def degree_of_parallel(self):
        """Start time entity value."""
        return int(self._program.parallel)

    async def build_run_script(self, init=False):
        """Build the run script based on each zones data."""
        zones = []
        for zone in self._zones:
            if self._running_zone:
                # Zone has been manually run from service call
                if zone.switch != self._running_zone.switch:
                    continue
            # auto_run where program started based on start time
            if (
                zone.switch
                and await zone.switch.should_run_ex(self._scheduled) is False
            ):
                # calculate the next run
                continue

            if not init:
                kwargs = {}
                kwargs["action"] = "prepare_to_run"
                kwargs["scheduled"] = self._scheduled
                await zone.switch.async_toggle(**kwargs)
            zones.append(zone)
        return zones

    async def calculate_program_remaining(
        self, running_zones, remaining_zones, izd_remaining=0, default_run_time=False
    ):
        """Calculate the remaining time for the program."""

        class Stream:
            """Container for items that keeps a running sum for parrallel zones."""

            def __init__(self) -> None:
                self.items = []
                self.sum = 0

            def append(self, item):
                self.items.append(item)
                self.sum += item

        if default_run_time:
            remaining = [zone.switch.default_run_time for zone in remaining_zones]
        else:
            remaining = [zone.remaining_time.numeric_value for zone in running_zones]
            for zone in remaining_zones:
                # During resume, queued zones already have a shortened remaining
                # in ``_resume_overrides`` — do not inflate with full default_run_time.
                if zone.zone and zone.zone in self._resume_overrides:
                    remaining.append(max(0, int(self._resume_overrides[zone.zone])))
                else:
                    remaining.append(zone.switch.default_run_time)

        streams = []
        # create the streams
        for _ in range(self.degree_of_parallel):
            stream: Stream = Stream()
            streams.append(stream)

        for time in remaining:
            # put the time in the stream with the lowest time
            minstream = None
            for stream in streams:
                if minstream is None:
                    minstream = stream
                if minstream.sum > stream.sum:
                    minstream = stream
            if minstream:
                minstream.append(time)

        remaining_time = 0
        for stream in streams:
            # return the max stream time
            remaining_time = max(remaining_time, stream.sum)

        if self.degree_of_parallel == 1:
            # add in the required zone transitions
            remaining_count = len(remaining_zones) + len(running_zones)
            remaining_time += self.inter_zone_delay * (remaining_count - 1)
            # If there is an active izd add it to the total
            if izd_remaining:
                remaining_time += izd_remaining

        if len(running_zones) == 0 and len(remaining_zones) == 0:
            remaining_time = 0

        if default_run_time is True:
            self._default_run_time = remaining_time
            await self.default_run_time_set()
        else:
            self._program_remaining = remaining_time
            await self.remaining_time_set()

        self.async_schedule_update_ha_state()

        return remaining_time

    async def pause_program_water_source(
        self,
        event: Event[EventStateChangedData],
    ):
        """Program paused status changes."""
        if (
            event.data["new_state"]
            and event.data["new_state"].state == CONST_ON
            and self.state == CONST_ON
        ):
            await self._program.pause.async_turn_off()

        if (
            event.data["new_state"]
            and event.data["new_state"].state == CONST_OFF
            and self.state == CONST_ON
        ):
            await self._program.pause.async_turn_on()

    async def pause_program(
        self,
        event: Event[EventStateChangedData],
    ):
        """Program paused status changes."""
        if self._program.pause.is_on:
            self._paused = True

        for zone in self._zones:
            kwargs = {}
            kwargs["action"] = "pause"
            await zone.switch.async_toggle(**kwargs)
        await asyncio.sleep(1)

        if not self._program.pause.is_on:
            self._paused = False

        if self._state is False:
            await self._program.pause.async_turn_off()
        elif self._state:
            # Persist paused flag immediately — run_monitor_zones skips its
            # periodic save while paused, so a crash before HA stop would
            # otherwise resume with downtime incorrectly applied.
            await self.async_save_checkpoint(force=True)

    async def zone_pending(self, zone) -> bool:
        """Determine if a another instance of the zone is pending."""
        if self._remaining_zones.count(zone) >= 1:
            return True
        return False

    async def run_monitor_zones(self):
        """Monitor zones to start based on inter zone delay."""

        if self._stop is True:
            # break out if program terminated
            self._program_remaining = 0
            await self.remaining_time_set()
            return self._running_zones

        if self._paused:
            await asyncio.sleep(1)
            return self._running_zones

        await self.calculate_program_remaining(
            self._running_zones, self._remaining_zones, 0, False
        )
        await self.async_save_checkpoint()
        await asyncio.sleep(1)

        if (
            len(self._running_zones) < self.degree_of_parallel
            and len(self._remaining_zones) > 0
        ):
            await self.zone_turn_on(
                self._remaining_zones[0], len(self._remaining_zones) == 1
            )
            self._running_zones.append(self._remaining_zones[0])
            del self._remaining_zones[0]
            return self._running_zones

        # iterate over a copy: zones are appended/removed while looping
        for running_zone in list(self._running_zones):
            # add another zone as required
            if self._state is False:
                # break out if program terminated
                break
            if (
                self.inter_zone_delay <= 0
                and running_zone.remaining_time.numeric_value
                <= abs(self.inter_zone_delay)
                and len(self._running_zones) == self.degree_of_parallel
            ):
                # start the next zone if there is one
                if len(self._remaining_zones) > 0:
                    await self.zone_turn_on(
                        self._remaining_zones[0], len(self._remaining_zones) == 1
                    )
                    self._running_zones.append(self._remaining_zones[0])
                    del self._remaining_zones[0]

            if (
                self.inter_zone_delay > 0
                and running_zone.remaining_time.numeric_value <= 1
                and len(self._remaining_zones) > 0
            ):
                # there is a +'ve IZD and there is a zone to follow
                # Delay before next zone
                for izd in range(int(self.inter_zone_delay), 0, -1):
                    await asyncio.sleep(1)
                    await self.calculate_program_remaining(
                        self._running_zones, self._remaining_zones, izd, False
                    )
                    if self.state == CONST_OFF:
                        break
                # Interzone delay is complete; a zone may have been removed
                # manually during the delay, guard against IndexError
                if (
                    len(self._running_zones) < self.degree_of_parallel
                    and len(self._remaining_zones) > 0
                ):
                    await self.zone_turn_on(
                        self._remaining_zones[0], len(self._remaining_zones) == 1
                    )
                    self._running_zones.append(self._remaining_zones[0])
                    del self._remaining_zones[0]

        return self._running_zones

    async def zone_turn_on(self, zone, last=False):
        """Turn on the irrigation zone."""
        await zone.switch.set_scheduled(self._scheduled)
        # run in the event loop to support independant executions

        #need to pass the program start time to support running across midnight
        #this will be the last_ran time for the zone

        override = self._resume_overrides.pop(zone.zone, None) if zone.zone else None
        self.hass.async_create_task(
            zone.switch.async_turn_on_from_program(
                last, self._start_time, remaining_override=override
            )
        )
        await asyncio.sleep(0)

    async def async_turn_on(self, **kwargs):
        """Turn on the switch."""
        if self._program.enabled.state == CONST_OFF and self._scheduled is True:
            return
        self._stop = False
        if self._state is True:
            # program is already running
            return
        if self._finished is False:
            # program is not finalised from previous run
            return

        # Multiple iterations of the program
        self._run_zones = []
        for _ in range(self.repeats_value - 1, -1, -1):
            self._run_zones += await self.build_run_script()

        if len(self._run_zones) > 0:
            # raise event when the program starts
            event_data = {
                "action": "program_turned_on",
                "device_id": self.entity_id,
                "scheduled": self._scheduled,
                "program": self.name,
            }
            self._hass.bus.async_fire("irrigation_event", event_data)
        else:
            # No zones to run
            event_data = {
                "action": "program_no_zones_ready",
                "device_id": self.entity_id,
                "scheduled": self._scheduled,
                "program": self.name,
            }
            self._hass.bus.async_fire("irrigation_event", event_data)
            return

        self._state = True
        self._finished = False
        self.async_schedule_update_ha_state()

        # stop all running programs except the calling program
        if self._program.interlock:
            await queue_program(self._hass, self)
            if self._pumps:
                await asyncio.sleep(1)

        # calculate the remaining time for the program
        # Monitor and start the zone with lead/lag time
        for zone in self._run_zones:
            if zone.status.state in (CONST_PENDING, CONST_ON):
                self._remaining_zones.append(zone)

        self._running_zones.clear()
        await self.run_monitor_zones()

        while self._program_remaining > 0:
            await self.run_monitor_zones()

        event_data = {
            "action": "program_turned_off",
            "device_id": self.entity_id,
            "program": self.name,
        }
        self._hass.bus.async_fire("irrigation_event", event_data)
        await self.async_turn_off()

    async def async_turn_off(self, **kwargs):
        """Stop the switch/program."""

        self._stop = True

        # HA is shutting down: keep valves open and preserve checkpoint so the
        # cycle can resume after reboot. Do not clear Store or close solenoids.
        # Do NOT unpause the next interlock program here — QUEUEDPROGRAMS is
        # persisted and restored after boot; waking the next program mid-stop
        # would start watering into a dying HA process.
        if self._ha_stopping:
            await self.async_save_checkpoint(force=True)
            self._state = False
            self._finished = True
            self.async_schedule_update_ha_state()
            return

        if self._pumps:
            event_data = {
                "action": "turn_off_pump_all",
                "program": self.entity_id,
            }
            self.hass.bus.async_fire("irrigation_event", event_data)
            await asyncio.sleep(3)

        await self._program.pause.async_turn_off()
        self._scheduled = False
        self._running_zone = None
        self._run_zones = []
        self._program_remaining = 0
        for zone in self._zones:
            if zone.switch.state == CONST_ON:
                await zone.switch.async_turn_off()
                await asyncio.sleep(0)
        self._state = False
        self._finished = True
        self.async_schedule_update_ha_state()

        # check the queue remove this program
        # (rebuild rather than pop while enumerating)
        QUEUEDPROGRAMS[:] = [p for p in QUEUEDPROGRAMS if p.name != self._name]
        # unpause the next program in the queue
        if len(QUEUEDPROGRAMS) > 0:
            await QUEUEDPROGRAMS[0].pause_switch.async_turn_off()
        self._remaining_zones.clear()
        await self.async_clear_checkpoint()
