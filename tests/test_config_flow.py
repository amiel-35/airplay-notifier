"""Tests for the AirPlay Notifier config and options flows."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.const import CONF_LANGUAGE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

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
