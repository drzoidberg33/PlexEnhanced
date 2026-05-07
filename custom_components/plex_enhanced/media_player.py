"""Media player entities for Plex clients."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PlexEnhancedRuntime, PlexServerCoordinator
from .models import PlexClientInfo, PlexSession

_LOGGER = logging.getLogger(__name__)


_PLEX_TO_HA_STATE = {
    "playing": MediaPlayerState.PLAYING,
    "paused": MediaPlayerState.PAUSED,
    "buffering": MediaPlayerState.BUFFERING,
    "stopped": MediaPlayerState.IDLE,
}

_PLEX_TO_HA_MEDIA_TYPE = {
    "movie": MediaType.MOVIE,
    "episode": MediaType.TVSHOW,
    "track": MediaType.MUSIC,
    "clip": MediaType.VIDEO,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: PlexEnhancedRuntime = entry.runtime_data
    account_uuid = runtime.account.uuid
    known_keys: set[tuple[str, str]] = set()

    @callback
    def _add_new_clients() -> None:
        new_entities: list[MediaPlayerEntity] = []
        for server_coord in runtime.server_coordinators.values():
            if server_coord.data is None:
                continue
            for client in server_coord.data.clients:
                key = (server_coord.machine_identifier, client.machine_identifier)
                if key in known_keys:
                    continue
                known_keys.add(key)
                new_entities.append(
                    PlexClientMediaPlayer(server_coord, client, account_uuid)
                )
        if new_entities:
            async_add_entities(new_entities)

    _add_new_clients()
    for coord in runtime.server_coordinators.values():
        entry.async_on_unload(coord.async_add_listener(_add_new_clients))


class PlexClientMediaPlayer(
    CoordinatorEntity[PlexServerCoordinator], MediaPlayerEntity
):
    """A Plex client surfaced as an HA media_player.

    State and media metadata are derived from the parent server coordinator's
    snapshot of sessions; remote control commands are dispatched via plexapi
    in an executor thread.
    """

    _attr_has_entity_name = True
    _attr_name = None  # use the device name only

    def __init__(
        self,
        coordinator: PlexServerCoordinator,
        client_info: PlexClientInfo,
        account_uuid: str,
    ) -> None:
        super().__init__(coordinator)
        self._account_uuid = account_uuid
        self._machine_identifier = client_info.machine_identifier
        self._fallback_info = client_info
        self._attr_unique_id = (
            f"{account_uuid}_client_{coordinator.machine_identifier}_"
            f"{client_info.machine_identifier}"
        )

    # ------------------------------------------------------------------ helpers

    def _info(self) -> PlexClientInfo:
        if self.coordinator.data is None:
            return self._fallback_info
        for client in self.coordinator.data.clients:
            if client.machine_identifier == self._machine_identifier:
                return client
        return self._fallback_info

    def _session(self) -> PlexSession | None:
        if self.coordinator.data is None:
            return None
        for session in self.coordinator.data.sessions:
            if session.player.machine_identifier == self._machine_identifier:
                return session
        return None

    def _is_currently_known(self) -> bool:
        if self.coordinator.data is None:
            return False
        for client in self.coordinator.data.clients:
            if client.machine_identifier == self._machine_identifier:
                return True
        return self._session() is not None

    # ------------------------------------------------------------------ entity

    @property
    def device_info(self) -> DeviceInfo:
        info = self._info()
        return DeviceInfo(
            identifiers={
                (DOMAIN, f"client_{self._account_uuid}_{self._machine_identifier}")
            },
            name=info.title,
            manufacturer="Plex",
            model=info.product,
            sw_version=info.platform,
        )

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        feats = (
            MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.STOP
            | MediaPlayerEntityFeature.PLAY_MEDIA
        )
        caps = set(self._info().protocol_capabilities)
        if "playback" in caps:
            feats |= (
                MediaPlayerEntityFeature.SEEK
                | MediaPlayerEntityFeature.NEXT_TRACK
                | MediaPlayerEntityFeature.PREVIOUS_TRACK
                | MediaPlayerEntityFeature.VOLUME_SET
                | MediaPlayerEntityFeature.VOLUME_MUTE
            )
        return feats

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self._is_currently_known()

    @property
    def state(self) -> MediaPlayerState:
        session = self._session()
        if session is None:
            return (
                MediaPlayerState.IDLE
                if self._is_currently_known()
                else MediaPlayerState.OFF
            )
        return _PLEX_TO_HA_STATE.get(session.player.state, MediaPlayerState.IDLE)

    # ------------------------------------------------------------- media meta

    @property
    def media_content_type(self) -> str | None:
        session = self._session()
        if session is None:
            return None
        return _PLEX_TO_HA_MEDIA_TYPE.get(session.content.type)

    @property
    def media_title(self) -> str | None:
        session = self._session()
        return session.content.title if session else None

    @property
    def media_series_title(self) -> str | None:
        session = self._session()
        return session.content.show_title if session else None

    @property
    def media_season(self) -> int | None:
        session = self._session()
        return session.content.season_number if session else None

    @property
    def media_episode(self) -> int | None:
        session = self._session()
        return session.content.episode_number if session else None

    @property
    def media_artist(self) -> str | None:
        session = self._session()
        return session.content.artist if session else None

    @property
    def media_album_name(self) -> str | None:
        session = self._session()
        return session.content.album if session else None

    @property
    def media_duration(self) -> int | None:
        session = self._session()
        if session is None or not session.content.duration_ms:
            return None
        return session.content.duration_ms // 1000

    @property
    def media_position(self) -> int | None:
        session = self._session()
        if session is None or session.content.view_offset_ms is None:
            return None
        return session.content.view_offset_ms // 1000

    @property
    def media_position_updated_at(self):
        return self.coordinator.data.fetched_at if self.coordinator.data else None

    @property
    def media_image_url(self) -> str | None:
        session = self._session()
        return session.content.thumb_url if session else None

    @property
    def media_image_remotely_accessible(self) -> bool:
        return False  # Plex thumbs require an auth token; HA will proxy them.

    @property
    def media_content_id(self) -> str | None:
        session = self._session()
        return session.content.guid if session else None

    @property
    def app_name(self) -> str | None:
        return self._info().product

    # ------------------------------------------------------------- commands

    async def async_media_play(self) -> None:
        await self._invoke("play")

    async def async_media_pause(self) -> None:
        await self._invoke("pause")

    async def async_media_stop(self) -> None:
        await self._invoke("stop")

    async def async_media_seek(self, position: float) -> None:
        await self._invoke("seekTo", int(position * 1000))

    async def async_media_next_track(self) -> None:
        await self._invoke("skipNext")

    async def async_media_previous_track(self) -> None:
        await self._invoke("skipPrevious")

    async def async_set_volume_level(self, volume: float) -> None:
        await self._invoke("setVolume", int(volume * 100))

    async def async_mute_volume(self, mute: bool) -> None:
        await self._invoke("setVolume", 0 if mute else 100)

    async def async_play_media(
        self, media_type: str, media_id: str, **kwargs: Any
    ) -> None:
        def _do() -> None:
            client = self._lookup_plex_client()
            if client is None:
                raise HomeAssistantError(
                    f"Plex client {self._machine_identifier} is not currently reachable"
                )
            try:
                ekey: int | str = int(media_id)
            except ValueError:
                ekey = media_id
            try:
                item = self.coordinator.server.fetchItem(ekey)
            except Exception as err:  # noqa: BLE001
                raise HomeAssistantError(
                    f"Could not resolve Plex item '{media_id}': {err}"
                ) from err
            client.playMedia(item)

        await self.hass.async_add_executor_job(_do)

    # ------------------------------------------------------------- internals

    async def _invoke(self, method: str, *args: Any) -> None:
        def _do() -> None:
            client = self._lookup_plex_client()
            if client is None:
                raise HomeAssistantError(
                    f"Plex client {self._machine_identifier} is not currently reachable"
                )
            try:
                getattr(client, method)(*args)
            except Exception as err:  # noqa: BLE001
                raise HomeAssistantError(
                    f"Plex client did not accept '{method}': {err}"
                ) from err

        await self.hass.async_add_executor_job(_do)

    def _lookup_plex_client(self):
        try:
            for client in self.coordinator.server.clients():
                if client.machineIdentifier == self._machine_identifier:
                    return client
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not enumerate Plex clients: %s", err)
        return None
