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
`assist_satellite/entity.py` uses for exactly this purpose
(`AssistSatelliteEntity._resolve_announcement_media_id`, lines 638-700 in
2026.9.1 — it calls `tts.generate_media_source_id`,
`media_source.async_resolve_media` and `async_process_play_media_url` in
that order to hand a satellite a fetchable URL):

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

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Final

import voluptuous as vol
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
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback, valid_entity_id
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.event import async_call_later

from .const import (
    ATTR_LANGUAGE,
    ATTR_SOURCE_ENTITY,
    ATTR_TTS_ENTITY,
    ATTR_VOICE,
    ATTR_VOLUME,
    DOMAIN,
    MA_MAX_ANNOUNCE_VOLUME,
    MA_MIN_ANNOUNCE_VOLUME,
    MIN_RESTORE_DELAY_SECONDS,
    MUSIC_ASSISTANT_DOMAIN,
    RESTORE_DELAY_PADDING_SECONDS,
    SERVICE_PLAY_ANNOUNCEMENT,
    SERVICE_SPEAK,
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
    voice: str | dict[str, Any] | None = None
    volume: float | None = None
    restore_volume: bool = True
    strategy: str = STRATEGY_AUTO
    announce_prefix: str = ""
    deny_domains: list[str] = field(default_factory=list)


@dataclass(slots=True)
class VolumeRestoreState:
    """Per-player bookkeeping for the announcement volume window.

    One instance lives on the config entry's runtime data (one entry = one
    `media_player`), so overlapping announcements on the same player share
    it. See `_async_deliver_direct` for what each field guards.
    """

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    original_volume: float | None = None
    cancel_restore: CALLBACK_TYPE | None = None

    @callback
    def async_cancel_pending_restore(self) -> None:
        """Cancel the restore timer, if one is armed.

        Registered with `entry.async_on_unload` so an unloaded entry never
        leaves a timer behind that would move a player's volume minutes
        later, after the integration is gone.
        """
        if self.cancel_restore is not None:
            self.cancel_restore()
            self.cancel_restore = None


# Per-call `data` payload accepted by the legacy `notify.airplay_<name>`
# service. Unknown keys are rejected (voluptuous' default `PREVENT_EXTRA`)
# so a typo such as `volumne:` fails loudly instead of being ignored and
# speaking at the configured volume.
#
# `source_entity` is deliberately typed loosely here: it is normalised and
# validated by `_normalise_source_entities`, which raises the dedicated,
# translated `invalid_source_entity` refusal rather than a generic schema
# error.
CALL_DATA_SCHEMA: Final = vol.Schema(
    {
        vol.Optional(ATTR_VOLUME): vol.All(
            vol.Coerce(float), vol.Range(min=0.0, max=1.0)
        ),
        vol.Optional(ATTR_LANGUAGE): cv.string,
        vol.Optional(ATTR_VOICE): vol.Any(cv.string, dict),
        vol.Optional(ATTR_TTS_ENTITY): cv.entity_domain(TTS_DOMAIN),
        vol.Optional(ATTR_SOURCE_ENTITY): object,
    }
)


def _validate_call_data(data: dict[str, Any]) -> dict[str, Any]:
    """Validate a per-call `data` payload, or refuse the call."""
    try:
        return dict(CALL_DATA_SCHEMA(data))
    except vol.Invalid as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_call_data",
            translation_placeholders={"error": str(err)},
        ) from err


def _tts_options(voice: str | dict[str, Any] | None) -> dict[str, Any] | None:
    """Build the `options` mapping handed to the TTS engine.

    A plain string is the engine-specific voice identifier and becomes
    `{"voice": ...}`; a mapping is passed through as the complete TTS
    options payload, for engines that accept more than a voice.
    """
    if voice is None:
        return None
    if isinstance(voice, dict):
        return dict(voice) or None
    return {ATTR_VOICE: voice}


class AnnouncementDenied(ServiceValidationError):
    """Raised when a call is refused by the deny-list.

    Security is never spoken: `alarm_control_panel` and `lock` (by default)
    are listed in `deny_domains` so an armed-away announcement or a door
    lock state can never be read aloud by mistake.

    A `ServiceValidationError` rather than a bare `HomeAssistantError`: the
    caller passed something this integration refuses to act on, and Home
    Assistant renders that class of error to the user without a stack trace
    (`homeassistant/exceptions.py`). Every instance carries a
    `translation_key` resolved from this integration's `exceptions` section.
    """


