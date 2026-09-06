"""Delivery logic for AirPlay Notifier.

This module turns a notification (message/title/data) into spoken audio on
one `media_player` target, using one of two strategies:

- **Music Assistant** (`STRATEGY_MUSIC_ASSISTANT`): the target `media_player`
  belongs to the `music_assistant` integration. We resolve the message to a
  playable URL ourselves and hand it to the `music_assistant.play_announcement`
  action.
- **Direct** (`STRATEGY_DIRECT`): everything else (in practice, `apple_tv`
  media players that support `play_media`). We call the core `tts.speak`
  action, which resolves and plays the announcement itself.

Why Music Assistant needs manual URL resolution
------------------------------------------------
It is tempting to assume `music_assistant.play_announcement` accepts a
`media-source://` URI directly, since Music Assistant's own
`media_player.play_media` handling does resolve those. It does not.

Verified in `home-assistant-core` (2026.9.1):

- `homeassistant/components/music_assistant/services.py` registers
  `play_announcement` (`SERVICE_PLAY_ANNOUNCEMENT`) as a platform entity
  service whose `url` field is `cv.string` (a plain string, no media-source
  handling) and dispatches straight to
  `MusicAssistantPlayer._async_handle_play_announcement`.
- `homeassistant/components/music_assistant/media_player.py`,
  `_async_handle_play_announcement` (around line 564) forwards `url`
  unchanged to `self.mass.players.play_announcement(self.player_id, url, ...)`
  — no `media_source.async_resolve_media` call anywhere on this path.
- The *only* place Music Assistant resolves a `media-source://` URI is
  inside `MusicAssistantPlayer.async_play_media` (around line 385, the
  handler for the generic `media_player.play_media` service), which is a
  different code path taken only when `announce=True` is passed as a kwarg
  to `play_media` rather than when `music_assistant.play_announcement` is
  called directly.

Since the task specifies calling `music_assistant.play_announcement`
directly (not `media_player.play_media` with `announce: true`), this
integration resolves the TTS media-source ID to a real HTTP(S) URL itself,
using the same three calls Home Assistant's own
`assist_satellite/entity.py` (`_resolve_announcement`, around line 677-696)
uses for exactly this purpose:

1. `homeassistant.components.tts.generate_media_source_id` — build a
   `media-source://tts/<engine>?message=...` identifier
   (`homeassistant/components/tts/media_source.py`).
2. `homeassistant.components.media_source.async_resolve_media` — turn that
   identifier into a `PlayMedia` with a real `.url`
   (`homeassistant/components/media_source/helper.py:115`).
3. `homeassistant.components.media_player.async_process_play_media_url` —
   make the URL absolute and, if it points back at this Home Assistant
   instance, sign it so an external player can fetch it without
   authenticating (`homeassistant/components/media_player/browse_media.py:34`).

The Direct strategy does not need any of this: `tts.speak`
(`homeassistant/components/tts/entity.py`, `TextToSpeechEntity.async_speak`)
already builds the same media-source ID and calls
`media_player.play_media` with `announce: true` on our behalf, and
`apple_tv`'s own `async_play_media`
(`homeassistant/components/apple_tv/media_player.py`, around line 353-372)
resolves `media-source://` URIs itself before streaming.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components import media_source, tts
from homeassistant.components.media_player.browse_media import (
    async_process_play_media_url,
)
from homeassistant.components.media_player.const import (
    ATTR_MEDIA_VOLUME_LEVEL,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
)
from homeassistant.components.tts.const import DOMAIN as TTS_DOMAIN
from homeassistant.const import SERVICE_VOLUME_SET
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later

from .const import (
    ATTR_LANGUAGE,
    ATTR_SOURCE_ENTITY,
    ATTR_TTS_ENTITY,
    ATTR_VOICE,
    ATTR_VOLUME,
    MIN_RESTORE_DELAY_SECONDS,
    MUSIC_ASSISTANT_DOMAIN,
    RESTORE_DELAY_PADDING_SECONDS,
    SPEECH_SECONDS_PER_CHARACTER,
    STRATEGY_AUTO,
    STRATEGY_DIRECT,
    STRATEGY_MUSIC_ASSISTANT,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class AirplayNotifierOptions:
    """Resolved configuration for one AirPlay Notifier config entry."""

    media_player: str
    tts_entity: str
    language: str | None = None
    voice: str | None = None
    volume: float | None = None
    restore_volume: bool = True
    strategy: str = STRATEGY_AUTO
    announce_prefix: str = ""
    deny_domains: list[str] = field(default_factory=list)


class AnnouncementDenied(HomeAssistantError):
    """Raised when a call is refused by the deny-list.

    Security is never spoken: `alarm_control_panel` and `lock` (by default)
    are excluded from `deny_domains` so an armed-away announcement or a door
    lock state can never be read aloud by mistake.
    """


def _effective_message(options: AirplayNotifierOptions, message: str) -> str:
    """Prepend `announce_prefix`, if configured."""
    if options.announce_prefix:
        return f"{options.announce_prefix} {message}"
    return message


def _check_deny_list(options: AirplayNotifierOptions, data: dict[str, Any]) -> None:
    """Refuse the call if `data.source_entity` belongs to a denied domain."""
    source_entity = data.get(ATTR_SOURCE_ENTITY)
    if not source_entity or "." not in source_entity:
        return
    domain = source_entity.split(".", 1)[0]
    if domain in options.deny_domains:
        raise AnnouncementDenied(
            f"Refusing to speak on behalf of {source_entity!r}: "
            f"domain {domain!r} is in deny_domains {options.deny_domains}"
        )


def _resolve_strategy(hass: HomeAssistant, options: AirplayNotifierOptions) -> str:
    """Return the strategy to use for this call.

    `auto` inspects the entity registry platform of the target
    `media_player` (`homeassistant/helpers/entity_registry.py`,
    `RegistryEntry.platform`, populated with the integration domain that set
    up the entity) rather than guessing from the entity_id.
    """
    if options.strategy != STRATEGY_AUTO:
        return options.strategy

    registry = er.async_get(hass)
    entry = registry.async_get(options.media_player)
    if entry is not None and entry.platform == MUSIC_ASSISTANT_DOMAIN:
        return STRATEGY_MUSIC_ASSISTANT
    return STRATEGY_DIRECT


def _estimate_speech_seconds(message: str) -> float:
    """Estimate how long `message` will take to speak.

    Home Assistant's TTS pipeline does not report the duration of the
    synthesized clip anywhere accessible before playback starts (there is no
    `duration` on `PlayMedia`, see
    `homeassistant/components/media_source/models.py`), so an exact restore
    trigger is not available. This is a deliberate heuristic, not something
    read from core: ~14 characters/second (a brisk but intelligible spoken
    rate) plus fixed padding, floored to a minimum so very short messages
    still get an audible volume window.
    """
    estimate = (
        len(message) * SPEECH_SECONDS_PER_CHARACTER + RESTORE_DELAY_PADDING_SECONDS
    )
    return max(estimate, MIN_RESTORE_DELAY_SECONDS)


async def _async_resolve_tts_url(
    hass: HomeAssistant,
    *,
    message: str,
    tts_entity: str,
    language: str | None,
    voice: str | None,
    target_media_player: str,
) -> str:
    """Resolve `message` to a real, playable HTTP(S) URL.

    See the module docstring for why this is required for the Music
    Assistant strategy.
    """
    tts_options: dict[str, Any] | None = {ATTR_VOICE: voice} if voice else None
    media_content_id = tts.generate_media_source_id(
        hass,
        message,
        engine=tts_entity,
        language=language,
        options=tts_options,
    )
    resolved = await media_source.async_resolve_media(
        hass, media_content_id, target_media_player
    )
    return async_process_play_media_url(hass, resolved.url)


async def _async_get_current_volume(
    hass: HomeAssistant, entity_id: str
) -> float | None:
    """Return the media player's current `volume_level`, if known."""
    state = hass.states.get(entity_id)
    if state is None:
        return None
    volume = state.attributes.get(ATTR_MEDIA_VOLUME_LEVEL)
    return float(volume) if isinstance(volume, int | float) else None


