"""End-to-end test of the Music Assistant strategy's TTS URL resolution.

`delivery._async_resolve_tts_url` is the one place this integration does
work Home Assistant would normally do for it (see docs/ARCHITECTURE.md:
`music_assistant.play_announcement` takes a plain URL, not a
`media-source://` identifier). Patching it out — as the rest of the suite
does, to keep the strategy tests focused — means nothing would notice if
core changed `generate_media_source_id`, `media_source.async_resolve_media`
or `async_process_play_media_url` under it.

So this module stands up a *real* `tts` entity (the same
`mock_integration` + `mock_platform` shape core's own
`tests/components/tts/common.py` uses) and lets the whole chain run.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.tts import TextToSpeechEntity, TtsAudioType
from homeassistant.components.tts.const import DATA_TTS_MANAGER
from homeassistant.config_entries import ConfigEntry, ConfigFlow
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    MockPlatform,
    async_mock_service,
    mock_integration,
    mock_platform,
)

from custom_components.airplay_notifier.const import STRATEGY_AUTO
from custom_components.airplay_notifier.delivery import (
    AirplayNotifierOptions,
    async_deliver_message,
)

TTS_TEST_DOMAIN = "airplay_notifier_test_tts"


class _MockTTSConfigFlow(ConfigFlow, domain=TTS_TEST_DOMAIN):
    """Registers a flow handler so the mock entry can be set up."""

    VERSION = 1


class _MockTTSEntity(TextToSpeechEntity):
    """A minimal but real TTS entity, enough to be resolved through."""

    _attr_name = "Mock TTS"
    _attr_default_language = "en_US"
    _attr_supported_languages = ["en_US", "fr_FR"]
    _attr_supported_options = ["voice"]

    def get_tts_audio(
        self, message: str, language: str, options: dict[str, Any]
    ) -> TtsAudioType:
        """Return a token audio payload; nothing plays it in a test."""
        return ("mp3", b"audio")


@pytest.fixture(autouse=True)
def _tts_cache_dir(tmp_path: Any) -> Generator[None]:
    """Keep the TTS file cache inside the test's tmp_path."""
    with (
        patch(
            "homeassistant.components.tts._init_tts_cache_dir",
            return_value=str(tmp_path),
        ),
        patch("homeassistant.components.tts._get_cache_files", return_value={}),
    ):
        yield


async def _setup_tts_entity(hass: HomeAssistant) -> str:
    """Set up a real `tts.mock_tts` entity through a mock config entry."""

    async def _async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
        await hass.config_entries.async_forward_entry_setups(entry, [Platform.TTS])
        return True

    async def _async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
        return await hass.config_entries.async_unload_platforms(entry, [Platform.TTS])

    mock_integration(
        hass,
        MockModule(
            TTS_TEST_DOMAIN,
            async_setup_entry=_async_setup_entry,
            async_unload_entry=_async_unload_entry,
        ),
    )

    async def _async_setup_platform(
        hass: HomeAssistant,
        entry: ConfigEntry,
        async_add_entities: AddConfigEntryEntitiesCallback,
    ) -> None:
        async_add_entities([_MockTTSEntity()])

    mock_platform(
        hass,
        f"{TTS_TEST_DOMAIN}.tts",
        MockPlatform(async_setup_entry=_async_setup_platform),
    )
    # The mock integration declares `config_flow`, so core tries to import
    # one when setting the entry up; a stub is enough here.
    mock_platform(hass, f"{TTS_TEST_DOMAIN}.config_flow")

    entry = MockConfigEntry(domain=TTS_TEST_DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # `media_source.async_resolve_media` refuses to run until the component
    # is loaded; in production it is a manifest dependency of this
    # integration, here nothing has pulled it in yet.
    assert await async_setup_component(hass, "media_source", {})

    tts_entities = hass.states.async_entity_ids("tts")
    assert len(tts_entities) == 1
    return tts_entities[0]


async def test_music_assistant_resolves_a_real_playable_url(
    hass: HomeAssistant,
) -> None:
    """The MA strategy hands `play_announcement` an absolute, signed HTTP URL.

    This exercises the real `tts.generate_media_source_id` →
    `media_source.async_resolve_media` → `async_process_play_media_url`
    chain rather than patching `_async_resolve_tts_url` away, so a change to
    any of those three core APIs breaks this test instead of production.
    """
    tts_entity_id = await _setup_tts_entity(hass)

    hass.config.internal_url = "http://10.0.0.1:8123"
    player = er.async_get(hass).async_get_or_create(
        "media_player",
        "music_assistant",
        "real-resolution",
        suggested_object_id="ma_real",
    )
    hass.states.async_set(player.entity_id, "idle", {})
    announce_calls = async_mock_service(hass, "music_assistant", "play_announcement")

    await async_deliver_message(
        hass,
        AirplayNotifierOptions(
            media_player=player.entity_id,
            tts_entity=tts_entity_id,
            language="fr_FR",
            voice="mock-voice",
            strategy=STRATEGY_AUTO,
        ),
        "Le lave-vaisselle est terminé",
    )

    assert len(announce_calls) == 1
    url = announce_calls[0].data["url"]
    # Absolute (Music Assistant may run off-box) and served by this instance.
    assert url.startswith("http://10.0.0.1:8123/api/tts_proxy/")
    assert "media-source://" not in url


async def test_resolved_stream_uses_the_tts_file_cache(hass: HomeAssistant) -> None:
    """The stream the MA path resolves is a file-cached one.

    Each resolution gets its own random `/api/tts_proxy/<token>` URL, so URL
    identity says nothing about caching; what matters is the `use_file_cache`
    flag on the `ResultStream` core builds, which is what `cache=True` in
    `generate_media_source_id` sets. Without that argument this is `None`
    (engine default) instead of `True`, and the two strategies disagree about
    caching the same text.
    """
    tts_entity_id = await _setup_tts_entity(hass)

    hass.config.internal_url = "http://10.0.0.1:8123"
    player = er.async_get(hass).async_get_or_create(
        "media_player",
        "music_assistant",
        "cache-agreement",
        suggested_object_id="ma_cache_agreement",
    )
    hass.states.async_set(player.entity_id, "idle", {})
    announce_calls = async_mock_service(hass, "music_assistant", "play_announcement")

    await async_deliver_message(
        hass,
        AirplayNotifierOptions(
            media_player=player.entity_id,
            tts_entity=tts_entity_id,
            strategy=STRATEGY_AUTO,
        ),
        "Same text",
    )

    token = announce_calls[0].data["url"].rsplit("/", 1)[-1]
    stream = hass.data[DATA_TTS_MANAGER].token_to_stream[token]
    assert stream.use_file_cache is True
