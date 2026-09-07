"""Tests for the AirPlay Notifier notify platform (legacy service + entity)."""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path

import pytest
import voluptuous as vol
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.notify.legacy import NOTIFY_SERVICES
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
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
INTEGRATION_DIR = (
    Path(__file__).resolve().parents[1] / "custom_components" / "airplay_notifier"
)


def _make_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Living Room",
        unique_id=MEDIA_PLAYER,
        data={CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )


async def test_setup_registers_legacy_service(
    hass: HomeAssistant, targets: None
) -> None:
    """Setting up the entry registers notify.airplay_living_room."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service("notify", "airplay_living_room")


async def test_setup_registers_notify_entity(
    hass: HomeAssistant, targets: None
) -> None:
    """Setting up the entry also registers a NotifyEntity."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.async_entity_ids("notify")


async def test_legacy_service_speaks_direct(hass: HomeAssistant, targets: None) -> None:
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


async def test_notify_entity_send_message_speaks(
    hass: HomeAssistant, targets: None
) -> None:
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
    hass: HomeAssistant, targets: None, caplog: pytest.LogCaptureFixture
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
    hass: HomeAssistant, targets: None
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
    hass: HomeAssistant, targets: None
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
    hass: HomeAssistant, targets: None
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


async def test_legacy_service_re_registers_after_reload(
    hass: HomeAssistant, targets: None
) -> None:
    """A reload leaves exactly one live service instance behind, not two."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service("notify", "airplay_living_room")
    assert len(hass.data[NOTIFY_SERVICES][DOMAIN]) == 1


async def test_no_volume_restore_timer_survives_unload(
    hass: HomeAssistant, targets: None
) -> None:
    """Unloading the entry performs the pending restore, then disarms it.

    The restore is scheduled, not awaited, so without an
    `entry.async_on_unload` hook it would still fire seconds later and move
    the speaker's volume on behalf of an integration that is no longer
    there. Cancelling alone is not enough either — see
    `test_options_reload_during_an_announcement_restores_the_volume` in
    test_init.py — so the hook restores first and cancels afterwards.
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

    # Restored on the spot, by the unload hook.
    assert [call.data["volume_level"] for call in volume_calls] == [0.9, 0.3]

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()

    # And the timer is gone: nothing moves the volume after the entry has.
    assert len(volume_calls) == 2


async def test_two_entries_get_distinct_entities_and_devices(
    hass: HomeAssistant, targets: None
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
    hass.states.async_set("media_player.kitchen", "idle", {})

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


async def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Set up the standard entry and return it."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_entity_follows_the_media_player_availability(
    hass: HomeAssistant, targets: None
) -> None:
    """The notify entity is unavailable while its player is.

    `entity-unavailable`: a notifier that cannot reach its speaker should
    say so in the UI rather than accept calls that are certain to fail.
    """
    await _setup_entry(hass)
    notify_entity = hass.states.async_entity_ids("notify")[0]

    assert hass.states.get(notify_entity).state != STATE_UNAVAILABLE

    hass.states.async_set(MEDIA_PLAYER, STATE_UNAVAILABLE, {})
    await hass.async_block_till_done()
    assert hass.states.get(notify_entity).state == STATE_UNAVAILABLE

    hass.states.async_set(MEDIA_PLAYER, "idle", {})
    await hass.async_block_till_done()
    assert hass.states.get(notify_entity).state != STATE_UNAVAILABLE


async def test_entity_follows_the_tts_entity_availability(
    hass: HomeAssistant, targets: None
) -> None:
    """A removed TTS engine makes the notifier unavailable too.

    Both targets are needed to speak, so either one going away is enough.
    """
    await _setup_entry(hass)
    notify_entity = hass.states.async_entity_ids("notify")[0]

    hass.states.async_remove(TTS_ENTITY)
    await hass.async_block_till_done()
    assert hass.states.get(notify_entity).state == STATE_UNAVAILABLE

    hass.states.async_set(TTS_ENTITY, "unknown", {})
    await hass.async_block_till_done()
    assert hass.states.get(notify_entity).state != STATE_UNAVAILABLE


async def test_unavailability_is_logged_once_and_recovery_once(
    hass: HomeAssistant, targets: None, caplog: pytest.LogCaptureFixture
) -> None:
    """`log-when-unavailable`: one line per transition, not per state change.

    The core pattern is to log on the transition only. A speaker that
    flaps, or one whose other attributes keep changing while it is
    unavailable, must not fill the log with the same line.
    """
    await _setup_entry(hass)
    caplog.clear()
    caplog.set_level(logging.INFO)

    hass.states.async_set(MEDIA_PLAYER, STATE_UNAVAILABLE, {})
    await hass.async_block_till_done()
    hass.states.async_set(MEDIA_PLAYER, STATE_UNAVAILABLE, {"attribution": "changed"})
    await hass.async_block_till_done()
    hass.states.async_remove(TTS_ENTITY)
    await hass.async_block_till_done()

    assert caplog.text.count("is unavailable") == 1

    hass.states.async_set(TTS_ENTITY, "unknown", {})
    await hass.async_block_till_done()
    hass.states.async_set(MEDIA_PLAYER, "idle", {})
    await hass.async_block_till_done()
    hass.states.async_set(MEDIA_PLAYER, "playing", {})
    await hass.async_block_till_done()

    assert caplog.text.count("is available again") == 1

    # And the cycle can repeat: the flag is reset, not latched.
    hass.states.async_set(MEDIA_PLAYER, STATE_UNAVAILABLE, {})
    await hass.async_block_till_done()
    assert caplog.text.count("is unavailable") == 2