async def _async_set_volume(hass: HomeAssistant, entity_id: str, volume: float) -> None:
    """Call `media_player.volume_set` on `entity_id`."""
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_VOLUME_SET,
        {"entity_id": entity_id, ATTR_MEDIA_VOLUME_LEVEL: volume},
        blocking=True,
    )


async def _async_deliver_direct(
    hass: HomeAssistant, options: AirplayNotifierOptions, message: str
) -> None:
    """Speak `message` via `tts.speak` targeting `options.media_player`.

    `tts.speak` (`homeassistant/components/tts/entity.py:133`) already
    builds the media-source ID and calls `media_player.play_media` with
    `announce: true`; `apple_tv`'s own `async_play_media`
    (`homeassistant/components/apple_tv/media_player.py:353`) resolves
    `media-source://` URIs before streaming, so no manual resolution is
    needed here.
    """
    volume = options.volume
    previous_volume: float | None = None
    if volume is not None:
        if options.restore_volume:
            previous_volume = await _async_get_current_volume(
                hass, options.media_player
            )
        await _async_set_volume(hass, options.media_player, volume)

    tts_options: dict[str, Any] | None = (
        {ATTR_VOICE: options.voice} if options.voice else None
    )
    await hass.services.async_call(
        TTS_DOMAIN,
        "speak",
        {
            "entity_id": options.tts_entity,
            "media_player_entity_id": options.media_player,
            "message": message,
            "cache": True,
            **({"language": options.language} if options.language else {}),
            **({"options": tts_options} if tts_options else {}),
        },
        blocking=True,
    )

    if volume is not None and options.restore_volume and previous_volume is not None:
        delay = _estimate_speech_seconds(message)

        async def _restore(_now: Any) -> None:
            await _async_set_volume(hass, options.media_player, previous_volume)

        async_call_later(hass, delay, _restore)


