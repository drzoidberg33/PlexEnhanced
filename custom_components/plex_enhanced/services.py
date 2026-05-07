"""Service handlers for Plex Enhanced.

All services target a Plex *server* via HA's device selector. The resolver
maps device_id → PlexServerCoordinator by walking the device registry; user
and client devices are deliberately rejected so the UI can offer one
ungrouped device picker without us having to maintain a custom selector.

Services are registered once per HA process — registration is idempotent so
multiple config entries can co-exist safely.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .coordinator import PlexEnhancedRuntime, PlexServerCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_SCAN_LIBRARY = "scan_library"
SERVICE_REFRESH_LIBRARY = "refresh_library"
SERVICE_MARK_WATCHED = "mark_watched"
SERVICE_MARK_UNWATCHED = "mark_unwatched"
SERVICE_TERMINATE_SESSION = "terminate_session"

ATTR_SERVER = "server"
ATTR_LIBRARY = "library"
ATTR_RATING_KEY = "rating_key"
ATTR_SESSION_KEY = "session_key"
ATTR_REASON = "reason"

_SERVER_FIELD = {vol.Required(ATTR_SERVER): cv.string}

_LIBRARY_SCHEMA = vol.Schema(
    {
        **_SERVER_FIELD,
        vol.Required(ATTR_LIBRARY): vol.Any(cv.positive_int, cv.string),
    }
)

_WATCHED_SCHEMA = vol.Schema(
    {
        **_SERVER_FIELD,
        vol.Required(ATTR_RATING_KEY): vol.Any(cv.positive_int, cv.string),
    }
)

_TERMINATE_SCHEMA = vol.Schema(
    {
        **_SERVER_FIELD,
        vol.Required(ATTR_SESSION_KEY): cv.string,
        vol.Optional(ATTR_REASON, default=""): cv.string,
    }
)


def _resolve_server_coordinator(
    hass: HomeAssistant, device_id: str
) -> PlexServerCoordinator:
    """Translate a device_id into the right PlexServerCoordinator."""
    device_reg = dr.async_get(hass)
    device = device_reg.async_get(device_id)
    if device is None:
        raise ServiceValidationError(f"Unknown device: {device_id}")

    # Server identifiers are bare machine_identifier values; user / client
    # identifiers carry their own prefix and are filtered out here.
    machine_id: str | None = None
    for ident_domain, ident_value in device.identifiers:
        if ident_domain != DOMAIN:
            continue
        if ident_value.startswith(("user_", "client_")):
            continue
        machine_id = ident_value
        break

    if machine_id is None:
        raise ServiceValidationError(
            f"Device '{device.name_by_user or device.name}' is not a Plex Media Server"
        )

    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            continue
        runtime: PlexEnhancedRuntime | None = getattr(entry, "runtime_data", None)
        if runtime is None:
            continue
        coordinator = runtime.server_coordinators.get(machine_id)
        if coordinator is not None:
            return coordinator

    raise ServiceValidationError(
        f"Server '{machine_id}' is not currently configured or loaded"
    )


def _find_library_section(server, library: Any):
    """Resolve a library by section id or title."""
    library_str = str(library)
    for section in server.library.sections():
        if str(section.key) == library_str or section.title == library_str:
            return section
    raise ServiceValidationError(f"Library not found: {library}")


async def _async_scan_library(hass: HomeAssistant, call: ServiceCall) -> None:
    coordinator = _resolve_server_coordinator(hass, call.data[ATTR_SERVER])
    library = call.data[ATTR_LIBRARY]

    def _do() -> None:
        section = _find_library_section(coordinator.server, library)
        section.update()

    await hass.async_add_executor_job(_do)


async def _async_refresh_library(hass: HomeAssistant, call: ServiceCall) -> None:
    coordinator = _resolve_server_coordinator(hass, call.data[ATTR_SERVER])
    library = call.data[ATTR_LIBRARY]

    def _do() -> None:
        section = _find_library_section(coordinator.server, library)
        section.refresh()

    await hass.async_add_executor_job(_do)


async def _async_mark_watched(hass: HomeAssistant, call: ServiceCall) -> None:
    await _async_set_watched_state(hass, call, watched=True)


async def _async_mark_unwatched(hass: HomeAssistant, call: ServiceCall) -> None:
    await _async_set_watched_state(hass, call, watched=False)


async def _async_set_watched_state(
    hass: HomeAssistant, call: ServiceCall, *, watched: bool
) -> None:
    coordinator = _resolve_server_coordinator(hass, call.data[ATTR_SERVER])
    rating_key = call.data[ATTR_RATING_KEY]

    def _do() -> None:
        try:
            ekey: int | str = int(rating_key)
        except (TypeError, ValueError):
            ekey = str(rating_key)
        try:
            item = coordinator.server.fetchItem(ekey)
        except Exception as err:  # noqa: BLE001
            raise ServiceValidationError(
                f"Could not resolve Plex item '{rating_key}': {err}"
            ) from err
        if watched:
            item.markWatched()
        else:
            item.markUnwatched()

    await hass.async_add_executor_job(_do)


async def _async_terminate_session(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    coordinator = _resolve_server_coordinator(hass, call.data[ATTR_SERVER])
    session_key = call.data[ATTR_SESSION_KEY]
    reason = (
        call.data.get(ATTR_REASON)
        or "Session terminated by Home Assistant"
    )

    def _do() -> None:
        for session in coordinator.server.sessions():
            if str(session.sessionKey) == session_key:
                session.stop(reason)
                return
        raise ServiceValidationError(
            f"Session {session_key} not found on {coordinator.display_name}"
        )

    await hass.async_add_executor_job(_do)


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register all Plex Enhanced services. Idempotent."""
    if hass.services.has_service(DOMAIN, SERVICE_SCAN_LIBRARY):
        return

    handlers = (
        (SERVICE_SCAN_LIBRARY, _async_scan_library, _LIBRARY_SCHEMA),
        (SERVICE_REFRESH_LIBRARY, _async_refresh_library, _LIBRARY_SCHEMA),
        (SERVICE_MARK_WATCHED, _async_mark_watched, _WATCHED_SCHEMA),
        (SERVICE_MARK_UNWATCHED, _async_mark_unwatched, _WATCHED_SCHEMA),
        (SERVICE_TERMINATE_SESSION, _async_terminate_session, _TERMINATE_SCHEMA),
    )

    for service, handler, schema in handlers:
        async def _wrapper(call: ServiceCall, _h=handler) -> None:
            await _h(hass, call)

        hass.services.async_register(DOMAIN, service, _wrapper, schema=schema)
