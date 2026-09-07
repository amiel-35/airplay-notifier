"""Fixtures shared by the AirPlay Notifier test suite."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from homeassistant.components.media_player.const import MediaPlayerEntityFeature
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant

pytest_plugins = "pytest_homeassistant_custom_component"

MEDIA_PLAYER = "media_player.living_room"
TTS_ENTITY = "tts.piper"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Make custom_components discoverable in every test."""
    yield


@pytest.fixture
def targets(hass: HomeAssistant) -> None:
    """Put the entry's two targets in the state machine.

    `async_setup_entry` raises `ConfigEntryNotReady` when the configured
    `media_player` or TTS entity is missing (the `test-before-setup` rule),
    so any test that expects an entry to *load* has to provide both.
    """
    hass.states.async_set(
        MEDIA_PLAYER,
        "idle",
        {ATTR_SUPPORTED_FEATURES: MediaPlayerEntityFeature.PLAY_MEDIA},
    )
    hass.states.async_set(TTS_ENTITY, "unknown", {})
