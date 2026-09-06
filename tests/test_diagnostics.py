"""Tests for AirPlay Notifier diagnostics."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.airplay_notifier.const import (
    CONF_MEDIA_PLAYER,
    CONF_TTS_ENTITY,
    DOMAIN,
)
from custom_components.airplay_notifier.diagnostics import (
    async_get_config_entry_diagnostics,
)

MEDIA_PLAYER = "media_player.living_room"
TTS_ENTITY = "tts.piper"


async def test_diagnostics_reports_resolved_options(hass: HomeAssistant) -> None:
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

    assert diagnostics["legacy_service_name"] == "airplay_living_room"
    assert diagnostics["resolved_options"]["media_player"] == MEDIA_PLAYER
    assert diagnostics["resolved_options"]["tts_entity"] == TTS_ENTITY
