"""Tests for the AirPlay Notifier notify platform (legacy service + entity)."""

from __future__ import annotations

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.airplay_notifier import notify as airplay_notify
from custom_components.airplay_notifier.const import (
    CONF_MEDIA_PLAYER,
    CONF_TTS_ENTITY,
    DOMAIN,
)

MEDIA_PLAYER = "media_player.living_room"
TTS_ENTITY = "tts.piper"


def _make_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Living Room",
        unique_id=MEDIA_PLAYER,
        data={CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )


async def test_setup_registers_legacy_service(hass: HomeAssistant) -> None:
    """Setting up the entry registers notify.airplay_living_room."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service("notify", "airplay_living_room")


async def test_setup_registers_notify_entity(hass: HomeAssistant) -> None:
    """Setting up the entry also registers a NotifyEntity."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.async_entity_ids("notify")


async def test_legacy_service_speaks_direct(hass: HomeAssistant) -> None:
    """notify.airplay_living_room calls tts.speak with the right fields."""
    hass.states.async_set(MEDIA_PLAYER, "idle", {})

    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Registered after setup: setting up the entry loads `tts` as a manifest
    # dependency, which registers the real `tts.speak` entity service and
    # would otherwise overwrite a mock registered beforehand.
    speak_calls = async_mock_service(hass, "tts", "speak")

    await hass.services.async_call(
        "notify",
        "airplay_living_room",
        {"message": "Dishwasher finished"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(speak_calls) == 1
    assert speak_calls[0].data["entity_id"] == TTS_ENTITY
    assert speak_calls[0].data["media_player_entity_id"] == MEDIA_PLAYER
    assert speak_calls[0].data["message"] == "Dishwasher finished"


async def test_notify_entity_send_message_speaks(hass: HomeAssistant) -> None:
    """The NotifyEntity's send_message speaks through the same delivery path."""
    hass.states.async_set(MEDIA_PLAYER, "idle", {})

    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    speak_calls = async_mock_service(hass, "tts", "speak")

    notify_entities = hass.states.async_entity_ids("notify")
    assert len(notify_entities) == 1

    await hass.services.async_call(
        "notify",
        "send_message",
        {"entity_id": notify_entities[0], "message": "Garage door open"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(speak_calls) == 1
    assert speak_calls[0].data["message"] == "Garage door open"


async def test_deny_list_refuses_call(hass: HomeAssistant) -> None:
    """A call whose source_entity is in deny_domains is refused, nothing spoken."""
    hass.states.async_set(MEDIA_PLAYER, "idle", {})

    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    speak_calls = async_mock_service(hass, "tts", "speak")

    await hass.services.async_call(
        "notify",
        "airplay_living_room",
        {
            "message": "Alarm is armed away",
            "data": {"source_entity": "alarm_control_panel.home"},
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(speak_calls) == 0


async def test_notify_send_message_schema_has_no_data_field(
    hass: HomeAssistant,
) -> None:
    """`notify.send_message` cannot carry `data`; only the legacy service can.

    This is a Home Assistant core constraint, not a bug in this integration
    (see the module docstring in notify.py): the modern notify entity
    service schema only accepts `message`/`title`. Locking this in as a
    test protects the deny-list documentation in README/ARCHITECTURE from
    silently going stale if core ever changes this.
    """
    hass.states.async_set(MEDIA_PLAYER, "idle", {})

    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    async_mock_service(hass, "tts", "speak")
    notify_entities = hass.states.async_entity_ids("notify")

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            "notify",
            "send_message",
            {
                "entity_id": notify_entities[0],
                "message": "Front door is unlocked",
                "data": {"source_entity": "lock.front_door"},
            },
            blocking=True,
        )


async def test_async_get_service_without_discovery_info(hass: HomeAssistant) -> None:
    """`async_get_service` refuses YAML-style setup (no discovery_info)."""
    assert await airplay_notify.async_get_service(hass, {}) is None


async def test_async_get_service_with_unknown_entry(hass: HomeAssistant) -> None:
    """`async_get_service` refuses an entry_id that is not loaded."""
    result = await airplay_notify.async_get_service(
        hass, {}, discovery_info={"entry_id": "does-not-exist"}
    )
    assert result is None
