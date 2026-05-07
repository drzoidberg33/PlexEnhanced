"""The Plex Enhanced integration."""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from plexapi.exceptions import Unauthorized
from plexapi.myplex import MyPlexAccount

from .alert_listener import PlexAlertListener, SessionEventEmitter
from .const import CONF_CLIENT_IDENTIFIER, CONF_SERVERS, DOMAIN, PLATFORMS
from .coordinator import (
    PlexAccountCoordinator,
    PlexBandwidthCoordinator,
    PlexEnhancedRuntime,
    PlexLibraryCoordinator,
    PlexServerCoordinator,
)
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)

CARD_FILE_NAME = "plex-enhanced-sessions-card.js"
CARD_URL_PATH = f"/{DOMAIN}_static"
_CARD_REGISTERED_KEY = f"{DOMAIN}_card_registered"


async def _async_register_frontend_card(hass: HomeAssistant) -> None:
    """Serve and auto-load the bundled Lovelace card. Idempotent across entries."""
    if hass.data.get(_CARD_REGISTERED_KEY):
        return
    hass.data[_CARD_REGISTERED_KEY] = True

    www_path = Path(__file__).parent / "www"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL_PATH, str(www_path), cache_headers=False)]
    )
    frontend.add_extra_js_url(hass, f"{CARD_URL_PATH}/{CARD_FILE_NAME}")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Plex Enhanced from a config entry."""
    token = entry.data[CONF_TOKEN]
    selected_machine_ids: set[str] = set(entry.data[CONF_SERVERS])

    try:
        account = await hass.async_add_executor_job(
            lambda: MyPlexAccount(token=token)
        )
    except Unauthorized as err:
        raise ConfigEntryAuthFailed("Plex token rejected") from err
    except Exception as err:  # noqa: BLE001 - network/plexapi noise
        raise ConfigEntryNotReady(f"plex.tv unreachable: {err}") from err

    try:
        resources = await hass.async_add_executor_job(account.resources)
    except Exception as err:  # noqa: BLE001
        raise ConfigEntryNotReady(f"Could not list Plex resources: {err}") from err

    selected_resources = [
        r for r in resources if r.clientIdentifier in selected_machine_ids and r.owned
    ]
    if not selected_resources:
        raise ConfigEntryNotReady(
            "None of the configured servers were returned by plex.tv"
        )

    # Account coordinator first — server coordinators ping it when they spot
    # an unknown user_id in a session, so the wiring needs to be reversed.
    account_coordinator = PlexAccountCoordinator(
        hass, account, selected_machine_ids
    )
    await account_coordinator.async_config_entry_first_refresh()

    server_coordinators: dict[str, PlexServerCoordinator] = {}
    library_coordinators: dict[str, PlexLibraryCoordinator] = {}
    bandwidth_coordinators: dict[str, PlexBandwidthCoordinator] = {}
    session_emitters: dict[str, SessionEventEmitter] = {}
    alert_listeners: dict[str, PlexAlertListener] = {}
    for resource in selected_resources:
        try:
            plex_server = await hass.async_add_executor_job(resource.connect)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Could not connect to Plex server %s: %s", resource.name, err
            )
            continue
        server_coord = PlexServerCoordinator(
            hass,
            entry,
            plex_server,
            account,
            resource.name,
            account_coordinator=account_coordinator,
        )
        await server_coord.async_config_entry_first_refresh()
        library_coord = PlexLibraryCoordinator(hass, server_coord)
        await library_coord.async_config_entry_first_refresh()
        bandwidth_coord = PlexBandwidthCoordinator(hass, server_coord)
        await bandwidth_coord.async_config_entry_first_refresh()

        # Emitter must be created *after* first refresh so it primes against
        # the current session list rather than firing 'started' for everything
        # that was already playing when HA came up.
        session_emitter = SessionEventEmitter(hass, server_coord)
        alert_listener = PlexAlertListener(hass, server_coord)
        await alert_listener.async_start()

        server_coordinators[resource.clientIdentifier] = server_coord
        library_coordinators[resource.clientIdentifier] = library_coord
        bandwidth_coordinators[resource.clientIdentifier] = bandwidth_coord
        session_emitters[resource.clientIdentifier] = session_emitter
        alert_listeners[resource.clientIdentifier] = alert_listener

    if not server_coordinators:
        raise ConfigEntryNotReady("Failed to connect to every selected Plex server")

    entry.runtime_data = PlexEnhancedRuntime(
        account=account,
        account_coordinator=account_coordinator,
        server_coordinators=server_coordinators,
        library_coordinators=library_coordinators,
        bandwidth_coordinators=bandwidth_coordinators,
        alert_listeners=alert_listeners,
        session_emitters=session_emitters,
    )

    if PLATFORMS:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async_register_services(hass)
    await _async_register_frontend_card(hass)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Plex Enhanced config entry."""
    if PLATFORMS:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    else:
        unload_ok = True

    runtime: PlexEnhancedRuntime | None = getattr(entry, "runtime_data", None)
    if runtime is not None:
        for emitter in runtime.session_emitters.values():
            emitter.stop()
        for listener in runtime.alert_listeners.values():
            await listener.async_stop()
        # Cancel scheduled refresh callbacks on every coordinator so a reload
        # doesn't leave stale timers polling the PMS.
        coordinators = [
            runtime.account_coordinator,
            *runtime.server_coordinators.values(),
            *runtime.library_coordinators.values(),
            *runtime.bandwidth_coordinators.values(),
        ]
        for coordinator in coordinators:
            await coordinator.async_shutdown()

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload a config entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
