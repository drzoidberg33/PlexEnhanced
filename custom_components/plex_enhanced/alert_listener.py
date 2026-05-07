"""Push-side plumbing: plex.tv websocket alerts → coordinator + HA bus events.

Two collaborating pieces:

* :class:`SessionEventEmitter` subscribes to a server coordinator and fires
  ``plex_enhanced_playback_*`` events on the HA bus whenever the snapshot
  diff implies a state transition. It's intentionally agnostic about *why*
  the coordinator just refreshed — works identically for poll-driven and
  push-driven updates.
* :class:`PlexAlertListener` opens a websocket via plexapi for low-latency
  pushes from the Plex Media Server. Its job is two-fold: nudge the server
  coordinator to refresh ASAP on ``playing`` events, and fire
  ``plex_enhanced_library_new`` for fresh library items.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback

from .const import (
    EVENT_LIBRARY_NEW,
    EVENT_PLAYBACK_PAUSED,
    EVENT_PLAYBACK_RESUMED,
    EVENT_PLAYBACK_STARTED,
    EVENT_PLAYBACK_STOPPED,
)
from .coordinator import PlexServerCoordinator, _resolve_image_url
from .models import PlexSession

_LOGGER = logging.getLogger(__name__)

# Plex timeline state codes — 5 == playback/import finished.
_TIMELINE_STATE_FINISHED = 5
_METADATA_STATE_CREATED = "created"


def _session_payload(session: PlexSession) -> dict[str, Any]:
    """Flatten a PlexSession into a JSON-friendly bus event payload."""
    content = session.content
    return {
        "server": session.server_machine_identifier,
        "session_key": session.session_key,
        "user": {
            "user_id": session.user.user_id,
            "username": session.user.username,
            "title": session.user.title,
        },
        "player": {
            "machine_identifier": session.player.machine_identifier,
            "title": session.player.title,
            "product": session.player.product,
            "platform": session.player.platform,
            "device": session.player.device,
            "local": session.player.local,
            "state": session.player.state,
        },
        "content": {
            "type": content.type,
            "title": content.title,
            "show": content.show_title,
            "season": content.season_number,
            "episode": content.episode_number,
            "artist": content.artist,
            "album": content.album,
            "library": content.library,
            "year": content.year,
            "guid": content.guid,
            "duration_ms": content.duration_ms,
            "view_offset_ms": content.view_offset_ms,
            "thumb_url": content.thumb_url,
        },
        "transcoding": session.transcode is not None,
        "bitrate_kbps": session.bitrate_kbps,
        "started_at": (
            session.started_at.isoformat() if session.started_at else None
        ),
    }


class SessionEventEmitter:
    """Diff coordinator snapshots and fire HA bus events on transitions."""

    def __init__(
        self, hass: HomeAssistant, coordinator: PlexServerCoordinator
    ) -> None:
        self.hass = hass
        self.coordinator = coordinator
        # Prime with whatever sessions exist now so we don't fire spurious
        # 'started' events for sessions that pre-date HA startup.
        self._previous: dict[str, PlexSession] = {}
        if coordinator.data is not None:
            self._previous = {
                s.session_key: s for s in coordinator.data.sessions
            }
        self._unsub = coordinator.async_add_listener(self._on_update)

    def stop(self) -> None:
        self._unsub()

    @callback
    def _on_update(self) -> None:
        if self.coordinator.data is None:
            return
        current = {s.session_key: s for s in self.coordinator.data.sessions}

        for key, session in current.items():
            prev = self._previous.get(key)
            if prev is None:
                self._fire(EVENT_PLAYBACK_STARTED, session)
                continue
            if prev.player.state == session.player.state:
                continue
            new_state = session.player.state
            if new_state == "paused":
                self._fire(EVENT_PLAYBACK_PAUSED, session)
            elif new_state == "playing" and prev.player.state == "paused":
                self._fire(EVENT_PLAYBACK_RESUMED, session)

        for key, prev in self._previous.items():
            if key not in current:
                self._fire(EVENT_PLAYBACK_STOPPED, prev)

        self._previous = current

    def _fire(self, event_name: str, session: PlexSession) -> None:
        self.hass.bus.async_fire(event_name, _session_payload(session))


class PlexAlertListener:
    """Websocket bridge from PMS to the coordinator + library-new event."""

    def __init__(
        self, hass: HomeAssistant, coordinator: PlexServerCoordinator
    ) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self._handle = None  # plexapi.alert.AlertListener

    async def async_start(self) -> None:
        try:
            self._handle = await self.hass.async_add_executor_job(
                self.coordinator.server.startAlertListener, self._on_alert
            )
        except Exception as err:  # noqa: BLE001 - websocket failures vary
            _LOGGER.warning(
                "Alert listener could not connect to %s — falling back to "
                "polling only: %s",
                self.coordinator.display_name,
                err,
            )
            self._handle = None

    async def async_stop(self) -> None:
        if self._handle is None:
            return
        try:
            await self.hass.async_add_executor_job(self._handle.stop)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Alert listener stop on %s raised: %s",
                self.coordinator.display_name,
                err,
            )
        self._handle = None

    def is_connected(self) -> bool:
        """Best-effort liveness check on the websocket worker thread."""
        if self._handle is None:
            return False
        is_alive = getattr(self._handle, "is_alive", None)
        if callable(is_alive):
            return bool(is_alive())
        return True

    # ------------------------------------------------------------- callbacks

    def _on_alert(self, message: dict[str, Any]) -> None:
        """Runs in plexapi's websocket thread — must hop to the loop."""
        msg_type = message.get("type")
        if msg_type == "playing":
            # Any playback state change → request a coordinator refresh,
            # which in turn drives SessionEventEmitter via async_add_listener.
            self.hass.add_job(self.coordinator.async_request_refresh())
            return

        if msg_type == "timeline":
            for note in message.get("TimelineEntry", []) or []:
                if (
                    note.get("state") == _TIMELINE_STATE_FINISHED
                    and note.get("metadataState") == _METADATA_STATE_CREATED
                ):
                    self.hass.add_job(self._async_emit_library_new(note))

    async def _async_emit_library_new(self, note: dict[str, Any]) -> None:
        item_id = note.get("itemID")
        section_id = note.get("sectionID")
        item_type = note.get("type")

        info: dict[str, Any] = {}
        if item_id:
            info = await self.hass.async_add_executor_job(
                self._fetch_item_info, item_id
            )

        self.hass.bus.async_fire(
            EVENT_LIBRARY_NEW,
            {
                "server": self.coordinator.machine_identifier,
                "item_id": item_id,
                "section_id": section_id,
                "type": item_type,
                **info,
            },
        )

    def _fetch_item_info(self, item_id: Any) -> dict[str, Any]:
        try:
            item = self.coordinator.server.fetchItem(int(item_id))
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not fetch new item %s: %s", item_id, err)
            return {}
        return {
            "title": getattr(item, "title", None),
            "library": getattr(item, "librarySectionTitle", None),
            "year": getattr(item, "year", None),
            "summary": getattr(item, "summary", None),
            "guid": getattr(item, "guid", None),
            "thumb_url": _resolve_image_url(
                getattr(item, "thumb", None), self.coordinator.server
            ),
            "show": getattr(item, "grandparentTitle", None),
            "season": getattr(item, "parentIndex", None),
            "episode": getattr(item, "index", None),
        }
