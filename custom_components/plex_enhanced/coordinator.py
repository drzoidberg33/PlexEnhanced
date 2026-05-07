"""DataUpdateCoordinators for Plex Enhanced.

There are two layers of polling per config entry:

* :class:`PlexAccountCoordinator` (one per entry) refreshes the universe of
  users with access to the monitored owned servers. Users change rarely, so
  the interval is hour-scale.
* :class:`PlexServerCoordinator` (one per selected server) polls the live
  state — sessions, bandwidth, server availability — every 30 s.

Push updates from the plexapi alert listener will land in phase 7 and bypass
the per-server poll for low-latency state changes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from plexapi.exceptions import Unauthorized
from plexapi.myplex import MyPlexAccount
from plexapi.server import PlexServer

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .models import (
    PlexClientInfo,
    PlexContent,
    PlexLibrary,
    PlexPlayer,
    PlexSession,
    PlexServerData,
    PlexTranscode,
    PlexUser,
)

_LOGGER = logging.getLogger(__name__)

ACCOUNT_UPDATE_INTERVAL = timedelta(hours=1)
LIBRARY_UPDATE_INTERVAL = timedelta(minutes=5)


@dataclass
class PlexEnhancedRuntime:
    """Per-config-entry runtime data attached to ``entry.runtime_data``."""

    account: MyPlexAccount
    account_coordinator: "PlexAccountCoordinator"
    server_coordinators: dict[str, "PlexServerCoordinator"] = field(default_factory=dict)
    library_coordinators: dict[str, "PlexLibraryCoordinator"] = field(default_factory=dict)
    # Populated in __init__.async_setup_entry (avoids importing alert_listener here).
    alert_listeners: dict[str, Any] = field(default_factory=dict)
    session_emitters: dict[str, Any] = field(default_factory=dict)


PlexEnhancedConfigEntry = ConfigEntry  # type: ignore[type-arg]
# Once HA min is comfortably 2024.5+, switch to:
#   PlexEnhancedConfigEntry = ConfigEntry[PlexEnhancedRuntime]


class PlexAccountCoordinator(DataUpdateCoordinator[dict[str, PlexUser]]):
    """Refreshes the user list for the account, scoped to monitored servers."""

    def __init__(
        self,
        hass: HomeAssistant,
        account: MyPlexAccount,
        owned_server_ids: set[str],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_account",
            update_interval=ACCOUNT_UPDATE_INTERVAL,
        )
        self._account = account
        self._owned_server_ids = owned_server_ids

    async def _async_update_data(self) -> dict[str, PlexUser]:
        try:
            return await self.hass.async_add_executor_job(self._fetch_blocking)
        except Unauthorized as err:
            raise ConfigEntryAuthFailed("Plex token rejected") from err
        except Exception as err:
            raise UpdateFailed(f"Account refresh failed: {err}") from err

    def _fetch_blocking(self) -> dict[str, PlexUser]:
        users: dict[str, PlexUser] = {}
        owner_id = str(self._account.id)
        users[owner_id] = PlexUser(
            user_id=owner_id,
            username=self._account.username or self._account.email or "owner",
            title=self._account.title or self._account.username or "Owner",
            email=self._account.email,
            thumb=self._account.thumb,
            is_owner=True,
        )
        for friend in self._account.users():
            shares = {
                str(s.machineIdentifier) for s in (friend.servers or [])
            }
            if not (shares & self._owned_server_ids):
                continue
            users[str(friend.id)] = PlexUser(
                user_id=str(friend.id),
                username=friend.username or friend.title or "user",
                title=friend.title or friend.username or "Plex user",
                email=friend.email,
                thumb=friend.thumb,
                is_home_user=bool(getattr(friend, "home", False)),
            )
        return users


class PlexServerCoordinator(DataUpdateCoordinator[PlexServerData]):
    """Polls a single Plex Media Server for sessions and bandwidth."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        server: PlexServer,
        account: MyPlexAccount,
        display_name: str,
        account_coordinator: "PlexAccountCoordinator | None" = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}[{display_name}]",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.server = server
        self.account = account
        self.display_name = display_name
        self.machine_identifier = server.machineIdentifier
        self.entry = entry
        self.account_coordinator = account_coordinator
        self._session_first_seen: dict[str, datetime] = {}

    async def _async_update_data(self) -> PlexServerData:
        try:
            data = await self.hass.async_add_executor_job(self._fetch_blocking)
        except Unauthorized as err:
            raise ConfigEntryAuthFailed("Plex token rejected") from err
        except Exception as err:
            raise UpdateFailed(f"Polling {self.display_name} failed: {err}") from err
        # If an unknown user shows up in a session (e.g. friend granted access
        # mid-day), prompt the account coordinator for an out-of-cycle refresh
        # so the user sensor materialises within seconds rather than ~1 hour.
        if self.account_coordinator is not None:
            known = set((self.account_coordinator.data or {}).keys())
            seen = {s.user.user_id for s in data.sessions}
            if seen - known:
                self.hass.async_create_task(
                    self.account_coordinator.async_request_refresh()
                )
        return data

    def _fetch_blocking(self) -> PlexServerData:
        now = dt_util.utcnow()
        sessions_raw = self.server.sessions()

        sessions: list[PlexSession] = []
        seen_keys: set[str] = set()
        for raw in sessions_raw:
            session_key = str(raw.sessionKey)
            seen_keys.add(session_key)
            started_at = self._session_first_seen.setdefault(session_key, now)
            sessions.append(
                _build_session(
                    raw,
                    self.server,
                    self.machine_identifier,
                    session_key,
                    started_at,
                )
            )
        # Drop bookkeeping for sessions that have ended.
        self._session_first_seen = {
            k: v for k, v in self._session_first_seen.items() if k in seen_keys
        }

        # Clients: registered remote-controllable devices. Failure here must
        # not break session/bandwidth tracking, so we default to empty.
        try:
            clients_raw = self.server.clients()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not fetch clients on %s: %s", self.display_name, err)
            clients_raw = []
        clients = tuple(_build_client(c) for c in clients_raw)

        bandwidth_lan = sum(s.bitrate_kbps for s in sessions if s.player.local)
        bandwidth_wan = sum(s.bitrate_kbps for s in sessions if not s.player.local)
        transcode_count = sum(1 for s in sessions if s.transcode is not None)

        return PlexServerData(
            machine_identifier=self.machine_identifier,
            name=self.server.friendlyName or self.display_name,
            version=self.server.version,
            platform=self.server.platform or "",
            online=True,
            sessions=tuple(sessions),
            clients=clients,
            bandwidth_total_kbps=bandwidth_lan + bandwidth_wan,
            bandwidth_lan_kbps=bandwidth_lan,
            bandwidth_wan_kbps=bandwidth_wan,
            transcode_session_count=transcode_count,
            fetched_at=now,
        )


