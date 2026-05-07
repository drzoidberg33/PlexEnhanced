"""Base entities for Plex Enhanced.

Each base class binds an entity to the right coordinator and produces a
DeviceInfo for the underlying Plex Media Server. Server entities and library
entities share the same physical device (the PMS); the library entity does
not declare ``name`` / ``manufacturer`` so it merges into the existing entry.
"""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PlexLibraryCoordinator, PlexServerCoordinator


def _server_device_info(coordinator: PlexServerCoordinator) -> DeviceInfo:
    data = coordinator.data
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.machine_identifier)},
        name=coordinator.display_name,
        manufacturer="Plex",
        model="Plex Media Server",
        sw_version=data.version if data else None,
        configuration_url=(
            f"https://app.plex.tv/desktop/#!/server/{coordinator.machine_identifier}"
        ),
    )


class PlexServerEntity(CoordinatorEntity[PlexServerCoordinator]):
    """Entity attached to the per-server polling coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PlexServerCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.machine_identifier}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        return _server_device_info(self.coordinator)


class PlexLibraryEntity(CoordinatorEntity[PlexLibraryCoordinator]):
    """Entity attached to the per-server library coordinator."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: PlexLibraryCoordinator, library_key: str, suffix: str
    ) -> None:
        super().__init__(coordinator)
        self._library_key = library_key
        self._attr_unique_id = (
            f"{coordinator.machine_identifier}_library_{library_key}_{suffix}"
        )

    @property
    def device_info(self) -> DeviceInfo:
        # Reference the same device as the server entities.
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.machine_identifier)},
        )
