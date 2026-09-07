"""Tests for the AirPlay Notifier config and options flows."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.components.media_player.const import MediaPlayerEntityFeature
from homeassistant.const import ATTR_SUPPORTED_FEATURES, CONF_LANGUAGE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.airplay_notifier.const import (
    CONF_DENY_DOMAINS,
    CONF_MEDIA_PLAYER,
    CONF_RESTORE_VOLUME,
    CONF_STRATEGY,
    CONF_TTS_ENTITY,
    CONF_VOLUME,
    DOMAIN,
)

MEDIA_PLAYER = "media_player.living_room"
TTS_ENTITY = "tts.piper"


async def _init_user_flow(hass: HomeAssistant) -> dict:
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_user_step_creates_entry(hass: HomeAssistant) -> None:
    """The user step creates an entry titled after the player's friendly name."""
    hass.states.async_set(MEDIA_PLAYER, "idle", {"friendly_name": "Living Room"})

    result = await _init_user_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living Room"
    assert result["data"] == {
        CONF_MEDIA_PLAYER: MEDIA_PLAYER,
        CONF_TTS_ENTITY: TTS_ENTITY,
    }


async def test_user_step_falls_back_to_entity_id_slug(hass: HomeAssistant) -> None:
    """Without a known state, the title falls back to the entity's object_id."""
    result = await _init_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "living_room"


async def test_duplicate_media_player_is_aborted(hass: HomeAssistant) -> None:
    """Only one entry per media player is allowed."""
    first = await _init_user_flow(hass)
    first = await hass.config_entries.flow.async_configure(
        first["flow_id"],
        {CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )
    assert first["type"] is FlowResultType.CREATE_ENTRY

    second = await _init_user_flow(hass)
    second = await hass.config_entries.flow.async_configure(
        second["flow_id"],
        {CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: "tts.other"},
    )

    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "already_configured"


async def test_options_flow_updates_settings(hass: HomeAssistant) -> None:
    """The options flow saves the tunable settings, parsing the deny-list."""
    result = await _init_user_flow(hass)
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    options_result = await hass.config_entries.options.async_init(entry.entry_id)
    assert options_result["type"] is FlowResultType.FORM
    assert options_result["step_id"] == "init"

    updated = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        {
            CONF_LANGUAGE: "fr",
            CONF_VOLUME: 0.4,
            CONF_RESTORE_VOLUME: False,
            CONF_STRATEGY: "direct",
            "announce_prefix": "Attention.",
            CONF_DENY_DOMAINS: "alarm_control_panel, lock, person",
        },
    )
    await hass.async_block_till_done()

    assert updated["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_LANGUAGE] == "fr"
    assert entry.options[CONF_VOLUME] == 0.4
    assert entry.options[CONF_RESTORE_VOLUME] is False
    assert entry.options[CONF_STRATEGY] == "direct"
    assert entry.options[CONF_DENY_DOMAINS] == [
        "alarm_control_panel",
        "lock",
        "person",
    ]


async def test_user_step_refuses_a_player_that_cannot_play_media(
    hass: HomeAssistant,
) -> None:
    """A player without `PLAY_MEDIA` can never speak, so the form refuses it.

    `test-before-configure`: everything this integration does ends in
    `media_player.play_media`, and Home Assistant raises
    `ServiceNotSupported` when the target does not advertise the feature.
    Catching that in the form beats catching it in the log of the first
    announcement that mattered.
    """
    hass.states.async_set(
        MEDIA_PLAYER,
        "idle",
        {
            "friendly_name": "Living Room",
            ATTR_SUPPORTED_FEATURES: MediaPlayerEntityFeature.VOLUME_SET,
        },
    )

    result = await _init_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_MEDIA_PLAYER: "unsupported_player"}

    # And the refusal is recoverable: the same flow accepts a usable player.
    hass.states.async_set(
        "media_player.kitchen",
        "idle",
        {ATTR_SUPPORTED_FEATURES: MediaPlayerEntityFeature.PLAY_MEDIA},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_MEDIA_PLAYER: "media_player.kitchen", CONF_TTS_ENTITY: TTS_ENTITY},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_step_accepts_a_player_with_no_supported_features(
    hass: HomeAssistant,
) -> None:
    """An absent `supported_features` is not evidence of anything.

    A template or YAML `media_player`, or one that has never been seen,
    may not publish the attribute at all. The check is permissive when the
    information is missing rather than refusing on the strength of an
    absent attribute.
    """
    hass.states.async_set(MEDIA_PLAYER, "idle", {"friendly_name": "Living Room"})

    result = await _init_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def _entry_with_options_flow(hass: HomeAssistant) -> tuple:
    """Create an entry through the user step and open its options flow."""
    result = await _init_user_flow(hass)
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    return entry, await hass.config_entries.options.async_init(entry.entry_id)


async def test_options_flow_refuses_the_ma_strategy_on_a_non_ma_player(
    hass: HomeAssistant,
) -> None:
    """Forcing the Music Assistant strategy on a player it cannot reach is refused.

    `music_assistant.play_announcement` is a Music Assistant platform entity
    action: it only accepts entities Music Assistant itself provides. Forcing
    the strategy on an `apple_tv` player produces a failure on every single
    announcement, so the options form refuses the combination instead.
    """
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "media_player", "apple_tv", "living-room-uid", suggested_object_id="living_room"
    )

    entry, options_result = await _entry_with_options_flow(hass)

    result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        {
            CONF_VOLUME: 0.4,
            CONF_RESTORE_VOLUME: True,
            CONF_STRATEGY: "music_assistant",
            "announce_prefix": "",
            CONF_DENY_DOMAINS: "lock",
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_STRATEGY: "not_a_music_assistant_player"}
    assert entry.options == {}


async def test_options_flow_allows_the_ma_strategy_on_an_ma_player(
    hass: HomeAssistant,
) -> None:
    """The same choice is accepted when the target really is an MA player."""
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "media_player",
        "music_assistant",
        "living-room-uid",
        suggested_object_id="living_room",
    )

    entry, options_result = await _entry_with_options_flow(hass)

    result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        {
            CONF_VOLUME: 0.4,
            CONF_RESTORE_VOLUME: True,
            CONF_STRATEGY: "music_assistant",
            "announce_prefix": "",
            CONF_DENY_DOMAINS: "lock",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_STRATEGY] == "music_assistant"


async def test_options_flow_allows_the_ma_strategy_for_an_unregistered_player(
    hass: HomeAssistant,
) -> None:
    """A player absent from the entity registry is not evidence either.

    Same permissiveness as the `PLAY_MEDIA` check: the strategy is only
    refused when the registry positively says the player belongs to some
    other integration.
    """
    entry, options_result = await _entry_with_options_flow(hass)

    result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        {
            CONF_VOLUME: 0.4,
            CONF_RESTORE_VOLUME: True,
            CONF_STRATEGY: "music_assistant",
            "announce_prefix": "",
            CONF_DENY_DOMAINS: "lock",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_STRATEGY] == "music_assistant"
