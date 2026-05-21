"""Sensor entities for Plex Enhanced."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfDataRate
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import (
    PlexAccountCoordinator,
    PlexBandwidthCoordinator,
    PlexEnhancedRuntime,
    PlexLibraryCoordinator,
    PlexServerCoordinator,
)
from datetime import datetime, timezone

from .entity import PlexLibraryEntity, PlexServerEntity
from .models import PlexLibrary, PlexSession, PlexUser, session_to_payload

USER_STATE_IDLE = "Idle"

# Sentinel used when a session somehow lacks a started_at — keeps such
# entries last in the most-recently-started ordering without raising on
# None vs datetime comparison.
_OLDEST = datetime.min.replace(tzinfo=timezone.utc)

_LIBRARY_ICONS = {
    "movie": "mdi:movie",
    "show": "mdi:television",
    "artist": "mdi:music",
    "photo": "mdi:image-multiple",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: PlexEnhancedRuntime = entry.runtime_data

    # 1. Server- and library-scoped sensors are static — known at setup time.
    static_entities: list[SensorEntity] = []
    for machine_id, server_coord in runtime.server_coordinators.items():
        bandwidth_coord = runtime.bandwidth_coordinators.get(machine_id)
        static_entities.extend(
            [
                PlexServerActiveSessionsSensor(server_coord),
                PlexServerTranscodeSessionsSensor(server_coord),
                PlexServerVersionSensor(server_coord),
            ]
        )
        if bandwidth_coord is not None:
            static_entities.extend(
                [
                    PlexServerBandwidthSensor(bandwidth_coord, "total"),
                    PlexServerBandwidthSensor(bandwidth_coord, "lan"),
                    PlexServerBandwidthSensor(bandwidth_coord, "wan"),
                ]
            )
        library_coord = runtime.library_coordinators.get(machine_id)
        if library_coord and library_coord.data:
            static_entities.extend(
                PlexLibraryItemCountSensor(library_coord, library)
                for library in library_coord.data.values()
            )
    async_add_entities(static_entities)

    # 2. User sensors materialise on demand: as new users are found in the
    #    account refresh, register a sensor each. We don't unregister entities
    #    when a user disappears — they go unavailable, matching HA conventions.
    account_coord = runtime.account_coordinator
    server_coords = runtime.server_coordinators
    account_uuid = runtime.account.uuid
    known_user_ids: set[str] = set()

    @callback
    def _add_new_user_entities() -> None:
        users = account_coord.data or {}
        new_entities: list[SensorEntity] = []
        for user_id in users:
            if user_id in known_user_ids:
                continue
            known_user_ids.add(user_id)
            new_entities.append(
                PlexUserNowPlayingSensor(
                    account_coord, server_coords, account_uuid, user_id
                )
            )
        if new_entities:
            async_add_entities(new_entities)

    _add_new_user_entities()
    entry.async_on_unload(account_coord.async_add_listener(_add_new_user_entities))


def _session_summary(session: PlexSession) -> str:
    content = session.content
    if content.type == "episode" and content.show_title:
        return f"{content.show_title}: {content.title}"
    if content.type == "track" and content.artist:
        return f"{content.artist} - {content.title}"
    return content.title


class PlexServerActiveSessionsSensor(PlexServerEntity, SensorEntity):
    _attr_translation_key = "active_sessions"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:play-circle"
    _attr_native_unit_of_measurement = "sessions"

    def __init__(self, coordinator: PlexServerCoordinator) -> None:
        super().__init__(coordinator, "active_sessions")

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.sessions)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "sessions": [
                {
                    "user": s.user.title,
                    "title": _session_summary(s),
                    "type": s.content.type,
                    "library": s.content.library,
                    "player": s.player.title,
                    "product": s.player.product,
                    "state": s.player.state,
                    "transcoding": s.transcode is not None,
                    "bitrate_kbps": s.bitrate_kbps,
                    "local": s.player.local,
                }
                for s in self.coordinator.data.sessions
            ]
        }


class PlexServerTranscodeSessionsSensor(PlexServerEntity, SensorEntity):
    _attr_translation_key = "transcode_sessions"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:cog-transfer"
    _attr_native_unit_of_measurement = "sessions"

    def __init__(self, coordinator: PlexServerCoordinator) -> None:
        super().__init__(coordinator, "transcode_sessions")

    @property
    def native_value(self) -> int:
        return self.coordinator.data.transcode_session_count


class PlexServerBandwidthSensor(
    CoordinatorEntity[PlexBandwidthCoordinator], SensorEntity
):
    """Live throughput from the dedicated bandwidth coordinator (5 s cadence)."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfDataRate.KILOBITS_PER_SECOND
    _attr_device_class = SensorDeviceClass.DATA_RATE
    # Disabled by default: ``/statistics/bandwidth`` is heavy on some PMS
    # instances. The coordinator only polls while at least one listener is
    # attached, so leaving these off means zero traffic until a user opts in.
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: PlexBandwidthCoordinator, scope: str
    ) -> None:
        super().__init__(coordinator)
        self._scope = scope
        self._attr_translation_key = f"bandwidth_{scope}"
        self._attr_unique_id = (
            f"{coordinator.machine_identifier}_bandwidth_{scope}"
        )

    @property
    def device_info(self) -> DeviceInfo:
        # Reference the same physical "Plex Media Server" device the rest of
        # the server entities are attached to so they group together.
        return DeviceInfo(
            identifiers={
                (DOMAIN, self.coordinator.machine_identifier)
            },
        )

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data
        if data is None:
            return None
        if self._scope == "total":
            return data.total_kbps
        if self._scope == "lan":
            return data.lan_kbps
        return data.wan_kbps


