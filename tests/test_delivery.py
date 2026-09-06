"""Tests for custom_components.airplay_notifier.delivery."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.components.media_source import PlayMedia
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
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
    VolumeRestoreState,
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


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"volume": 1.5}, id="volume-above-1"),
        pytest.param({"volume": -0.1}, id="volume-below-0"),
        pytest.param({"volume": "loud"}, id="volume-not-a-number"),
        pytest.param(
            {"tts_entity": "media_player.kitchen"}, id="tts_entity-wrong-domain"
        ),
        pytest.param({"tts_entity": "not-an-entity"}, id="tts_entity-garbage"),
        pytest.param({"voice": ["a", "b"]}, id="voice-wrong-type"),
        pytest.param({"volumne": 0.5}, id="unknown-key-typo"),
    ],
)
async def test_invalid_call_data_is_refused(
    hass: HomeAssistant, data: dict[str, object]
) -> None:
    """A malformed per-call `data` payload refuses the call, loudly."""
    hass.states.async_set(DIRECT_PLAYER, "idle", {})
    speak_calls = async_mock_service(hass, "tts", "speak")

    with pytest.raises(ServiceValidationError) as err:
        await async_deliver_message(hass, _options(), "Hello", data)

    assert err.value.translation_key == "invalid_call_data"
    assert len(speak_calls) == 0


async def test_call_data_voice_may_be_a_full_options_mapping(
    hass: HomeAssistant,
) -> None:
    """`data.voice` given as a mapping is passed through as the TTS options."""
    hass.states.async_set(DIRECT_PLAYER, "idle", {})
    speak_calls = async_mock_service(hass, "tts", "speak")

    await async_deliver_message(
        hass, _options(), "Hello", {"voice": {"voice": "nova", "style": "calm"}}
    )

    assert speak_calls[0].data["options"] == {"voice": "nova", "style": "calm"}


async def test_call_data_volume_is_coerced(hass: HomeAssistant) -> None:
    """A volume given as a numeric string is accepted and coerced."""
    hass.states.async_set(DIRECT_PLAYER, "idle", {"volume_level": 0.3})
    async_mock_service(hass, "tts", "speak")
    volume_calls = async_mock_service(hass, "media_player", "volume_set")

    await async_deliver_message(hass, _options(), "Hello", {"volume": "0.4"})

    assert volume_calls[0].data["volume_level"] == 0.4

    # Let the scheduled restore run so no timer outlives the test.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()


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


def _mock_volume_set(hass: HomeAssistant, player: str) -> list[ServiceCall]:
    """Mock `media_player.volume_set` and make it move the player's state.

    A plain `async_mock_service` would leave `volume_level` frozen, which is
    exactly what hides the overlapping-announcement bug: the second call
    would keep reading the *pre-announcement* volume by accident.
    """
    calls: list[ServiceCall] = []

    async def _handler(call: ServiceCall) -> None:
        calls.append(call)
        state = hass.states.get(player)
        attributes = dict(state.attributes) if state is not None else {}
        attributes["volume_level"] = call.data["volume_level"]
        hass.states.async_set(
            player, state.state if state is not None else "idle", attributes
        )

    hass.services.async_register("media_player", "volume_set", _handler)
    return calls


async def test_volume_is_restored_when_speaking_fails(hass: HomeAssistant) -> None:
    """A failing `tts.speak` still gives the player its volume back.

    Without the `try/finally` the restore was simply skipped and the speaker
    stayed at announcement volume until someone noticed.
    """
    hass.states.async_set(DIRECT_PLAYER, "idle", {"volume_level": 0.3})
    async_mock_service(
        hass, "tts", "speak", raise_exception=HomeAssistantError("engine exploded")
    )
    volume_calls = _mock_volume_set(hass, DIRECT_PLAYER)

    with pytest.raises(HomeAssistantError):
        await async_deliver_message(hass, _options(volume=0.9), "Loud")
    await hass.async_block_till_done()

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()

    assert [call.data["volume_level"] for call in volume_calls] == [0.9, 0.3]


async def test_overlapping_announcements_restore_the_original_volume(
    hass: HomeAssistant,
) -> None:
    """Two overlapping announcements end at the volume before the first one.

    Each call used to read the current volume for itself and schedule its own
    restore without cancelling anyone else's. The second call therefore
    captured the *announcement* volume (0.9) and, firing last, "restored" the
    speaker to it permanently.
    """
    hass.states.async_set(DIRECT_PLAYER, "idle", {"volume_level": 0.3})
    volume_calls = _mock_volume_set(hass, DIRECT_PLAYER)

    speaking = asyncio.Event()
    release = asyncio.Event()

    async def _speak(call: ServiceCall) -> None:
        speaking.set()
        await release.wait()

    hass.services.async_register("tts", "speak", _speak)

    state = VolumeRestoreState()
    first = hass.async_create_task(
        async_deliver_message(hass, _options(volume=0.9), "First", volume_state=state)
    )
    await speaking.wait()

    # The second announcement starts while the first is still speaking.
    speaking.clear()
    second = hass.async_create_task(
        async_deliver_message(hass, _options(volume=0.9), "Second", volume_state=state)
    )
    await speaking.wait()

    release.set()
    await first
    await second
    await hass.async_block_till_done()

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()

    assert hass.states.get(DIRECT_PLAYER).attributes["volume_level"] == 0.3
    # Exactly one restore fired, not one per announcement.
    assert [call.data["volume_level"] for call in volume_calls] == [0.9, 0.9, 0.3]


@pytest.mark.parametrize(
    ("attributes", "state_exists"),
    [
        pytest.param({}, True, id="no-volume_level-attribute"),
        pytest.param({"volume_level": None}, True, id="volume_level-unknown"),
        pytest.param({}, False, id="player-has-no-state"),
    ],
)
async def test_no_restore_is_armed_when_the_original_volume_is_unknown(
    hass: HomeAssistant, attributes: dict[str, object], state_exists: bool
) -> None:
    """Nothing is scheduled when there is no volume to go back to.

    Guessing a "previous" volume would be worse than leaving the player
    where the announcement put it, and an armed timer with nothing to
    restore would still fire.
    """
    if state_exists:
        hass.states.async_set(DIRECT_PLAYER, "idle", attributes)
    async_mock_service(hass, "tts", "speak")
    volume_calls = _mock_volume_set(hass, DIRECT_PLAYER)

    state = VolumeRestoreState()
    await async_deliver_message(hass, _options(volume=0.9), "Loud", volume_state=state)
    await hass.async_block_till_done()

    assert state.cancel_restore is None
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()

    assert [call.data["volume_level"] for call in volume_calls] == [0.9]


async def test_pending_restore_can_be_cancelled(hass: HomeAssistant) -> None:
    """`async_cancel_pending_restore` disarms the timer (used on entry unload)."""
    hass.states.async_set(DIRECT_PLAYER, "idle", {"volume_level": 0.3})
    async_mock_service(hass, "tts", "speak")
    volume_calls = _mock_volume_set(hass, DIRECT_PLAYER)

    state = VolumeRestoreState()
    await async_deliver_message(hass, _options(volume=0.9), "Loud", volume_state=state)
    await hass.async_block_till_done()
    assert state.cancel_restore is not None

    state.async_cancel_pending_restore()
    assert state.cancel_restore is None

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()

    assert [call.data["volume_level"] for call in volume_calls] == [0.9]


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


def _register_ma_player(hass: HomeAssistant, unique_id: str, object_id: str) -> str:
    """Register a media_player owned by the music_assistant platform."""
    entry = er.async_get(hass).async_get_or_create(
        "media_player", "music_assistant", unique_id, suggested_object_id=object_id
    )
    hass.states.async_set(entry.entity_id, "idle", {})
    return entry.entity_id


async def test_music_assistant_falls_back_to_direct_when_action_missing(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A Music Assistant player still speaks when MA itself is not loaded.

    `music_assistant` is an `after_dependencies`, so its action can be absent
    while the player entity is still registered to its platform. This used to
    raise a bare `ServiceNotFound`.
    """
    player = _register_ma_player(hass, "ma-not-loaded", "ma_not_loaded")
    speak_calls = async_mock_service(hass, "tts", "speak")

    await async_deliver_message(hass, _options(media_player=player), "Pizza is here")

    assert len(speak_calls) == 1
    assert "falling back to the Direct strategy" in caplog.text


