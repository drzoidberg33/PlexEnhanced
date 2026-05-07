"""Diagnostics for Plex Enhanced.

Returns a redacted snapshot of runtime state for the "Download diagnostics"
button in the integration page. Sensitive fields (token, email, IPs, content
GUIDs) are scrubbed via :func:`async_redact_data`.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import PlexEnhancedRuntime

REDACT_KEYS = {
    "token",
    "client_identifier",
    "email",
    "address",
    "thumb",
    "thumb_url",
    "art_url",
    "guid",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "unique_id": entry.unique_id,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
    }

    runtime: PlexEnhancedRuntime | None = getattr(entry, "runtime_data", None)
    if runtime is None:
        return async_redact_data(payload, REDACT_KEYS)

    payload["account"] = {
        "uuid": runtime.account.uuid,
        "username": runtime.account.username,
        "title": runtime.account.title,
        "email": runtime.account.email,
    }

    payload["users"] = [
        {
            "user_id": user.user_id,
            "username": user.username,
            "title": user.title,
            "email": user.email,
            "is_owner": user.is_owner,
            "is_home_user": user.is_home_user,
        }
        for user in (runtime.account_coordinator.data or {}).values()
    ]

    payload["servers"] = []
    for machine_id, server_coord in runtime.server_coordinators.items():
        data = server_coord.data
        listener = runtime.alert_listeners.get(machine_id)
        library_coord = runtime.library_coordinators.get(machine_id)
        bandwidth_coord = runtime.bandwidth_coordinators.get(machine_id)
        bandwidth = bandwidth_coord.data if bandwidth_coord else None
        payload["servers"].append(
            {
                "machine_identifier": machine_id,
                "name": server_coord.display_name,
                "version": data.version if data else None,
                "platform": data.platform if data else None,
                "session_count": len(data.sessions) if data else 0,
                "transcode_count": (
                    data.transcode_session_count if data else 0
                ),
                "bandwidth_kbps": {
                    "total": bandwidth.total_kbps if bandwidth else 0,
                    "lan": bandwidth.lan_kbps if bandwidth else 0,
                    "wan": bandwidth.wan_kbps if bandwidth else 0,
                },
                "bandwidth_last_poll_at": (
                    bandwidth.fetched_at.isoformat()
                    if bandwidth and bandwidth.fetched_at
                    else None
                ),
                "bandwidth_last_poll_success": (
                    bandwidth_coord.last_update_success
                    if bandwidth_coord
                    else None
                ),
                "client_count": len(data.clients) if data else 0,
                "library_count": (
                    len(library_coord.data or {}) if library_coord else 0
                ),
                "alert_listener_connected": (
                    listener.is_connected() if listener is not None else False
                ),
                "last_poll_success": server_coord.last_update_success,
                "last_poll_at": (
                    data.fetched_at.isoformat()
                    if data and data.fetched_at
                    else None
                ),
                "library_update_success": (
                    library_coord.last_update_success
                    if library_coord
                    else None
                ),
            }
        )

    payload["sessions"] = [
        {
            "server": session.server_machine_identifier,
            "session_key": session.session_key,
            "user_id": session.user.user_id,
            "username": session.user.username,
            "player": session.player.title,
            "product": session.player.product,
            "platform": session.player.platform,
            "state": session.player.state,
            "local": session.player.local,
            "content_type": session.content.type,
            "content_title": session.content.title,
            "show": session.content.show_title,
            "library": session.content.library,
            "transcoding": session.transcode is not None,
            "bitrate_kbps": session.bitrate_kbps,
        }
        for coord in runtime.server_coordinators.values()
        if coord.data is not None
        for session in coord.data.sessions
    ]

    return async_redact_data(payload, REDACT_KEYS)