class PlexLibraryCoordinator(DataUpdateCoordinator[dict[str, PlexLibrary]]):
    """Polls library sections (item counts) on a slower cadence."""

    def __init__(
        self,
        hass: HomeAssistant,
        server_coordinator: PlexServerCoordinator,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}[{server_coordinator.display_name}_libraries]",
            update_interval=LIBRARY_UPDATE_INTERVAL,
        )
        self._server_coordinator = server_coordinator

    @property
    def server(self) -> PlexServer:
        return self._server_coordinator.server

    @property
    def machine_identifier(self) -> str:
        return self._server_coordinator.machine_identifier

    @property
    def display_name(self) -> str:
        return self._server_coordinator.display_name

    async def _async_update_data(self) -> dict[str, PlexLibrary]:
        try:
            return await self.hass.async_add_executor_job(self._fetch_blocking)
        except Unauthorized as err:
            raise ConfigEntryAuthFailed("Plex token rejected") from err
        except Exception as err:
            raise UpdateFailed(
                f"Library refresh for {self.display_name} failed: {err}"
            ) from err

    def _fetch_blocking(self) -> dict[str, PlexLibrary]:
        result: dict[str, PlexLibrary] = {}
        for section in self.server.library.sections():
            try:
                count = int(section.totalSize or 0)
            except Exception:  # noqa: BLE001 - section may be empty / inaccessible
                count = 0
            key = str(section.key)
            result[key] = PlexLibrary(
                section_id=int(section.key),
                key=key,
                title=section.title,
                type=section.type,
                item_count=count,
            )
        return result


# ---------------------------------------------------------------------------
# Helpers: plexapi → dataclass conversion
# ---------------------------------------------------------------------------