async def test_no_delivery_path_raises_translated_error(hass: HomeAssistant) -> None:
    """Neither MA nor tts.speak available: a translated error, not ServiceNotFound."""
    player = _register_ma_player(hass, "ma-nothing", "ma_nothing")

    with pytest.raises(HomeAssistantError) as err:
        await async_deliver_message(hass, _options(media_player=player), "Hello")

    assert err.value.translation_key == "no_delivery_path"


async def test_music_assistant_tts_url_is_cached(hass: HomeAssistant) -> None:
    """The MA path caches the synthesized clip, like the Direct path does."""
    player = _register_ma_player(hass, "ma-cache", "ma_cache")
    async_mock_service(hass, "music_assistant", "play_announcement")
    # `async_process_play_media_url` needs a base URL to make the relative
    # `/api/tts_proxy/...` path absolute for an off-box Music Assistant.
    hass.config.internal_url = "http://10.0.0.1:8123"

    with (
        patch(
            "homeassistant.components.tts.generate_media_source_id",
            return_value="media-source://tts/tts.piper?message=hi",
        ) as generate,
        patch(
            "homeassistant.components.media_source.async_resolve_media",
            return_value=PlayMedia("/api/tts_proxy/x.mp3", "audio/mpeg"),
        ),
    ):
        await async_deliver_message(hass, _options(media_player=player), "hi")

    assert generate.call_args.kwargs["cache"] is True


async def test_music_assistant_volume_zero_is_clamped_and_warned(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Music Assistant has no silent announcement: volume 0 becomes 1 %, loudly."""
    player = _register_ma_player(hass, "ma-zero", "ma_zero")
    announce_calls = async_mock_service(hass, "music_assistant", "play_announcement")

    with patch(
        "custom_components.airplay_notifier.delivery._async_resolve_tts_url",
        return_value="https://example.local/tts.mp3",
    ):
        await async_deliver_message(
            hass, _options(media_player=player, volume=0.0), "Quiet please"
        )

    assert announce_calls[0].data["announce_volume"] == 1
    assert "outside the range Music Assistant" in caplog.text
