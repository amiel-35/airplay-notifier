"""Tests for the AirPlay Notifier manifest and config-entry migration."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.airplay_notifier import (
    LEGACY_SERVICE_OWNERS,
    _async_remove_legacy_service,
    _derives_from,
    async_migrate_entry,
)
from custom_components.airplay_notifier.config_flow import AirplayNotifierConfigFlow
from custom_components.airplay_notifier.const import (
    CONF_MEDIA_PLAYER,
    CONF_TTS_ENTITY,
    CONF_VOLUME,
    DOMAIN,
)

MANIFEST_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "airplay_notifier"
    / "manifest.json"
)

MEDIA_PLAYER = "media_player.living_room"
TTS_ENTITY = "tts.piper"


def _manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text())


def test_manifest_declares_a_helper_with_no_polling() -> None:
    """This integration owns no device and reaches out to nothing.

    It derives a notify surface from entities other integrations already
    provide, which is `integration_type: helper` and `iot_class: calculated`
    — not `device` / `local_push`, which claimed a device this integration
    does not have and push updates it never receives.
    """
    manifest = _manifest()
    assert manifest["integration_type"] == "helper"
    assert manifest["iot_class"] == "calculated"


def test_a_double_suffix_does_not_derive_from_the_base() -> None:
    """`base_2_2` is not a numbered variant of `base`: `"2_2".isdigit()` is False."""
    assert not _derives_from("base_2_2", "base")


def test_manifest_keys_and_lists_are_sorted() -> None:
    """`domain`, `name`, then alphabetical — hassfest's own ordering."""
    manifest = _manifest()
    keys = list(manifest)
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])
    for key in ("after_dependencies", "codeowners", "dependencies", "requirements"):
        assert manifest[key] == sorted(manifest[key]), key


async def test_removing_the_legacy_service_tolerates_a_missing_registry(
    hass: HomeAssistant,
) -> None:
    """The unload hook is safe when `notify` never registered anything.

    `hass.data[NOTIFY_SERVICES]` only exists once the notify component has
    set up its legacy machinery, and the hook can run on a teardown path
    where setup never got that far.
    """
    hass.data[LEGACY_SERVICE_OWNERS] = {"airplay_nothing": "entry-1"}

    _async_remove_legacy_service(hass, "entry-1", "airplay_nothing")

    assert not hass.services.has_service("notify", "airplay_nothing")
    assert hass.data[LEGACY_SERVICE_OWNERS] == {}


