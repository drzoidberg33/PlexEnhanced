"""Constants for the Plex Enhanced integration."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "plex_enhanced"

CONF_SERVERS = "servers"
CONF_CLIENT_IDENTIFIER = "client_identifier"

DEFAULT_SCAN_INTERVAL = 30

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.MEDIA_PLAYER,
    Platform.SENSOR,
]

SIGNAL_SESSION_UPDATE = f"{DOMAIN}_session_update"
SIGNAL_NEW_CLIENT = f"{DOMAIN}_new_client"
SIGNAL_NEW_USER = f"{DOMAIN}_new_user"
SIGNAL_NEW_LIBRARY = f"{DOMAIN}_new_library"

EVENT_PLAYBACK_STARTED = f"{DOMAIN}_playback_started"
EVENT_PLAYBACK_STOPPED = f"{DOMAIN}_playback_stopped"
EVENT_PLAYBACK_PAUSED = f"{DOMAIN}_playback_paused"
EVENT_PLAYBACK_RESUMED = f"{DOMAIN}_playback_resumed"
EVENT_LIBRARY_NEW = f"{DOMAIN}_library_new"

PLEX_TV_PIN_HEADERS = {
    "X-Plex-Product": "Plex Enhanced for Home Assistant",
    "X-Plex-Device-Name": "Home Assistant",
    "X-Plex-Platform": "Home Assistant",
}
