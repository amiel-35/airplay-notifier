"""Tests for AirPlay Notifier diagnostics."""

from __future__ import annotations

from homeassistant.core import HomeAssistant, ServiceCall
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.airplay_notifier.config_flow import AirplayNotifierConfigFlow
from custom_components.airplay_notifier.const import (
    CONF_MEDIA_PLAYER,
    CONF_TTS_ENTITY,
    DOMAIN,
)
from custom_components.airplay_notifier.diagnostics import (
    TO_REDACT,
    async_get_config_entry_diagnostics,
)

MEDIA_PLAYER = "media_player.living_room"
TTS_ENTITY = "tts.piper"


async def test_diagnostics_reports_resolved_options(
    hass: HomeAssistant, targets: None
) -> None:
    """Diagnostics expose the entry data/options and the resolved settings."""
    async_mock_service(hass, "tts", "speak")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living Room",
        unique_id=MEDIA_PLAYER,
        data={CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["loaded"] is True
    assert diagnostics["legacy_service_name"] == "airplay_living_room"
    assert diagnostics["legacy_service_registered"] is True
    assert diagnostics["resolved_options"]["media_player"] == MEDIA_PLAYER
    assert diagnostics["resolved_options"]["tts_entity"] == TTS_ENTITY
    assert diagnostics["entry"]["version"] == AirplayNotifierConfigFlow.VERSION
    assert (
        diagnostics["entry"]["minor_version"] == AirplayNotifierConfigFlow.MINOR_VERSION
    )


async def test_diagnostics_without_runtime_data(hass: HomeAssistant) -> None:
    """Diagnostics still work for an entry that is not loaded.

    Core deletes `entry.runtime_data` on unload, and a broken entry is
    exactly when someone downloads diagnostics: reading it unguarded raised
    `AttributeError` and produced no diagnostics at all.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living Room",
        unique_id=MEDIA_PLAYER,
        data={CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )
    entry.add_to_hass(hass)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["loaded"] is False
    assert diagnostics["resolved_options"] is None
    assert diagnostics["legacy_service_name"] is None
    assert diagnostics["legacy_service_registered"] is None
    assert diagnostics["data"][CONF_MEDIA_PLAYER] == MEDIA_PLAYER


async def test_diagnostics_reports_a_name_this_entry_does_not_own(
    hass: HomeAssistant, targets: None
) -> None:
    """`legacy_service_name` alone cannot tell you whether the service is ours.

    An entry whose name was already taken loads normally and still reports
    the name it wanted — core's
    `BaseNotificationService.async_register_services` returns early in
    silence (`homeassistant/components/notify/legacy.py:312`), so
    `hass.services.has_service` is true either way and says nothing about
    who owns it. The ownership map is the only answer, and it is exactly
    what someone reading a diagnostics download for "my alert stopped
    speaking" needs to see.
    """

    async def _foreign_service(call: ServiceCall) -> None:
        """Stand in for whatever already owns the name."""

    hass.services.async_register("notify", "airplay_living_room", _foreign_service)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living Room",
        unique_id=MEDIA_PLAYER,
        data={CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["loaded"] is True
    assert diagnostics["legacy_service_name"] == "airplay_living_room"
    assert diagnostics["legacy_service_registered"] is False
    assert hass.services.has_service("notify", "airplay_living_room")


def test_diagnostics_redaction_set_is_documented_empty() -> None:
    """`TO_REDACT` is deliberately empty; nothing sensitive is ever stored."""
    assert not TO_REDACT