def _build_session(
    raw,
    server: PlexServer,
    machine_id: str,
    session_key: str,
    started_at: datetime,
) -> PlexSession:
    """Translate one plexapi session object into our dataclass form."""
    return PlexSession(
        session_key=session_key,
        server_machine_identifier=machine_id,
        user=_build_user_from_session(raw),
        player=_build_player_from_session(raw),
        content=_build_content_from_session(raw, server),
        transcode=_build_transcode_from_session(raw),
        bitrate_kbps=_session_bitrate(raw),
        started_at=started_at,
    )


def _build_user_from_session(raw) -> PlexUser:
    user = getattr(raw, "user", None)
    if user is None:
        username = (raw.usernames or ["unknown"])[0]
        return PlexUser(
            user_id=f"unknown-{username}", username=username, title=username
        )
    return PlexUser(
        user_id=str(user.id),
        username=user.username or user.title or "user",
        title=user.title or user.username or "Plex user",
        thumb=user.thumb,
    )


def _build_player_from_session(raw) -> PlexPlayer:
    player = raw.players[0] if raw.players else None
    if player is None:
        return PlexPlayer(
            machine_identifier="unknown", title="Unknown", product="Unknown"
        )
    return PlexPlayer(
        machine_identifier=player.machineIdentifier or "",
        title=player.title or "Unknown",
        product=player.product or "Unknown",
        platform=player.platform,
        device=player.device,
        address=player.address,
        local=bool(getattr(player, "local", True)),
        state=player.state or "stopped",
    )


def _build_content_from_session(raw, server: PlexServer) -> PlexContent:
    content_type = raw.type
    common = dict(
        type=content_type,
        title=raw.title or "",
        library=getattr(raw, "librarySectionTitle", None),
        duration_ms=getattr(raw, "duration", None),
        view_offset_ms=getattr(raw, "viewOffset", None),
        year=getattr(raw, "year", None),
        summary=getattr(raw, "summary", None),
        content_rating=getattr(raw, "contentRating", None),
        thumb_url=_resolve_image_url(getattr(raw, "thumb", None), server),
        art_url=_resolve_image_url(getattr(raw, "art", None), server),
        guid=getattr(raw, "guid", None),
    )
    if content_type == "episode":
        return PlexContent(
            **common,
            show_title=getattr(raw, "grandparentTitle", None),
            season_number=getattr(raw, "parentIndex", None),
            episode_number=getattr(raw, "index", None),
        )
    if content_type == "track":
        return PlexContent(
            **common,
            artist=getattr(raw, "grandparentTitle", None),
            album=getattr(raw, "parentTitle", None),
        )
    return PlexContent(**common)


def _build_transcode_from_session(raw) -> PlexTranscode | None:
    transcodes = getattr(raw, "transcodeSessions", None) or []
    if not transcodes:
        return None
    t = transcodes[0]
    return PlexTranscode(
        video_decision=getattr(t, "videoDecision", None),
        audio_decision=getattr(t, "audioDecision", None),
        container=getattr(t, "container", None),
        target_video_codec=getattr(t, "videoCodec", None),
        target_audio_codec=getattr(t, "audioCodec", None),
        source_video_codec=getattr(t, "sourceVideoCodec", None),
        source_audio_codec=getattr(t, "sourceAudioCodec", None),
        progress=getattr(t, "progress", None),
        throttled=bool(getattr(t, "throttled", False)),
        speed=getattr(t, "speed", None),
    )


def _session_bitrate(raw) -> int:
    transcodes = getattr(raw, "transcodeSessions", None) or []
    if transcodes and getattr(transcodes[0], "bitrate", None):
        return int(transcodes[0].bitrate)
    media = getattr(raw, "media", None) or []
    if media and getattr(media[0], "bitrate", None):
        return int(media[0].bitrate)
    return 0


def _resolve_image_url(path: str | None, server: PlexServer) -> str | None:
    if not path:
        return None
    try:
        return server.url(path, includeToken=True)
    except Exception:  # noqa: BLE001 - plexapi raises various types here
        return None


def _build_client(raw) -> PlexClientInfo:
    capabilities_raw = getattr(raw, "protocolCapabilities", "") or ""
    capabilities = tuple(
        cap.strip() for cap in capabilities_raw.split(",") if cap.strip()
    )
    return PlexClientInfo(
        machine_identifier=getattr(raw, "machineIdentifier", "") or "",
        title=getattr(raw, "title", None) or "Plex client",
        product=getattr(raw, "product", None) or "Plex",
        platform=getattr(raw, "platform", None),
        device=getattr(raw, "device", None),
        address=getattr(raw, "address", None),
        protocol_capabilities=capabilities,
    )
