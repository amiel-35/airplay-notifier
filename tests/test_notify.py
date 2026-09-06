"""Tests for the AirPlay Notifier notify platform (legacy service + entity)."""

from __future__ import annotations

from datetime import timedelta

import pytest
import voluptuous as vol
from homeassistant.components.notify.legacy import NOTIFY_SERVICES
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.airplay_notifier import notify as airplay_notify
from custom_components.airplay_notifier.const import (
    CONF_ANNOUNCE_PREFIX,
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


async def test_deny_list_refuses_call(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A call whose source_entity is in deny_domains is refused, nothing spoken.

    The refusal surfaces to the caller as a `ServiceValidationError` *and* is
    logged, so a misconfigured automation fails loudly instead of silently
    doing nothing.
    """
    hass.states.async_set(MEDIA_PLAYER, "idle", {})

    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    speak_calls = async_mock_service(hass, "tts", "speak")

    with pytest.raises(ServiceValidationError):
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
    assert "deny_domains" in caplog.text


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


async def test_legacy_service_uses_live_options_after_reload(
    hass: HomeAssistant,
) -> None:
    """Changing an option and reloading changes what the legacy service speaks.

    Regression test for the stale-service bug: `discovery.async_load_platform`
    is re-dispatched on every setup, but core's
    `BaseNotificationService.async_register_services` returns early when the
    service name already exists, so the *first* instance keeps serving the
    `notify.airplay_<name>` service forever. If that instance captured the
    options by value, an options change would never take effect.
    """
    hass.states.async_set(MEDIA_PLAYER, "idle", {})

    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    speak_calls = async_mock_service(hass, "tts", "speak")

    hass.config_entries.async_update_entry(
        entry, options={CONF_ANNOUNCE_PREFIX: "Attention."}
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        "notify",
        "airplay_living_room",
        {"message": "Dinner is ready"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(speak_calls) == 1
    assert speak_calls[0].data["message"] == "Attention. Dinner is ready"


async def test_legacy_service_is_removed_on_entry_removal(
    hass: HomeAssistant,
) -> None:
    """Removing the entry unregisters notify.airplay_<name> and its instance."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service("notify", "airplay_living_room")
    assert hass.data[NOTIFY_SERVICES][DOMAIN]

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.services.has_service("notify", "airplay_living_room")
    assert not hass.data[NOTIFY_SERVICES].get(DOMAIN)


async def test_legacy_service_re_registers_after_reload(hass: HomeAssistant) -> None:
    """A reload leaves exactly one live service instance behind, not two."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service("notify", "airplay_living_room")
    assert len(hass.data[NOTIFY_SERVICES][DOMAIN]) == 1


async def test_no_volume_restore_timer_survives_unload(hass: HomeAssistant) -> None:
    """Unloading the entry disarms the pending volume restore.

    The restore is scheduled, not awaited, so without an
    `entry.async_on_unload` hook it would still fire seconds later and move
    the speaker's volume on behalf of an integration that is no longer there.
    """
    hass.states.async_set(MEDIA_PLAYER, "idle", {"volume_level": 0.3})

    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    async_mock_service(hass, "tts", "speak")
    volume_calls = async_mock_service(hass, "media_player", "volume_set")

    await hass.services.async_call(
        "notify",
        "airplay_living_room",
        {"message": "Loud", "data": {"volume": 0.9}},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert len(volume_calls) == 1

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()

    assert len(volume_calls) == 1


async def test_two_entries_get_distinct_entities_and_devices(
    hass: HomeAssistant,
) -> None:
    """Each entry owns one device and one entity named after it.

    With the previous hard-coded `_attr_name = "Speak"` and no device, both
    entries produced `notify.speak` / `notify.speak_2` — indistinguishable in
    the UI and unstable in ordering.
    """
    first = _make_entry()
    first.add_to_hass(hass)
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Kitchen HomePod",
        unique_id="media_player.kitchen",
        data={
            CONF_MEDIA_PLAYER: "media_player.kitchen",
            CONF_TTS_ENTITY: TTS_ENTITY,
        },
    )
    second.add_to_hass(hass)

    # Setting up the first entry loads the component, which sets up every
    # other entry of the domain too.
    assert await hass.config_entries.async_setup(first.entry_id)
    await hass.async_block_till_done()
    assert second.state is ConfigEntryState.LOADED

    assert sorted(hass.states.async_entity_ids("notify")) == [
        "notify.kitchen_homepod",
        "notify.living_room",
    ]

    devices = dr.async_get(hass)
    for entry, title in ((first, "Living Room"), (second, "Kitchen HomePod")):
        device = devices.async_get_device_by_identifier(
            (DOMAIN, entry.entry_id), entry.entry_id
        )
        assert device is not None
        assert device.name == title


async def test_legacy_service_reports_an_unloaded_entry(hass: HomeAssistant) -> None:
    """Speaking through a service whose entry is gone is a clear error.

    Looking the entry up on every call (the fix for stale options) means the
    lookup can now come back empty — after an unload, or if the entry was
    deleted while a call was in flight.
    """
    service = airplay_notify.AirplayNotifierNotificationService(hass, "gone")

    with pytest.raises(HomeAssistantError) as err:
        await service.async_send_message("Hello")

    assert err.value.translation_key == "entry_not_loaded"


async def test_async_get_service_without_discovery_info(hass: HomeAssistant) -> None:
    """`async_get_service` refuses YAML-style setup (no discovery_info)."""
    assert await airplay_notify.async_get_service(hass, {}) is None


async def test_async_get_service_with_unknown_entry(hass: HomeAssistant) -> None:
    """`async_get_service` refuses an entry_id that is not loaded."""
    result = await airplay_notify.async_get_service(
        hass, {}, discovery_info={"entry_id": "does-not-exist"}
    )
    assert result is None
