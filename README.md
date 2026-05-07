# Plex Enhanced for Home Assistant

A richer Plex integration for Home Assistant, building on top of [python-plexapi](https://github.com/pushingkarmaorg/python-plexapi). Designed to coexist with Home Assistant's built-in `plex` integration.

## Features

- plex.tv PIN authentication via the config flow.
- Owned-server discovery; pick which servers to monitor.
- Per-server health: bandwidth (LAN / WAN / total), active sessions, transcode count, version.
- Per-library item-count sensors.
- Per-user "Now playing" sensors with rich attributes (show, season, episode, library, progress %, player, transcoding flag, server, started_at, …) — covering all users with access to the monitored owned servers.
- `media_player` entities for Plex clients with full transport controls and metadata.
- Real-time updates via the plex.tv websocket alert listener (poll fallback every 30 s).
- Bus events: `plex_enhanced_playback_started` / `_paused` / `_resumed` / `_stopped` / `_library_new`.
- Services: `scan_library`, `refresh_library`, `mark_watched`, `mark_unwatched`, `terminate_session`.

## Installation

### HACS (recommended)

1. Add this repository as a custom integration in HACS.
2. Install **Plex Enhanced**.
3. Restart Home Assistant.
4. **Settings → Devices & Services → Add Integration → Plex Enhanced** and follow the plex.tv sign-in.

### Manual

Copy `custom_components/plex_enhanced/` to your Home Assistant `config/custom_components/` directory and restart.

## Coexistence with HA's built-in Plex integration

The integration uses the `plex_enhanced` domain so it can run alongside the core `plex` integration without conflict. Entities, services and events are namespaced separately.
