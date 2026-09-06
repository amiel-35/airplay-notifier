"""Tests for custom_components.airplay_notifier.delivery."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.airplay_notifier.const import (
    DOMAIN,
    STRATEGY_AUTO,
    STRATEGY_DIRECT,
)
from custom_components.airplay_notifier.delivery import (
    AirplayNotifierOptions,
    AnnouncementDenied,
    async_deliver_message,
)

DIRECT_PLAYER = "media_player.apple_tv"
TTS_ENTITY = "tts.piper"


def _options(**overrides: object) -> AirplayNotifierOptions:
    base = AirplayNotifierOptions(
        media_player=DIRECT_PLAYER,
        tts_entity=TTS_ENTITY,
        strategy=STRATEGY_AUTO,
        deny_domains=["alarm_control_panel", "lock"],
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


async def test_direct_strategy_calls_tts_speak(hass: HomeAssistant) -> None:
    """A plain media player (not Music Assistant) is spoken to via tts.speak."""
    hass.states.async_set(DIRECT_PLAYER, "idle", {})
    speak_calls = async_mock_service(hass, "tts", "speak")

    await async_deliver_message(hass, _options(), "Hello there")

    assert len(speak_calls) == 1
    assert speak_calls[0].data == {
        "entity_id": TTS_ENTITY,
        "media_player_entity_id": DIRECT_PLAYER,
        "message": "Hello there",
        "cache": True,
    }


async def test_announce_prefix_is_prepended(hass: HomeAssistant) -> None:
    """`announce_prefix` is spoken before the message."""
    hass.states.async_set(DIRECT_PLAYER, "idle", {})
    speak_calls = async_mock_service(hass, "tts", "speak")

    await async_deliver_message(
        hass, _options(announce_prefix="Attention."), "Dinner is ready"
    )

    assert speak_calls[0].data["message"] == "Attention. Dinner is ready"


async def test_per_call_overrides_language_and_voice(hass: HomeAssistant) -> None:
    """`data` overrides win over the entry's configured language/voice."""
    hass.states.async_set(DIRECT_PLAYER, "idle", {})
    speak_calls = async_mock_service(hass, "tts", "speak")

    await async_deliver_message(
        hass,
        _options(language="en", voice="default"),
        "Bonjour",
        {"language": "fr", "voice": "fr-voice"},
    )

    assert speak_calls[0].data["language"] == "fr"
    assert speak_calls[0].data["options"] == {"voice": "fr-voice"}


async def test_deny_list_raises(hass: HomeAssistant) -> None:
    """A denied source_entity domain raises AnnouncementDenied and speaks nothing."""
    hass.states.async_set(DIRECT_PLAYER, "idle", {})
    speak_calls = async_mock_service(hass, "tts", "speak")

    with pytest.raises(AnnouncementDenied):
        await async_deliver_message(
            hass,
            _options(),
            "Front door is unlocked",
            {"source_entity": "lock.front_door"},
        )

    assert len(speak_calls) == 0


@pytest.mark.parametrize(
    "source_entity",
    [
        pytest.param(["lock.front_door"], id="list"),
        pytest.param(["sensor.ok", "lock.front_door"], id="list-with-allowed-entry"),
        pytest.param("LOCK.Front_Door", id="upper-case"),
        pytest.param(["ALARM_CONTROL_PANEL.Home"], id="upper-case-in-list"),
        pytest.param(("lock.front_door",), id="tuple"),
    ],
    # `source_entity` was compared raw against `deny_domains`, so a list (the
    # shape every HA `entity_id` field accepts) or any capitalisation walked
    # straight past the deny-list and got spoken.
)
async def test_deny_list_normalises_source_entity(
    hass: HomeAssistant, source_entity: object
) -> None:
    """A denied domain is caught whatever shape/case `source_entity` arrives in."""
    hass.states.async_set(DIRECT_PLAYER, "idle", {})
    speak_calls = async_mock_service(hass, "tts", "speak")

    with pytest.raises(AnnouncementDenied):
        await async_deliver_message(
            hass, _options(), "Front door", {"source_entity": source_entity}
        )

    assert len(speak_calls) == 0


async def test_deny_list_honours_upper_case_deny_domains(hass: HomeAssistant) -> None:
    """A deny-list entry typed in upper case still matches."""
    hass.states.async_set(DIRECT_PLAYER, "idle", {})
    speak_calls = async_mock_service(hass, "tts", "speak")

    with pytest.raises(AnnouncementDenied):
        await async_deliver_message(
            hass,
            _options(deny_domains=["Lock"]),
            "Front door",
            {"source_entity": "lock.front_door"},
        )

    assert len(speak_calls) == 0


