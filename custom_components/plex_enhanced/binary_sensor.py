"""Binary sensors for Plex Enhanced."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .alert_listener import PlexAlertListener
from .coordinator import PlexEnhancedRuntime, PlexServerCoordinator
from .entity import PlexServerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: PlexEnhancedRuntime = entry.runtime_data
    async_add_entities(
        PlexServerOnlineBinarySensor(
            coord, runtime.alert_listeners.get(machine_id)
        )
        for machine_id, coord in runtime.server_coordinators.items()
    )


class PlexServerOnlineBinarySensor(PlexServerEntity, BinarySensorEntity):
    """Reports whether the per-server poll is succeeding.

    State follows ``coordinator.last_update_success`` only — the alert socket
    might be down while polling still works perfectly. The websocket health
    is exposed as an attribute so users can build deeper diagnostics.
    """

    _attr_translation_key = "online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self,
        coordinator: PlexServerCoordinator,
        alert_listener: PlexAlertListener | None,
    ) -> None:
        super().__init__(coordinator, "online")
        self._alert_listener = alert_listener

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def available(self) -> bool:
        # Connectivity sensor must always report — never mark unavailable.
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "alert_listener_connected": (
                self._alert_listener.is_connected()
                if self._alert_listener is not None
                else False
            ),
        }