class PlexServerVersionSensor(PlexServerEntity, SensorEntity):
    _attr_translation_key = "version"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:server"

    def __init__(self, coordinator: PlexServerCoordinator) -> None:
        super().__init__(coordinator, "version")

    @property
    def native_value(self) -> str:
        return self.coordinator.data.version


class PlexLibraryItemCountSensor(PlexLibraryEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "items"

    def __init__(
        self, coordinator: PlexLibraryCoordinator, library: PlexLibrary
    ) -> None:
        super().__init__(coordinator, library.key, "items")
        self._attr_name = library.title
        self._attr_icon = _LIBRARY_ICONS.get(library.type, "mdi:bookshelf")

    @property
    def native_value(self) -> int | None:
        library = (self.coordinator.data or {}).get(self._library_key)
        return library.item_count if library else None

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and self._library_key in self.coordinator.data
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        library = (self.coordinator.data or {}).get(self._library_key)
        if library is None:
            return None
        return {"type": library.type, "section_id": library.section_id}


class PlexUserNowPlayingSensor(
    CoordinatorEntity[PlexAccountCoordinator], SensorEntity
):
    """Per-user 'now playing' sensor.

    Subscribes to the account coordinator (for user metadata + availability)
    and additionally to every server coordinator so state updates whenever
    any monitored server's session list changes.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "now_playing"
    _attr_icon = "mdi:account-music"

    def __init__(
        self,
        account_coordinator: PlexAccountCoordinator,
        server_coordinators: dict[str, PlexServerCoordinator],
        account_uuid: str,
        user_id: str,
    ) -> None:
        super().__init__(account_coordinator)
        self._server_coordinators = server_coordinators
        self._account_uuid = account_uuid
        self._user_id = user_id
        self._attr_unique_id = (
            f"{account_uuid}_user_{user_id}_now_playing"
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        for coord in self._server_coordinators.values():
            self.async_on_remove(
                coord.async_add_listener(self.async_write_ha_state)
            )

    @property
    def _user(self) -> PlexUser | None:
        return (self.coordinator.data or {}).get(self._user_id)

    def _ordered_sessions(self) -> list[PlexSession]:
        """All of this user's active sessions, most-recently-started first."""
        sessions: list[PlexSession] = []
        for coord in self._server_coordinators.values():
            if not coord.last_update_success or coord.data is None:
                continue
            for session in coord.data.sessions:
                if session.user.user_id == self._user_id:
                    sessions.append(session)
        sessions.sort(key=lambda s: s.started_at or _OLDEST, reverse=True)
        return sessions

    @property
    def device_info(self) -> DeviceInfo:
        user = self._user
        return DeviceInfo(
            identifiers={
                (DOMAIN, f"user_{self._account_uuid}_{self._user_id}")
            },
            name=user.title if user else f"Plex user {self._user_id}",
            manufacturer="Plex",
            model="Plex user",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def entity_picture(self) -> str | None:
        user = self._user
        return user.thumb if user else None

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self._user is not None

    @property
    def native_value(self) -> str:
        sessions = self._ordered_sessions()
        if not sessions:
            return USER_STATE_IDLE
        return _session_summary(sessions[0])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        user = self._user
        sessions = self._ordered_sessions()
        base: dict[str, Any] = {
            "username": user.username if user else self._user_id,
            "user_id": self._user_id,
            "is_owner": bool(user and user.is_owner),
            "is_home_user": bool(user and user.is_home_user),
            "email": user.email if user else None,
            "session_count": len(sessions),
            "state": _aggregate_state(sessions),
            "sessions": [session_to_payload(s) for s in sessions],
        }
        if not sessions:
            return base

        # Top-level fields mirror the primary (most-recently-started) session
        # so existing templates keyed on attributes.title etc. keep working.
        primary = sessions[0]
        content = primary.content
        base.update(
            {
                "type": content.type,
                "title": content.title,
                "show": content.show_title,
                "season": content.season_number,
                "episode": content.episode_number,
                "artist": content.artist,
                "album": content.album,
                "library": content.library,
                "year": content.year,
                "summary": content.summary,
                "content_rating": content.content_rating,
                "duration_ms": content.duration_ms,
                "progress_ms": content.view_offset_ms,
                "progress_percent": _progress_percent(content),
                "thumb_url": content.thumb_url,
                "art_url": content.art_url,
                "guid": content.guid,
                "player": primary.player.title,
                "player_product": primary.player.product,
                "player_platform": primary.player.platform,
                "player_device": primary.player.device,
                "local": primary.player.local,
                "transcoding": primary.transcode is not None,
                "bitrate_kbps": primary.bitrate_kbps,
                "server": primary.server_machine_identifier,
                "started_at": (
                    primary.started_at.isoformat() if primary.started_at else None
                ),
            }
        )
        return base


def _progress_percent(content) -> float | None:
    if not content.duration_ms or not content.view_offset_ms:
        return None
    pct = (content.view_offset_ms / content.duration_ms) * 100
    return round(pct, 1)


def _aggregate_state(sessions: list[PlexSession]) -> str:
    """Roll up multiple session states for the user-level ``state`` attribute.

    Priority: any session playing → ``playing``; else any buffering →
    ``buffering``; else any paused → ``paused``; else ``idle``.
    """
    if not sessions:
        return "idle"
    states = {s.player.state for s in sessions}
    for candidate in ("playing", "buffering", "paused"):
        if candidate in states:
            return candidate
    return "idle"
