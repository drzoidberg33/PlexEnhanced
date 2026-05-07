/**
 * Plex Enhanced Sessions card.
 *
 * Reads every `sensor.*_now_playing` entity exposed by the Plex Enhanced
 * integration, flattens their `sessions` attributes into a single list, and
 * renders one row per active playback. Re-renders on every hass update so
 * progress bars stay current.
 *
 * Usage in Lovelace:
 *
 *     type: custom:plex-enhanced-sessions-card
 *     title: Plex Now Playing      # optional, defaults to "Plex Now Playing"
 *     hide_when_empty: false        # optional
 */

class PlexEnhancedSessionsCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  setConfig(config) {
    this._config = { title: "Plex Now Playing", ...(config || {}) };
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    const count = this._gatherSessions().length;
    // ha-card units, roughly 50px each.
    return Math.max(2, 1 + count * 2);
  }

  static getStubConfig() {
    return { title: "Plex Now Playing" };
  }

  _gatherSessions() {
    if (!this._hass) return [];
    const sessions = [];
    for (const [eid, state] of Object.entries(this._hass.states)) {
      if (!eid.startsWith("sensor.") || !eid.endsWith("_now_playing")) continue;
      const items = state && state.attributes && state.attributes.sessions;
      if (Array.isArray(items)) sessions.push(...items);
    }
    // Most-recently-started first; ISO 8601 strings sort lexicographically.
    sessions.sort((a, b) => {
      const ta = a.started_at || "";
      const tb = b.started_at || "";
      return tb.localeCompare(ta);
    });
    return sessions;
  }

  _render() {
    if (!this._config) return;
    const sessions = this._gatherSessions();

    if (sessions.length === 0 && this._config.hide_when_empty) {
      this.shadowRoot.innerHTML = "";
      return;
    }

    const headerAttr = this._config.title
      ? `header="${this._escape(this._config.title)}"`
      : "";

    this.shadowRoot.innerHTML = `
      <style>${this._styles()}</style>
      <ha-card ${headerAttr}>
        <div class="content">
          ${
            sessions.length === 0
              ? '<div class="empty">No active Plex sessions.</div>'
              : sessions.map((s) => this._renderSession(s)).join("")
          }
        </div>
      </ha-card>
    `;
  }

  _renderSession(s) {
    const dur = (s.content && s.content.duration_ms) || 0;
    const off = (s.content && s.content.view_offset_ms) || 0;
    const pct = dur > 0 ? Math.min(100, (off / dur) * 100) : 0;
    const state = ((s.player && s.player.state) || "stopped").toLowerCase();
    const stateLabel = state.charAt(0).toUpperCase() + state.slice(1);

    const c = s.content || {};
    let titleText = c.title || "Unknown";
    if (c.show && c.season != null && c.episode != null) {
      titleText = `${c.show} · S${this._pad(c.season)}E${this._pad(
        c.episode
      )} · ${c.title}`;
    } else if (c.artist) {
      titleText = `${c.artist} — ${c.title}`;
    }

    const meta = [s.player && s.player.title, s.player && s.player.product]
      .filter(Boolean)
      .join(" · ");

    const transcoding = s.transcoding
      ? `<span class="badge transcoding">Transcoding</span>`
      : "";

    const thumb = c.thumb_url
      ? `<img class="thumb" src="${this._escape(
          c.thumb_url
        )}" loading="lazy" onerror="this.classList.add('hidden')" />`
      : `<div class="thumb placeholder"></div>`;

    const progressLabel =
      dur > 0
        ? `${this._formatTime(off)} / ${this._formatTime(dur)}`
        : "";

    return `
      <div class="session">
        ${thumb}
        <div class="info">
          <div class="user">${this._escape((s.user && s.user.title) || "")}</div>
          <div class="title" title="${this._escape(titleText)}">${this._escape(
      titleText
    )}</div>
          <div class="meta">${this._escape(meta)}</div>
          <div class="progress">
            <div class="bar" style="width: ${pct.toFixed(1)}%"></div>
          </div>
          <div class="footer">
            <div class="badges">
              <span class="badge state ${state}">${stateLabel}</span>
              ${transcoding}
            </div>
            <div class="time">${this._escape(progressLabel)}</div>
          </div>
        </div>
      </div>
    `;
  }

  _pad(n) {
    return String(n).padStart(2, "0");
  }

  _formatTime(ms) {
    const totalSec = Math.max(0, Math.floor(ms / 1000));
    const h = Math.floor(totalSec / 3600);
    const m = Math.floor((totalSec % 3600) / 60);
    const s = totalSec % 60;
    if (h > 0) {
      return `${h}:${this._pad(m)}:${this._pad(s)}`;
    }
    return `${m}:${this._pad(s)}`;
  }

  _escape(s) {
    if (s == null) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  _styles() {
    return `
      :host { display: block; }
      .content { padding: 0; }
      .empty {
        padding: 24px 16px;
        text-align: center;
        color: var(--secondary-text-color);
        font-style: italic;
      }
      .session {
        display: flex;
        gap: 12px;
        padding: 12px 16px;
        border-bottom: 1px solid var(--divider-color);
      }
      .session:last-child { border-bottom: none; }
      .thumb {
        flex: 0 0 60px;
        width: 60px;
        height: 90px;
        object-fit: cover;
        border-radius: 4px;
        background: var(--secondary-background-color);
      }
      .thumb.hidden { visibility: hidden; }
      .thumb.placeholder { background: var(--secondary-background-color); }
      .info { flex: 1; min-width: 0; display: flex; flex-direction: column; }
      .user {
        font-size: 0.7em;
        color: var(--secondary-text-color);
        text-transform: uppercase;
        letter-spacing: 0.5px;
        font-weight: 600;
      }
      .title {
        font-weight: 500;
        margin: 4px 0 2px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        color: var(--primary-text-color);
      }
      .meta {
        font-size: 0.8em;
        color: var(--secondary-text-color);
        margin-bottom: 8px;
      }
      .progress {
        height: 4px;
        background: var(--divider-color);
        border-radius: 2px;
        overflow: hidden;
      }
      .bar {
        height: 100%;
        background: var(--primary-color);
        transition: width 0.5s ease-out;
      }
      .footer {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-top: 6px;
      }
      .badges { display: flex; gap: 6px; }
      .badge {
        display: inline-block;
        font-size: 0.7em;
        padding: 2px 8px;
        border-radius: 10px;
        font-weight: 500;
      }
      .badge.state.playing {
        background: var(--success-color, #4caf50);
        color: white;
      }
      .badge.state.paused {
        background: var(--secondary-text-color);
        color: var(--card-background-color);
      }
      .badge.state.buffering {
        background: var(--warning-color, #ff9800);
        color: white;
      }
      .badge.state.stopped,
      .badge.state.idle {
        background: var(--divider-color);
        color: var(--primary-text-color);
      }
      .badge.transcoding {
        background: var(--warning-color, #ff9800);
        color: white;
      }
      .time {
        font-size: 0.75em;
        color: var(--secondary-text-color);
        font-variant-numeric: tabular-nums;
      }
    `;
  }
}

customElements.define("plex-enhanced-sessions-card", PlexEnhancedSessionsCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "plex-enhanced-sessions-card",
  name: "Plex Enhanced Sessions",
  description:
    "Live view of all currently playing Plex sessions across users and servers.",
  preview: false,
});

console.info(
  "%c PLEX-ENHANCED-SESSIONS-CARD ",
  "color: white; background: #e5a00d; font-weight: 700;"
);