async def test_migrate_entry_is_a_no_op_for_the_current_version(
    hass: HomeAssistant, targets: None
) -> None:
    """A 1.1 entry migrates trivially and keeps its data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living Room",
        unique_id=MEDIA_PLAYER,
        version=AirplayNotifierConfigFlow.VERSION,
        minor_version=AirplayNotifierConfigFlow.MINOR_VERSION,
        data={CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True
    assert dict(entry.data) == {
        CONF_MEDIA_PLAYER: MEDIA_PLAYER,
        CONF_TTS_ENTITY: TTS_ENTITY,
    }

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_migrate_entry_refuses_a_future_major_version(
    hass: HomeAssistant,
) -> None:
    """An entry written by a newer version fails migration instead of loading."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living Room",
        unique_id=MEDIA_PLAYER,
        version=AirplayNotifierConfigFlow.VERSION + 1,
        data={CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is False

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


async def test_options_reload_during_an_announcement_restores_the_volume(
    hass: HomeAssistant, targets: None
) -> None:
    """A reload inside the announcement window performs the pending restore.

    Changing an option reloads the entry, which runs the `async_on_unload`
    hooks. Cancelling the armed restore there and stopping was silently
    destructive: the reloaded entry starts from a fresh, empty
    `VolumeRestoreState`, so nothing was left to put the speaker back down
    and it stayed at announcement volume for good.
    """
    hass.states.async_set(MEDIA_PLAYER, "idle", {"volume_level": 0.3})

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living Room",
        unique_id=MEDIA_PLAYER,
        data={CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
        options={CONF_VOLUME: 0.9},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    async_mock_service(hass, "tts", "speak")
    volume_calls = async_mock_service(hass, "media_player", "volume_set")

    await hass.services.async_call(
        "notify", "airplay_living_room", {"message": "Dinner is ready"}, blocking=True
    )
    await hass.async_block_till_done()

    volume_state = entry.runtime_data.volume_state
    assert [call.data["volume_level"] for call in volume_calls] == [0.9]
    assert volume_state.cancel_restore is not None

    # The user changes an option mid-announcement.
    hass.config_entries.async_update_entry(entry, options={CONF_VOLUME: 0.5})
    await hass.async_block_till_done()

    assert [call.data["volume_level"] for call in volume_calls] == [0.9, 0.3]
    assert volume_state.cancel_restore is None

    # And the cancelled timer does not fire a second, stale restore.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()
    assert len(volume_calls) == 2


async def test_unload_restores_a_pending_volume(
    hass: HomeAssistant, targets: None
) -> None:
    """Deleting/unloading an entry mid-announcement also gives the volume back."""
    hass.states.async_set(MEDIA_PLAYER, "idle", {"volume_level": 0.3})

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living Room",
        unique_id=MEDIA_PLAYER,
        data={CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
        options={CONF_VOLUME: 0.9},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    async_mock_service(hass, "tts", "speak")
    volume_calls = async_mock_service(hass, "media_player", "volume_set")

    await hass.services.async_call(
        "notify", "airplay_living_room", {"message": "Dinner is ready"}, blocking=True
    )
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert [call.data["volume_level"] for call in volume_calls] == [0.9, 0.3]
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_migrate_entry_refuses_a_future_minor_version(
    hass: HomeAssistant,
) -> None:
    """A *minor* downgrade is refused too, not silently loaded.

    A minor bump is backwards-compatible forwards only: an entry written by
    a newer AirPlay Notifier may carry option keys this code does not
    understand, and loading it anyway would quietly drop them on the next
    options save.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living Room",
        unique_id=MEDIA_PLAYER,
        version=AirplayNotifierConfigFlow.VERSION,
        minor_version=AirplayNotifierConfigFlow.MINOR_VERSION + 1,
        data={CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is False

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


async def test_setup_retries_while_the_media_player_is_missing(
    hass: HomeAssistant,
) -> None:
    """A configured target that is not in the state machine defers setup.

    `test-before-setup`: the entry must not load into a state where every
    announcement is going to fail. `ConfigEntryNotReady` makes Home
    Assistant retry with a backoff, which is exactly right for a target
    whose own integration simply has not finished starting up.
    """
    hass.states.async_set(TTS_ENTITY, "unknown", {})

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living Room",
        unique_id=MEDIA_PLAYER,
        data={CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.error_reason_translation_key == "target_unavailable"
    assert entry.error_reason_translation_placeholders == {"entities": MEDIA_PLAYER}
    # Nothing half-registered: no legacy service for an entry that never loaded.
    assert not hass.services.has_service("notify", "airplay_living_room")


async def test_setup_retries_while_the_tts_entity_is_missing(
    hass: HomeAssistant,
) -> None:
    """The TTS engine is a target too: without it nothing can be spoken."""
    hass.states.async_set(MEDIA_PLAYER, "idle", {})

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living Room",
        unique_id=MEDIA_PLAYER,
        data={CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.error_reason_translation_placeholders == {"entities": TTS_ENTITY}


async def test_setup_reports_both_missing_targets_at_once(
    hass: HomeAssistant,
) -> None:
    """Both targets missing is one message naming both, not a guessing game."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living Room",
        unique_id=MEDIA_PLAYER,
        data={CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.error_reason_translation_placeholders == {
        "entities": f"{MEDIA_PLAYER}, {TTS_ENTITY}"
    }


async def test_an_unavailable_target_still_loads_the_entry(
    hass: HomeAssistant,
) -> None:
    """`unavailable` is not `missing`: the entry loads, the entity does not hide.

    Deferring setup for a target that is merely `unavailable` would be
    wrong — a speaker that is off overnight would keep the whole entry in
    retry, and the notify entity (which reports that unavailability, see
    test_notify.py) would never exist to report it.
    """
    hass.states.async_set(MEDIA_PLAYER, STATE_UNAVAILABLE, {})
    hass.states.async_set(TTS_ENTITY, STATE_UNAVAILABLE, {})

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living Room",
        unique_id=MEDIA_PLAYER,
        data={CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED


async def test_migrate_entry_stamps_a_1_1_entry_as_current(
    hass: HomeAssistant, targets: None
) -> None:
    """A 0.1.x entry loads unchanged and is re-stamped with the new minor.

    1.1 → 1.2 only adds the quiet-hours option keys, and an absent key
    already means "off", so there is nothing to rewrite. The stamp still
    has to move: core never bumps it on the integration's behalf
    (`homeassistant/config_entries.py`, `ConfigEntry.async_migrate`), and
    the version is what makes a later downgrade refuse an entry whose
    quiet hours this code would otherwise silently ignore.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living Room",
        unique_id=MEDIA_PLAYER,
        version=1,
        minor_version=1,
        data={CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
        options={CONF_VOLUME: 0.4},
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.minor_version == AirplayNotifierConfigFlow.MINOR_VERSION
    assert entry.options == {CONF_VOLUME: 0.4}