def _effective_message(options: AirplayNotifierOptions, message: str) -> str:
    """Prepend `announce_prefix`, if configured."""
    if options.announce_prefix:
        return f"{options.announce_prefix} {message}"
    return message


def _normalise_source_entities(value: Any) -> list[str]:
    """Return `value` as a list of lower-case, well-formed entity ids.

    `source_entity` is caller-supplied and arrives in whatever shape an
    automation happens to produce: a bare string, a list (the shape every
    Home Assistant `entity_id` field accepts), a tuple from a template, or
    something with stray whitespace or capitals. Every one of those used to
    walk straight past the deny-list, so they are all normalised here with
    `cv.ensure_list` + `str()` + `casefold()`.

    Anything that is not a usable `domain.object_id`
    (`homeassistant.core.valid_entity_id`) is *refused*, never ignored:
    silently speaking a message whose provenance could not be checked is
    exactly the failure mode the deny-list exists to prevent.
    """
    entities: list[str] = []
    for item in cv.ensure_list(value):
        entity_id = str(item).strip().casefold()
        if not valid_entity_id(entity_id):
            raise AnnouncementDenied(
                translation_domain=DOMAIN,
                translation_key="invalid_source_entity",
                translation_placeholders={"source_entity": str(item)},
            )
        entities.append(entity_id)
    return entities


