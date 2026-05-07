"""Plain-data snapshots produced by the coordinators.

Entities should only ever read from these dataclasses, never directly from
plexapi objects, so that platform code stays cheap and easy to test.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PlexUser:
    """A Plex user with access to one of the monitored owned servers."""

    user_id: str
    username: str
    title: str
    email: str | None = None
    thumb: str | None = None
    is_owner: bool = False
    is_home_user: bool = False


@dataclass(frozen=True)
class PlexPlayer:
    """The Plex client device hosting a session."""

    machine_identifier: str
    title: str
    product: str
    platform: str | None = None
    device: str | None = None
    address: str | None = None
    local: bool = True
    state: str = "stopped"  # playing / paused / buffering / stopped


@dataclass(frozen=True)
class PlexContent:
    """The piece of content currently being played in a session."""

    type: str  # movie / episode / track / clip
    title: str
    library: str | None
    duration_ms: int | None
    view_offset_ms: int | None
    year: int | None = None
    summary: str | None = None
    content_rating: str | None = None
    thumb_url: str | None = None
    art_url: str | None = None
    guid: str | None = None
    show_title: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    artist: str | None = None
    album: str | None = None


@dataclass(frozen=True)
class PlexTranscode:
    """Transcode details when the server is reformatting a stream."""

    video_decision: str | None = None
    audio_decision: str | None = None
    container: str | None = None
    target_video_codec: str | None = None
    target_audio_codec: str | None = None
    source_video_codec: str | None = None
    source_audio_codec: str | None = None
    progress: float | None = None
    throttled: bool = False
    speed: float | None = None


@dataclass(frozen=True)
class PlexSession:
    """A single playback session on a Plex Media Server."""

    session_key: str
    server_machine_identifier: str
    user: PlexUser
    player: PlexPlayer
    content: PlexContent
    transcode: PlexTranscode | None
    bitrate_kbps: int
    started_at: datetime | None


@dataclass(frozen=True)
class PlexLibrary:
    """A library section on a Plex Media Server."""

    section_id: int
    key: str
    title: str
    type: str  # movie / show / artist / photo
    item_count: int


@dataclass(frozen=True)
class PlexClientInfo:
    """A Plex client device registered against a server."""

    machine_identifier: str
    title: str
    product: str
    platform: str | None = None
    device: str | None = None
    address: str | None = None
    protocol_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlexServerData:
    """One snapshot of a single Plex Media Server's runtime state.

    Bandwidth is *not* part of this snapshot — it's owned by
    :class:`PlexBandwidthCoordinator` which polls on a faster cadence so
    bandwidth sensors update independently of session/client polling.
    """

    machine_identifier: str
    name: str
    version: str
    platform: str
    online: bool
    sessions: tuple[PlexSession, ...]
    clients: tuple[PlexClientInfo, ...]
    transcode_session_count: int
    fetched_at: datetime


@dataclass(frozen=True)
class PlexBandwidth:
    """Live throughput snapshot from ``/statistics/bandwidth``."""

    lan_kbps: int
    wan_kbps: int
    total_kbps: int
    fetched_at: datetime
