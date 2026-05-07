"""Unit tests for SessionEventEmitter — pure dataclass diffing, no plexapi."""
from __future__ import annotations

import pytest

from custom_components.plex_enhanced.alert_listener import SessionEventEmitter
from custom_components.plex_enhanced.const import (
    EVENT_PLAYBACK_PAUSED,
    EVENT_PLAYBACK_RESUMED,
    EVENT_PLAYBACK_STARTED,
    EVENT_PLAYBACK_STOPPED,
)


@pytest.mark.asyncio
async def test_priming_skips_events_for_existing_sessions(
    hass, fake_coordinator, make_session
):
    """A session present at construction must not fire 'started'."""
    fake_coordinator.data.sessions = (make_session("1", "playing"),)

    started: list = []
    hass.bus.async_listen(EVENT_PLAYBACK_STARTED, started.append)

    SessionEventEmitter(hass, fake_coordinator)
    await hass.async_block_till_done()

    assert started == []


@pytest.mark.asyncio
async def test_new_session_fires_started(hass, fake_coordinator, make_session):
    fake_coordinator.data.sessions = ()
    emitter = SessionEventEmitter(hass, fake_coordinator)

    started: list = []
    hass.bus.async_listen(EVENT_PLAYBACK_STARTED, started.append)

    fake_coordinator.data.sessions = (make_session("1", "playing"),)
    emitter._on_update()
    await hass.async_block_till_done()

    assert len(started) == 1
    assert started[0].data["session_key"] == "1"
    assert started[0].data["content"]["title"] == "Test Item"


@pytest.mark.asyncio
async def test_pause_then_resume_emits_correct_events(
    hass, fake_coordinator, make_session
):
    fake_coordinator.data.sessions = (make_session("1", "playing"),)
    emitter = SessionEventEmitter(hass, fake_coordinator)

    paused: list = []
    resumed: list = []
    hass.bus.async_listen(EVENT_PLAYBACK_PAUSED, paused.append)
    hass.bus.async_listen(EVENT_PLAYBACK_RESUMED, resumed.append)

    fake_coordinator.data.sessions = (make_session("1", "paused"),)
    emitter._on_update()
    await hass.async_block_till_done()

    fake_coordinator.data.sessions = (make_session("1", "playing"),)
    emitter._on_update()
    await hass.async_block_till_done()

    assert len(paused) == 1
    assert len(resumed) == 1


@pytest.mark.asyncio
async def test_disappearing_session_fires_stopped(
    hass, fake_coordinator, make_session
):
    fake_coordinator.data.sessions = (make_session("1", "playing"),)
    emitter = SessionEventEmitter(hass, fake_coordinator)

    stopped: list = []
    hass.bus.async_listen(EVENT_PLAYBACK_STOPPED, stopped.append)

    fake_coordinator.data.sessions = ()
    emitter._on_update()
    await hass.async_block_till_done()

    assert len(stopped) == 1
    assert stopped[0].data["session_key"] == "1"


@pytest.mark.asyncio
async def test_state_unchanged_does_not_emit(
    hass, fake_coordinator, make_session
):
    fake_coordinator.data.sessions = (make_session("1", "playing"),)
    emitter = SessionEventEmitter(hass, fake_coordinator)

    captured: list = []
    for event in (
        EVENT_PLAYBACK_STARTED,
        EVENT_PLAYBACK_PAUSED,
        EVENT_PLAYBACK_RESUMED,
        EVENT_PLAYBACK_STOPPED,
    ):
        hass.bus.async_listen(event, captured.append)

    # Same session in same state on next refresh — must be silent.
    emitter._on_update()
    await hass.async_block_till_done()

    assert captured == []
