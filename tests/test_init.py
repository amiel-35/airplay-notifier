"""Tests for the AirPlay Notifier manifest and config-entry migration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.airplay_notifier import async_migrate_entry
from custom_components.airplay_notifier.config_flow import AirplayNotifierConfigFlow
from custom_components.airplay_notifier.const import (
    CONF_MEDIA_PLAYER,
    CONF_TTS_ENTITY,
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


def test_manifest_keys_and_lists_are_sorted() -> None:
    """`domain`, `name`, then alphabetical — hassfest's own ordering."""
    manifest = _manifest()
    keys = list(manifest)
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])
    for key in ("after_dependencies", "codeowners", "dependencies", "requirements"):
        assert manifest[key] == sorted(manifest[key]), key


async def test_migrate_entry_is_a_no_op_for_the_current_version(
    hass: HomeAssistant,
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