async def test_the_entity_declares_a_translation_key_for_its_icon(
    hass: HomeAssistant, targets: None
) -> None:
    """`icon-translations`: icons.json is keyed on the entity's translation key.

    The key exists for the icon only — `_attr_name = None` short-circuits
    `Entity._name_internal` before any name lookup, so it can never name
    the entity. A key here with no matching `icons.json` entry (or the
    reverse) is a silently missing icon, so both ends are pinned.
    """
    await _setup_entry(hass)
    notify_entity = hass.states.async_entity_ids("notify")[0]

    registry_entry = er.async_get(hass).async_get(notify_entity)
    assert registry_entry is not None
    translation_key = registry_entry.translation_key
    assert translation_key is not None

    icons = json.loads((INTEGRATION_DIR / "icons.json").read_text(encoding="utf-8"))
    assert icons["entity"]["notify"][translation_key]["default"].startswith("mdi:")

    # The key names the icon, never the entity: the name still comes from
    # the device, exactly as before the key existed.
    assert hass.states.get(notify_entity).attributes["friendly_name"] == "Living Room"


async def test_legacy_service_name_comes_from_the_title_not_the_entity_id(
    hass: HomeAssistant, targets: None
) -> None:
    """`notify.airplay_<slugify(title)>`, and nothing else.

    The distinction matters because the two differ in practice: this entry
    is titled after the player's friendly name ("Living Room") while its
    entity id is `media_player.lounge_atv_2`. The service name is what
    `alert.notifiers:` refers to, so which of the two it follows is part
    of the integration's contract, not an implementation detail.
    """
    hass.states.async_set("media_player.lounge_atv_2", "idle", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living Room",
        unique_id="media_player.lounge_atv_2",
        data={
            CONF_MEDIA_PLAYER: "media_player.lounge_atv_2",
            CONF_TTS_ENTITY: TTS_ENTITY,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service("notify", "airplay_living_room")
    assert not hass.services.has_service("notify", "airplay_lounge_atv_2")
    assert entry.runtime_data.legacy_service_name == "airplay_living_room"


async def test_two_entries_with_the_same_title_get_distinct_services(
    hass: HomeAssistant, targets: None
) -> None:
    """Two players sharing a friendly name still get one service each.

    Core's `BaseNotificationService.async_register_services` returns early
    when the service name already exists, so without a suffix the second
    entry would silently have no legacy service at all — and unloading the
    first would remove the one service both were sharing.
    """
    hass.states.async_set("media_player.bedroom_left", "idle", {})
    hass.states.async_set("media_player.bedroom_right", "idle", {})
    entries = []
    for object_id in ("bedroom_left", "bedroom_right"):
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Bedroom",
            unique_id=f"media_player.{object_id}",
            data={
                CONF_MEDIA_PLAYER: f"media_player.{object_id}",
                CONF_TTS_ENTITY: TTS_ENTITY,
            },
        )
        entry.add_to_hass(hass)
        entries.append(entry)

    assert await hass.config_entries.async_setup(entries[0].entry_id)
    await hass.async_block_till_done()

    assert entries[0].runtime_data.legacy_service_name == "airplay_bedroom"
    assert entries[1].runtime_data.legacy_service_name == "airplay_bedroom_2"
    assert hass.services.has_service("notify", "airplay_bedroom")
    assert hass.services.has_service("notify", "airplay_bedroom_2")

    # Deterministic: a reload gives the same entry the same name back.
    await hass.config_entries.async_reload(entries[1].entry_id)
    await hass.async_block_till_done()
    assert entries[1].runtime_data.legacy_service_name == "airplay_bedroom_2"
    assert hass.services.has_service("notify", "airplay_bedroom")
    assert hass.services.has_service("notify", "airplay_bedroom_2")

    # And each service reaches its own player.
    speak_calls = async_mock_service(hass, "tts", "speak")
    await hass.services.async_call(
        "notify", "airplay_bedroom_2", {"message": "Ready"}, blocking=True
    )
    await hass.async_block_till_done()
    assert speak_calls[0].data["media_player_entity_id"] == "media_player.bedroom_right"


async def test_the_entity_is_refused_during_quiet_hours(
    hass: HomeAssistant, targets: None, freezer: FrozenDateTimeFactory
) -> None:
    """Quiet hours apply to the modern surface too.

    The entity cannot carry `data`, so it can neither mark a call
    `critical` nor override the volume — but the window itself is entry
    configuration, so it is honoured, with the entry's own quiet volume
    when one is set.
    """
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-01-15 23:30:00+00:00")

    entry = _make_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, options={"quiet_start": "22:00:00", "quiet_end": "07:00:00"}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    speak_calls = async_mock_service(hass, "tts", "speak")
    notify_entity = hass.states.async_entity_ids("notify")[0]

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "notify",
            "send_message",
            {"entity_id": notify_entity, "message": "Dishwasher finished"},
            blocking=True,
        )

    assert err.value.translation_key == "quiet_hours"
    assert not speak_calls