async def _async_deliver_music_assistant(
    hass: HomeAssistant, options: AirplayNotifierOptions, message: str
) -> None:
    """Speak `message` via `music_assistant.play_announcement`.

    Music Assistant's own `announce_volume` field
    (`homeassistant/components/music_assistant/const.py`,
    `ATTR_ANNOUNCE_VOLUME`) is a temporary, self-restoring volume level for
    the announcement (handled by the Music Assistant player library, not by
    this integration), so `restore_volume` is not applicable to this
    strategy: there is nothing for us to restore.
    """
    url = await _async_resolve_tts_url(
        hass,
        message=message,
        tts_entity=options.tts_entity,
        language=options.language,
        voice=options.voice,
        target_media_player=options.media_player,
    )

    service_data: dict[str, Any] = {"entity_id": options.media_player, "url": url}
    if options.volume is not None:
        service_data["announce_volume"] = max(1, min(100, round(options.volume * 100)))

    await hass.services.async_call(
        MUSIC_ASSISTANT_DOMAIN,
        "play_announcement",
        service_data,
        blocking=True,
    )


async def async_deliver_message(
    hass: HomeAssistant,
    options: AirplayNotifierOptions,
    message: str,
    data: dict[str, Any] | None = None,
) -> None:
    """Speak `message` on `options.media_player`, applying overrides in `data`.

    `data` may override, per call: `volume`, `language`, `voice`,
    `tts_entity`. `data.source_entity` is checked against `deny_domains`
    before anything else runs.
    """
    data = data or {}
    _check_deny_list(options, data)

    tts_entity = data.get(ATTR_TTS_ENTITY, options.tts_entity)
    language = data.get(ATTR_LANGUAGE, options.language)
    voice = data.get(ATTR_VOICE, options.voice)
    volume = data.get(ATTR_VOLUME, options.volume)

    call_options = AirplayNotifierOptions(
        media_player=options.media_player,
        tts_entity=tts_entity,
        language=language,
        voice=voice,
        volume=volume,
        restore_volume=options.restore_volume,
        strategy=options.strategy,
        announce_prefix=options.announce_prefix,
        deny_domains=options.deny_domains,
    )

    full_message = _effective_message(options, message)
    strategy = _resolve_strategy(hass, call_options)

    _LOGGER.debug(
        "Speaking on %s via %s strategy: %r",
        options.media_player,
        strategy,
        full_message,
    )

    if strategy == STRATEGY_MUSIC_ASSISTANT:
        await _async_deliver_music_assistant(hass, call_options, full_message)
    else:
        await _async_deliver_direct(hass, call_options, full_message)