def _check_deny_list(options: AirplayNotifierOptions, data: dict[str, Any]) -> None:
    """Refuse the call if any `data.source_entity` is in a denied domain."""
    raw = data.get(ATTR_SOURCE_ENTITY)
    if raw is None:
        return

    denied = {domain.strip().casefold() for domain in options.deny_domains}
    for entity_id in _normalise_source_entities(raw):
        domain = entity_id.split(".", 1)[0]
        if domain in denied:
            raise AnnouncementDenied(
                translation_domain=DOMAIN,
                translation_key="source_domain_denied",
                translation_placeholders={
                    "source_entity": entity_id,
                    "domain": domain,
                    "deny_domains": ", ".join(sorted(denied)),
                },
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


@callback
def _async_usable_strategy(hass: HomeAssistant, strategy: str) -> str:
    """Downgrade `strategy` to one whose action actually exists right now.

    `music_assistant` is an `after_dependencies` entry, not a hard
    dependency: the target player can be registered to the
    `music_assistant` platform while the integration itself is not loaded
    (not set up yet at startup, unloaded, or in a failed retry). Calling
    `music_assistant.play_announcement` then fails with a bare
    `ServiceNotFound`, which says nothing useful to whoever wrote the
    automation.
    """
    if strategy != STRATEGY_MUSIC_ASSISTANT or hass.services.has_service(
        MUSIC_ASSISTANT_DOMAIN, SERVICE_PLAY_ANNOUNCEMENT
    ):
        return strategy

    if not hass.services.has_service(TTS_DOMAIN, SERVICE_SPEAK):
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="no_delivery_path"
        )

    _LOGGER.warning(
        "%s.%s is not available (Music Assistant is not loaded); falling back "
        "to the Direct strategy (%s.%s) for this announcement",
        MUSIC_ASSISTANT_DOMAIN,
        SERVICE_PLAY_ANNOUNCEMENT,
        TTS_DOMAIN,
        SERVICE_SPEAK,
    )
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
    voice: str | dict[str, Any] | None,
    target_media_player: str,
) -> str:
    """Resolve `message` to a real, playable HTTP(S) URL.

    See the module docstring for why this is required for the Music
    Assistant strategy.
    """
    tts_options = _tts_options(voice)
    media_content_id = tts.generate_media_source_id(
        hass,
        message,
        engine=tts_entity,
        language=language,
        options=tts_options,
        # Same file cache the Direct strategy gets for free by passing
        # `cache: true` to `tts.speak`. `generate_media_source_id`
        # (`homeassistant/components/tts/media_source.py`) encodes it as
        # `cache=true` in the media-source identifier, which
        # `parse_media_source_id` turns back into `use_file_cache`. Without
        # it the two strategies disagree about caching for identical text.
        cache=True,
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


@callback
def _async_schedule_restore(
    hass: HomeAssistant,
    options: AirplayNotifierOptions,
    state: VolumeRestoreState,
    message: str,
) -> None:
    """(Re)arm the single pending volume restore for this player.

    Must be called with `state.lock` held. Any restore already armed by an
    earlier, still-overlapping announcement is cancelled first, so exactly
    one restore ever fires and it always targets the volume the player had
    *before* the first announcement of the burst.
    """
    original = state.original_volume
    if original is None:
        return

    state.async_cancel_pending_restore()

    async def _restore(_now: Any) -> None:
        state.cancel_restore = None
        state.original_volume = None
        await _async_set_volume(hass, options.media_player, original)

    state.cancel_restore = async_call_later(
        hass, _estimate_speech_seconds(message), _restore
    )


async def _async_deliver_direct(
    hass: HomeAssistant,
    options: AirplayNotifierOptions,
    message: str,
    state: VolumeRestoreState,
) -> None:
    """Speak `message` via `tts.speak` targeting `options.media_player`.

    `tts.speak` (`homeassistant/components/tts/entity.py:133`) already
    builds the media-source ID and calls `media_player.play_media` with
    `announce: true`; `apple_tv`'s own `async_play_media`
    (`homeassistant/components/apple_tv/media_player.py:353`) resolves
    `media-source://` URIs before streaming, so no manual resolution is
    needed here.

    The volume window around the announcement is guarded by `state`:

    - `state.lock` serialises the read-then-set of the player volume, so two
      announcements racing each other cannot interleave between reading the
      current volume and raising it.
    - `state.original_volume` is remembered *once* per burst. Without this, a
      second announcement starting while the first is still speaking would
      read the already-raised announcement volume and "restore" to it.
    - the restore is scheduled, never awaited, and its handle is kept so the
      next overlapping announcement (or an entry unload) can cancel it.
    - the restore is armed in a `finally`, so a `tts.speak` that raises
      (unknown engine, player refusing `play_media`, …) does not leave the
      player stuck at announcement volume.
    """
    restore = options.volume is not None and options.restore_volume

    if options.volume is not None:
        async with state.lock:
            if restore:
                state.async_cancel_pending_restore()
                if state.original_volume is None:
                    state.original_volume = await _async_get_current_volume(
                        hass, options.media_player
                    )
            await _async_set_volume(hass, options.media_player, options.volume)

    tts_options = _tts_options(options.voice)
    try:
        await hass.services.async_call(
            TTS_DOMAIN,
            SERVICE_SPEAK,
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
    finally:
        if restore:
            async with state.lock:
                _async_schedule_restore(hass, options, state, message)


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

    `announce_volume` is an integer percentage and Music Assistant has no
    "silent announcement" value, so a configured `volume: 0` is clamped up to
    1 % and warned about rather than silently played at full volume. See
    docs/known-issues.md.
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
        announce_volume = max(
            MA_MIN_ANNOUNCE_VOLUME,
            min(MA_MAX_ANNOUNCE_VOLUME, round(options.volume * 100)),
        )
        if announce_volume != round(options.volume * 100):
            _LOGGER.warning(
                "Volume %.2f is outside the range Music Assistant's "
                "announce_volume accepts (%d-%d %%); announcing at %d %% instead",
                options.volume,
                MA_MIN_ANNOUNCE_VOLUME,
                MA_MAX_ANNOUNCE_VOLUME,
                announce_volume,
            )
        service_data["announce_volume"] = announce_volume

    await hass.services.async_call(
        MUSIC_ASSISTANT_DOMAIN,
        SERVICE_PLAY_ANNOUNCEMENT,
        service_data,
        blocking=True,
    )


async def async_deliver_message(
    hass: HomeAssistant,
    options: AirplayNotifierOptions,
    message: str,
    data: dict[str, Any] | None = None,
    *,
    volume_state: VolumeRestoreState | None = None,
) -> None:
    """Speak `message` on `options.media_player`, applying overrides in `data`.

    `data` may override, per call: `volume`, `language`, `voice`,
    `tts_entity`. `data.source_entity` is checked against `deny_domains`
    before anything else runs.

    `volume_state` is the config entry's shared `VolumeRestoreState` (from
    `entry.runtime_data`). Callers should always pass it: it is what makes
    two overlapping announcements on the same player restore the volume the
    player had before the *first* of them. A fresh one is created when it is
    omitted so the function stays usable standalone.
    """
    data = _validate_call_data(data or {})
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
    strategy = _async_usable_strategy(hass, _resolve_strategy(hass, call_options))

    _LOGGER.debug(
        "Speaking on %s via %s strategy: %r",
        options.media_player,
        strategy,
        full_message,
    )

    if strategy == STRATEGY_MUSIC_ASSISTANT:
        await _async_deliver_music_assistant(hass, call_options, full_message)
    else:
        await _async_deliver_direct(
            hass,
            call_options,
            full_message,
            volume_state if volume_state is not None else VolumeRestoreState(),
        )
