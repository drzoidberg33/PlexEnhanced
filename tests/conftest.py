"""Common pytest fixtures for Plex Enhanced."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.plex_enhanced.const import (
    CONF_CLIENT_IDENTIFIER,
    CONF_SERVERS,
    DOMAIN,
)
from custom_components.plex_enhanced.models import (
    PlexContent,
    PlexPlayer,
    PlexSession,
    PlexUser,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow custom_components/ to load during tests."""
    yield


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A representative config entry without runtime_data."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Tim",
        data={
            "token": "test-token",
            CONF_CLIENT_IDENTIFIER: "client-uuid",
            CONF_SERVERS: ["server-id-1"],
        },
        unique_id="account-uuid",
    )


@pytest.fixture
def make_session():
    """Factory producing :class:`PlexSession` instances for diff tests."""

    def _factory(
        session_key: str = "1",
        state: str = "playing",
        user_id: str = "100",
        title: str = "Test Item",
        content_type: str = "movie",
    ) -> PlexSession:
        return PlexSession(
            session_key=session_key,
            server_machine_identifier="server-id-1",
            user=PlexUser(
                user_id=user_id, username="tester", title="Tester"
            ),
            player=PlexPlayer(
                machine_identifier="client-1",
                title="Living Room",
                product="Plex Web",
                state=state,
            ),
            content=PlexContent(
                type=content_type,
                title=title,
                library="Movies",
                duration_ms=3600000,
                view_offset_ms=600000,
            ),
            transcode=None,
            bitrate_kbps=8000,
            started_at=datetime.now(timezone.utc),
        )

    return _factory


@pytest.fixture
def fake_coordinator():
    """A MagicMock that mimics PlexServerCoordinator's emitter contract."""
    coordinator = MagicMock()
    coordinator.data = MagicMock()
    coordinator.data.sessions = ()
    coordinator.last_update_success = True
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    return coordinator
