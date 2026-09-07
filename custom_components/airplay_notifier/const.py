"""Constants for the AirPlay Notifier integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "airplay_notifier"

# Integration domains this integration knows how to speak through.
MUSIC_ASSISTANT_DOMAIN: Final = "music_assistant"
APPLE_TV_DOMAIN: Final = "apple_tv"

# Actions called by the delivery strategies.
SERVICE_PLAY_ANNOUNCEMENT: Final = "play_announcement"
SERVICE_SPEAK: Final = "speak"

# Music Assistant's `announce_volume` is an integer percentage (1-100); 0 is
# not a valid "silent" value, see docs/known-issues.md.
MA_MIN_ANNOUNCE_VOLUME: Final = 1
MA_MAX_ANNOUNCE_VOLUME: Final = 100

# Config / options entry keys.
CONF_MEDIA_PLAYER: Final = "media_player"
CONF_TTS_ENTITY: Final = "tts_entity"
CONF_LANGUAGE: Final = "language"
CONF_VOICE: Final = "voice"
CONF_VOLUME: Final = "volume"
CONF_RESTORE_VOLUME: Final = "restore_volume"
CONF_STRATEGY: Final = "strategy"
CONF_ANNOUNCE_PREFIX: Final = "announce_prefix"
CONF_DENY_DOMAINS: Final = "deny_domains"
CONF_QUIET_START: Final = "quiet_start"
CONF_QUIET_END: Final = "quiet_end"
CONF_QUIET_VOLUME: Final = "quiet_volume"

# Strategies.
STRATEGY_AUTO: Final = "auto"
STRATEGY_MUSIC_ASSISTANT: Final = "music_assistant"
STRATEGY_DIRECT: Final = "direct"
VALID_STRATEGIES: Final[tuple[str, ...]] = (
    STRATEGY_AUTO,
    STRATEGY_MUSIC_ASSISTANT,
    STRATEGY_DIRECT,
)

# Defaults.
DEFAULT_VOLUME: Final = 0.6
DEFAULT_RESTORE_VOLUME: Final = True
DEFAULT_STRATEGY: Final = STRATEGY_AUTO
DEFAULT_ANNOUNCE_PREFIX: Final = ""
DEFAULT_DENY_DOMAINS: Final[list[str]] = ["alarm_control_panel", "lock"]

# Per-call `data` overrides accepted by the legacy notify service and by
# NotifyEntity.send_message (via the `data` payload attached by the notify
# component).
ATTR_VOLUME: Final = "volume"
ATTR_LANGUAGE: Final = "language"
ATTR_VOICE: Final = "voice"
ATTR_TTS_ENTITY: Final = "tts_entity"
ATTR_SOURCE_ENTITY: Final = "source_entity"
ATTR_PRIORITY: Final = "priority"

# `priority` is a closed set rather than a free string: only `critical`
# does anything (it bypasses quiet hours), and a typo in a value that
# decides whether a 3am alarm is spoken must fail the call rather than
# quietly behave like `normal`.
PRIORITY_NORMAL: Final = "normal"
PRIORITY_CRITICAL: Final = "critical"
VALID_PRIORITIES: Final[tuple[str, ...]] = (PRIORITY_NORMAL, PRIORITY_CRITICAL)

# Heuristic used to size the volume-restore delay when a strategy cannot
# report when playback actually finished (see docs/ARCHITECTURE.md).
SPEECH_SECONDS_PER_CHARACTER: Final = 0.07
MIN_RESTORE_DELAY_SECONDS: Final = 3.0
RESTORE_DELAY_PADDING_SECONDS: Final = 2.0
