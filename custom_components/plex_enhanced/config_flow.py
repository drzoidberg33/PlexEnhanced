"""Config flow for Plex Enhanced."""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_TOKEN
from homeassistant.helpers import config_validation as cv
from plexapi.exceptions import BadRequest, Unauthorized
from plexapi.myplex import MyPlexAccount, MyPlexPinLogin

from .const import (
    CONF_CLIENT_IDENTIFIER,
    CONF_SERVERS,
    DOMAIN,
    PLEX_TV_PIN_HEADERS,
)

_LOGGER = logging.getLogger(__name__)

PIN_TIMEOUT_SECONDS = 300
PIN_POLL_INTERVAL_SECONDS = 2


def _build_pin_headers(client_identifier: str) -> dict[str, str]:
    return {**PLEX_TV_PIN_HEADERS, "X-Plex-Client-Identifier": client_identifier}


class PlexEnhancedConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the user-driven setup flow for Plex Enhanced."""

    VERSION = 1

    def __init__(self) -> None:
        self._pin_login: MyPlexPinLogin | None = None
        self._pin_task: asyncio.Task[str] | None = None
        self._token: str | None = None
        self._client_identifier: str | None = None
        self._account: MyPlexAccount | None = None
        self._available_servers: list[Any] = []
        self._reauth_entry: config_entries.ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start a fresh PIN login from the integrations page."""
        return await self._async_run_pin_step(step_id="user")

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Triggered when an existing entry's token is rejected by plex.tv."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        self._client_identifier = entry_data.get(CONF_CLIENT_IDENTIFIER)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm before kicking off another plex.tv PIN flow for reauth."""
        if user_input is None and self._pin_login is None:
            assert self._reauth_entry is not None
            return self.async_show_form(
                step_id="reauth_confirm",
                description_placeholders={"username": self._reauth_entry.title},
            )
        return await self._async_run_pin_step(step_id="reauth_confirm")

    async def _async_run_pin_step(self, step_id: str) -> ConfigFlowResult:
        """Drive the PIN-login progress step shared by user + reauth."""
        if self._pin_login is None:
            if self._client_identifier is None:
                self._client_identifier = str(uuid.uuid4())
            try:
                self._pin_login = await self.hass.async_add_executor_job(
                    self._create_pin_login
                )
            except Exception:
                _LOGGER.exception("Failed to start Plex PIN login")
                return self.async_abort(reason="cannot_connect")
            self._pin_task = self.hass.async_create_task(self._wait_for_token())

        assert self._pin_task is not None
        if not self._pin_task.done():
            return self.async_show_progress(
                step_id=step_id,
                progress_action="wait_for_pin",
                description_placeholders={"url": self._pin_login.oauthUrl()},
                progress_task=self._pin_task,
            )

        try:
            self._token = self._pin_task.result()
        except TimeoutError:
            return self.async_show_progress_done(next_step_id="auth_timeout")
        except Exception:
            _LOGGER.exception("Plex PIN login failed")
            return self.async_show_progress_done(next_step_id="auth_failed")

        if self._reauth_entry is not None:
            return self.async_show_progress_done(next_step_id="reauth_finalize")
        return self.async_show_progress_done(next_step_id="servers")

    def _create_pin_login(self) -> MyPlexPinLogin:
        """Build and start polling the PIN login (runs in executor thread)."""
        assert self._client_identifier is not None
        login = MyPlexPinLogin(
            headers=_build_pin_headers(self._client_identifier),
            oauth=True,
        )
        login.run(timeout=PIN_TIMEOUT_SECONDS)
        return login

    async def _wait_for_token(self) -> str:
        """Resolve once plex.tv hands us a token, or raise on expiry."""
        assert self._pin_login is not None
        while not self._pin_login.token:
            if self._pin_login.expired:
                raise TimeoutError("Plex PIN expired before authorization")
            await asyncio.sleep(PIN_POLL_INTERVAL_SECONDS)
        return self._pin_login.token

    async def async_step_auth_timeout(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_abort(reason="auth_timeout")

    async def async_step_auth_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_abort(reason="invalid_auth")

    async def async_step_reauth_finalize(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._reauth_entry is not None and self._token is not None
        new_data = {**self._reauth_entry.data, CONF_TOKEN: self._token}
        return self.async_update_reload_and_abort(self._reauth_entry, data=new_data)

    async def async_step_servers(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show owned servers and let the user choose which to monitor."""
        if self._account is None:
            try:
                self._account = await self.hass.async_add_executor_job(
                    lambda: MyPlexAccount(token=self._token)
                )
            except (BadRequest, Unauthorized):
                return self.async_abort(reason="invalid_auth")
            except Exception:
                _LOGGER.exception("Failed to fetch Plex account")
                return self.async_abort(reason="cannot_connect")

            await self.async_set_unique_id(self._account.uuid)
            self._abort_if_unique_id_configured()

            try:
                resources = await self.hass.async_add_executor_job(
                    self._account.resources
                )
            except Exception:
                _LOGGER.exception("Failed to enumerate Plex resources")
                return self.async_abort(reason="cannot_connect")

            self._available_servers = [
                r
                for r in resources
                if r.owned and "server" in (r.provides or "").split(",")
            ]
            if not self._available_servers:
                return self.async_abort(reason="no_servers")

        if user_input is not None:
            return self.async_create_entry(
                title=self._account.username or self._account.email or "Plex",
                data={
                    CONF_TOKEN: self._token,
                    CONF_CLIENT_IDENTIFIER: self._client_identifier,
                    CONF_SERVERS: user_input[CONF_SERVERS],
                },
            )

        choices = {
            r.clientIdentifier: f"{r.name} ({r.productVersion})"
            for r in self._available_servers
        }
        return self.async_show_form(
            step_id="servers",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SERVERS, default=list(choices.keys())
                    ): cv.multi_select(choices),
                }
            ),
        )

    async def async_remove(self) -> None:
        """Best-effort cleanup when the flow is dismissed mid-PIN.

        plexapi's ``MyPlexPinLogin`` runs a non-daemon polling thread that
        otherwise lingers until its timeout fires. Cancelling the awaiter and
        signalling the login object lets the thread exit promptly.
        """
        if self._pin_task is not None and not self._pin_task.done():
            self._pin_task.cancel()
            try:
                await self._pin_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._pin_login is not None:
            stop = getattr(self._pin_login, "stop", None)
            if callable(stop):
                try:
                    await self.hass.async_add_executor_job(stop)
                except Exception:  # noqa: BLE001
                    pass