@pytest.mark.parametrize(
    "source_entity",
    [
        pytest.param("garbage", id="no-dot"),
        pytest.param("", id="empty"),
        pytest.param("lock.", id="no-object-id"),
        pytest.param(["lock.front_door", "garbage"], id="one-bad-entry"),
        pytest.param(42, id="not-a-string"),
    ],
)
async def test_unusable_source_entity_is_refused_not_ignored(
    hass: HomeAssistant, source_entity: object
) -> None:
    """A `source_entity` that is not `domain.object_id` refuses the call."""
    hass.states.async_set(DIRECT_PLAYER, "idle", {})
    speak_calls = async_mock_service(hass, "tts", "speak")

    with pytest.raises(AnnouncementDenied):
        await async_deliver_message(
            hass, _options(), "Something", {"source_entity": source_entity}
        )

    assert len(speak_calls) == 0


async def test_allowed_source_entity_still_speaks(hass: HomeAssistant) -> None:
    """A well-formed `source_entity` outside the deny-list is spoken normally."""
    hass.states.async_set(DIRECT_PLAYER, "idle", {})
    speak_calls = async_mock_service(hass, "tts", "speak")

    await async_deliver_message(
        hass,
        _options(),
        "Dishwasher finished",
        {"source_entity": ["binary_sensor.Dishwasher_Done"]},
    )

    assert len(speak_calls) == 1


async def test_deny_list_raises_service_validation_error(hass: HomeAssistant) -> None:
    """A refusal is a `ServiceValidationError` carrying a translation key."""
    hass.states.async_set(DIRECT_PLAYER, "idle", {})
    async_mock_service(hass, "tts", "speak")

    with pytest.raises(ServiceValidationError) as err:
        await async_deliver_message(
            hass,
            _options(),
            "Armed away",
            {"source_entity": "alarm_control_panel.home"},
        )

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "source_domain_denied"


async def test_volume_is_set_and_restored(hass: HomeAssistant) -> None:
    """Direct strategy sets volume before speaking and restores it afterward."""
    hass.states.async_set(DIRECT_PLAYER, "idle", {"volume_level": 0.3})
    speak_calls = async_mock_service(hass, "tts", "speak")
    volume_calls = async_mock_service(hass, "media_player", "volume_set")

    await async_deliver_message(hass, _options(volume=0.9, restore_volume=True), "Loud")
    await hass.async_block_till_done()

    assert len(speak_calls) == 1
    assert len(volume_calls) == 1
    assert volume_calls[0].data["volume_level"] == 0.9

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()

    assert len(volume_calls) == 2
    assert volume_calls[1].data["volume_level"] == 0.3


async def test_volume_not_restored_when_disabled(hass: HomeAssistant) -> None:
    """restore_volume=False leaves the volume as set."""
    hass.states.async_set(DIRECT_PLAYER, "idle", {"volume_level": 0.3})
    async_mock_service(hass, "tts", "speak")
    volume_calls = async_mock_service(hass, "media_player", "volume_set")

    await async_deliver_message(
        hass, _options(volume=0.9, restore_volume=False), "Loud"
    )
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()

    assert len(volume_calls) == 1


async def test_auto_strategy_detects_music_assistant(hass: HomeAssistant) -> None:
    """A media player owned by the music_assistant platform uses that strategy."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        "media_player",
        "music_assistant",
        "unique-ma-speaker",
        suggested_object_id="ma_speaker",
    )
    hass.states.async_set(entry.entity_id, "idle", {})

    announce_calls = async_mock_service(hass, "music_assistant", "play_announcement")

    with patch(
        "custom_components.airplay_notifier.delivery._async_resolve_tts_url",
        return_value="https://example.local/tts.mp3",
    ):
        await async_deliver_message(
            hass, _options(media_player=entry.entity_id, volume=0.5), "Pizza is here"
        )

    assert len(announce_calls) == 1
    assert announce_calls[0].data == {
        "entity_id": entry.entity_id,
        "url": "https://example.local/tts.mp3",
        "announce_volume": 50,
    }


async def test_explicit_direct_strategy_overrides_music_assistant_detection(
    hass: HomeAssistant,
) -> None:
    """Forcing `direct` skips the Music Assistant auto-detection."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        "media_player",
        "music_assistant",
        "unique-ma-speaker-2",
        suggested_object_id="ma_speaker_2",
    )
    hass.states.async_set(entry.entity_id, "idle", {})

    speak_calls = async_mock_service(hass, "tts", "speak")
    announce_calls = async_mock_service(hass, "music_assistant", "play_announcement")

    await async_deliver_message(
        hass,
        _options(media_player=entry.entity_id, strategy=STRATEGY_DIRECT),
        "Forced direct",
    )

    assert len(speak_calls) == 1
    assert len(announce_calls) == 0
