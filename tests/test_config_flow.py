"""Tests for the AirPlay Notifier config and options flows."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.components.media_player.const import MediaPlayerEntityFeature
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_SUPPORTED_FEATURES, CONF_LANGUAGE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.airplay_notifier.const import (
    CONF_DENY_DOMAINS,
    CONF_MEDIA_PLAYER,
    CONF_QUIET_END,
    CONF_QUIET_START,
    CONF_QUIET_VOLUME,
    CONF_RESTORE_VOLUME,
    CONF_SERVICE_NAME,
    CONF_STRATEGY,
    CONF_TTS_ENTITY,
    CONF_VOLUME,
    DOMAIN,
)

MEDIA_PLAYER = "media_player.living_room"
TTS_ENTITY = "tts.piper"


async def _init_user_flow(hass: HomeAssistant) -> dict:
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_user_step_creates_entry(hass: HomeAssistant) -> None:
    """The user step creates an entry titled after the player's friendly name."""
    hass.states.async_set(MEDIA_PLAYER, "idle", {"friendly_name": "Living Room"})

    result = await _init_user_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living Room"
    assert result["data"] == {
        CONF_MEDIA_PLAYER: MEDIA_PLAYER,
        CONF_TTS_ENTITY: TTS_ENTITY,
    }


async def test_user_step_falls_back_to_entity_id_slug(hass: HomeAssistant) -> None:
    """Without a known state, the title falls back to the entity's object_id."""
    result = await _init_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "living_room"


async def test_duplicate_media_player_is_aborted(hass: HomeAssistant) -> None:
    """Only one entry per media player is allowed."""
    first = await _init_user_flow(hass)
    first = await hass.config_entries.flow.async_configure(
        first["flow_id"],
        {CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )
    assert first["type"] is FlowResultType.CREATE_ENTRY

    second = await _init_user_flow(hass)
    second = await hass.config_entries.flow.async_configure(
        second["flow_id"],
        {CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: "tts.other"},
    )

    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "already_configured"


async def test_options_flow_updates_settings(hass: HomeAssistant) -> None:
    """The options flow saves the tunable settings, parsing the deny-list."""
    result = await _init_user_flow(hass)
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    options_result = await hass.config_entries.options.async_init(entry.entry_id)
    assert options_result["type"] is FlowResultType.FORM
    assert options_result["step_id"] == "init"

    updated = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        {
            CONF_LANGUAGE: "fr",
            CONF_VOLUME: 0.4,
            CONF_RESTORE_VOLUME: False,
            CONF_STRATEGY: "direct",
            "announce_prefix": "Attention.",
            CONF_DENY_DOMAINS: "alarm_control_panel, lock, person",
        },
    )
    await hass.async_block_till_done()

    assert updated["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_LANGUAGE] == "fr"
    assert entry.options[CONF_VOLUME] == 0.4
    assert entry.options[CONF_RESTORE_VOLUME] is False
    assert entry.options[CONF_STRATEGY] == "direct"
    assert entry.options[CONF_DENY_DOMAINS] == [
        "alarm_control_panel",
        "lock",
        "person",
    ]


async def test_user_step_refuses_a_player_that_cannot_play_media(
    hass: HomeAssistant,
) -> None:
    """A player without `PLAY_MEDIA` can never speak, so the form refuses it.

    `test-before-configure`: everything this integration does ends in
    `media_player.play_media`, and Home Assistant raises
    `ServiceNotSupported` when the target does not advertise the feature.
    Catching that in the form beats catching it in the log of the first
    announcement that mattered.
    """
    hass.states.async_set(
        MEDIA_PLAYER,
        "idle",
        {
            "friendly_name": "Living Room",
            ATTR_SUPPORTED_FEATURES: MediaPlayerEntityFeature.VOLUME_SET,
        },
    )

    result = await _init_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_MEDIA_PLAYER: "unsupported_player"}

    # And the refusal is recoverable: the same flow accepts a usable player.
    hass.states.async_set(
        "media_player.kitchen",
        "idle",
        {ATTR_SUPPORTED_FEATURES: MediaPlayerEntityFeature.PLAY_MEDIA},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_MEDIA_PLAYER: "media_player.kitchen", CONF_TTS_ENTITY: TTS_ENTITY},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_step_accepts_a_player_with_no_supported_features(
    hass: HomeAssistant,
) -> None:
    """An absent `supported_features` is not evidence of anything.

    A template or YAML `media_player`, or one that has never been seen,
    may not publish the attribute at all. The check is permissive when the
    information is missing rather than refusing on the strength of an
    absent attribute.
    """
    hass.states.async_set(MEDIA_PLAYER, "idle", {"friendly_name": "Living Room"})

    result = await _init_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def _entry_with_options_flow(hass: HomeAssistant) -> tuple:
    """Create an entry through the user step and open its options flow."""
    result = await _init_user_flow(hass)
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    return entry, await hass.config_entries.options.async_init(entry.entry_id)


async def test_options_flow_refuses_the_ma_strategy_on_a_non_ma_player(
    hass: HomeAssistant,
) -> None:
    """Forcing the Music Assistant strategy on a player it cannot reach is refused.

    `music_assistant.play_announcement` is a Music Assistant platform entity
    action: it only accepts entities Music Assistant itself provides. Forcing
    the strategy on an `apple_tv` player produces a failure on every single
    announcement, so the options form refuses the combination instead.
    """
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "media_player", "apple_tv", "living-room-uid", suggested_object_id="living_room"
    )

    entry, options_result = await _entry_with_options_flow(hass)

    result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        {
            CONF_VOLUME: 0.4,
            CONF_RESTORE_VOLUME: True,
            CONF_STRATEGY: "music_assistant",
            "announce_prefix": "",
            CONF_DENY_DOMAINS: "lock",
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_STRATEGY: "not_a_music_assistant_player"}
    assert entry.options == {}


async def test_options_flow_allows_the_ma_strategy_on_an_ma_player(
    hass: HomeAssistant,
) -> None:
    """The same choice is accepted when the target really is an MA player."""
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "media_player",
        "music_assistant",
        "living-room-uid",
        suggested_object_id="living_room",
    )

    entry, options_result = await _entry_with_options_flow(hass)

    result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        {
            CONF_VOLUME: 0.4,
            CONF_RESTORE_VOLUME: True,
            CONF_STRATEGY: "music_assistant",
            "announce_prefix": "",
            CONF_DENY_DOMAINS: "lock",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_STRATEGY] == "music_assistant"


async def test_options_flow_allows_the_ma_strategy_for_an_unregistered_player(
    hass: HomeAssistant,
) -> None:
    """A player absent from the entity registry is not evidence either.

    Same permissiveness as the `PLAY_MEDIA` check: the strategy is only
    refused when the registry positively says the player belongs to some
    other integration.
    """
    entry, options_result = await _entry_with_options_flow(hass)

    result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        {
            CONF_VOLUME: 0.4,
            CONF_RESTORE_VOLUME: True,
            CONF_STRATEGY: "music_assistant",
            "announce_prefix": "",
            CONF_DENY_DOMAINS: "lock",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_STRATEGY] == "music_assistant"


OTHER_PLAYER = "media_player.kitchen"
OTHER_TTS = "tts.cloud_say"


def _loaded_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Add (but do not set up) an entry pointing at the standard targets."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living Room",
        unique_id=MEDIA_PLAYER,
        data={CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
        options={CONF_VOLUME: 0.4},
    )
    entry.add_to_hass(hass)
    return entry


async def test_reconfigure_moves_the_entry_to_another_player(
    hass: HomeAssistant, targets: None
) -> None:
    """Both setup-time fields can be changed without losing the options.

    `reconfiguration-flow`: before this, pointing an entry at a different
    speaker meant deleting it and re-adding it, which threw away every
    tuned option and, worse, silently renamed the legacy notify service
    that `alert.notifiers:` refers to.
    """
    hass.states.async_set(OTHER_PLAYER, "idle", {"friendly_name": "Kitchen"})
    hass.states.async_set(OTHER_TTS, "unknown", {})
    entry = _loaded_entry(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_MEDIA_PLAYER: OTHER_PLAYER, CONF_TTS_ENTITY: OTHER_TTS},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_MEDIA_PLAYER] == OTHER_PLAYER
    assert entry.data[CONF_TTS_ENTITY] == OTHER_TTS
    # The unique id follows the player: still one entry per player.
    assert entry.unique_id == OTHER_PLAYER
    # The options survive, and so does the title — renaming the entry here
    # would rename `notify.airplay_living_room` under every `alert` that
    # refers to it.
    assert entry.options[CONF_VOLUME] == 0.4
    assert entry.title == "Living Room"


async def test_reconfigure_can_change_only_the_tts_engine(
    hass: HomeAssistant, targets: None
) -> None:
    """Keeping the same player is not a duplicate of itself.

    The unique id check has to skip the entry being reconfigured, or
    swapping the TTS engine alone would abort as `already_configured`.
    """
    hass.states.async_set(OTHER_TTS, "unknown", {})
    entry = _loaded_entry(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_MEDIA_PLAYER: MEDIA_PLAYER, CONF_TTS_ENTITY: OTHER_TTS},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_TTS_ENTITY] == OTHER_TTS
    assert entry.unique_id == MEDIA_PLAYER


async def test_reconfigure_refuses_a_player_owned_by_another_entry(
    hass: HomeAssistant, targets: None
) -> None:
    """One entry per player still holds when the player is moved."""
    hass.states.async_set(OTHER_PLAYER, "idle", {})
    entry = _loaded_entry(hass)
    other = MockConfigEntry(
        domain=DOMAIN,
        title="Kitchen",
        unique_id=OTHER_PLAYER,
        data={CONF_MEDIA_PLAYER: OTHER_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )
    other.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_MEDIA_PLAYER: OTHER_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_MEDIA_PLAYER: "already_configured"}
    assert entry.data[CONF_MEDIA_PLAYER] == MEDIA_PLAYER


async def test_reconfigure_refuses_a_player_that_cannot_play_media(
    hass: HomeAssistant, targets: None
) -> None:
    """The suitability check of the user step applies to reconfigure too."""
    hass.states.async_set(
        OTHER_PLAYER,
        "idle",
        {ATTR_SUPPORTED_FEATURES: MediaPlayerEntityFeature.VOLUME_SET},
    )
    entry = _loaded_entry(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_MEDIA_PLAYER: OTHER_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_MEDIA_PLAYER: "unsupported_player"}
    assert entry.data[CONF_MEDIA_PLAYER] == MEDIA_PLAYER


async def test_reconfigure_reloads_a_loaded_entry_keeping_its_service_name(
    hass: HomeAssistant, targets: None
) -> None:
    """A live entry picks the new targets up, under the same notify service.

    The reload is what makes the change take effect: the runtime options
    (and the notify entity's availability tracking) are rebuilt from the
    entry on every setup. The legacy service keeps its name because the
    title does — an `alert` pointing at `notify.airplay_living_room` goes
    on working, now speaking on the new player.
    """
    hass.states.async_set(OTHER_PLAYER, "idle", {"friendly_name": "Kitchen"})
    entry = _loaded_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.services.has_service("notify", "airplay_living_room")

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_MEDIA_PLAYER: OTHER_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.options.media_player == OTHER_PLAYER
    assert hass.services.has_service("notify", "airplay_living_room")
    # The persisted name is what survives, not just the live registration:
    # `data_updates` merges into `entry.data`, so a reconfigure that dropped
    # `service_name` would look identical here until the next restart.
    assert entry.data[CONF_SERVICE_NAME] == "airplay_living_room"


BASE_OPTIONS = {
    CONF_VOLUME: 0.4,
    CONF_RESTORE_VOLUME: True,
    CONF_STRATEGY: "auto",
    "announce_prefix": "",
    CONF_DENY_DOMAINS: "lock",
}


async def test_options_flow_saves_a_quiet_window(hass: HomeAssistant) -> None:
    """Both bounds and the quiet volume round-trip into the entry options."""
    entry, options_result = await _entry_with_options_flow(hass)

    result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        {
            **BASE_OPTIONS,
            CONF_QUIET_START: "22:00:00",
            CONF_QUIET_END: "07:00:00",
            CONF_QUIET_VOLUME: 0.15,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_QUIET_START] == "22:00:00"
    assert entry.options[CONF_QUIET_END] == "07:00:00"
    assert entry.options[CONF_QUIET_VOLUME] == 0.15


async def test_options_flow_refuses_half_a_quiet_window(hass: HomeAssistant) -> None:
    """One bound alone is a mistake, not a half-open window.

    Leaving both empty is how quiet hours are turned off; filling in only
    one is ambiguous, and silently ignoring it would leave the user
    believing their nights were protected.
    """
    entry, options_result = await _entry_with_options_flow(hass)

    result = await hass.config_entries.options.async_configure(
        options_result["flow_id"], {**BASE_OPTIONS, CONF_QUIET_START: "22:00:00"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_QUIET_START: "quiet_hours_incomplete"}
    assert entry.options == {}


async def test_options_flow_clears_a_quiet_window(hass: HomeAssistant) -> None:
    """Emptying both time fields switches quiet hours back off."""
    entry, options_result = await _entry_with_options_flow(hass)
    await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        {**BASE_OPTIONS, CONF_QUIET_START: "22:00:00", CONF_QUIET_END: "07:00:00"},
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_QUIET_START] == "22:00:00"

    second = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(second["flow_id"], BASE_OPTIONS)
    await hass.async_block_till_done()

    assert CONF_QUIET_START not in entry.options
    assert CONF_QUIET_END not in entry.options


async def test_reconfigure_reloads_the_entry_exactly_once(
    hass: HomeAssistant, targets: None
) -> None:
    """One reload, and no transitional notice about the update listener.

    `async_update_reload_and_abort` updates the entry — which fires the
    update listener, and the listener reloads — and then schedules a reload
    of its own. Two reloads for one change, and core reports the
    combination through `report_usage` ("has an update listener and should
    use it for scheduling a reload",
    `homeassistant/config_entries.py`), breaking in 2026.12.0.
    """
    hass.states.async_set(OTHER_PLAYER, "idle", {"friendly_name": "Kitchen"})
    entry = _loaded_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await entry.start_reconfigure_flow(hass)
    with patch.object(
        hass.config_entries,
        "async_reload",
        wraps=hass.config_entries.async_reload,
    ) as reload:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_MEDIA_PLAYER: OTHER_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert reload.call_count == 1
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.options.media_player == OTHER_PLAYER


async def test_reconfigure_refuses_a_non_ma_player_under_the_ma_strategy(
    hass: HomeAssistant, targets: None
) -> None:
    """The stored strategy is re-checked against the *new* player.

    The options flow refuses `music_assistant` on a player Music Assistant
    does not provide, but the reconfigure step moved the entry onto exactly
    such a player without a word — leaving a combination the options form
    would never have accepted, and a failure on every announcement.
    """
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "media_player", "apple_tv", "kitchen-uid", suggested_object_id="kitchen"
    )
    hass.states.async_set(OTHER_PLAYER, "idle", {})

    entry = _loaded_entry(hass)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_STRATEGY: "music_assistant"}
    )

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_MEDIA_PLAYER: OTHER_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_MEDIA_PLAYER: "not_a_music_assistant_player"}
    assert entry.data[CONF_MEDIA_PLAYER] == MEDIA_PLAYER


async def test_reconfigure_allows_an_ma_player_under_the_ma_strategy(
    hass: HomeAssistant, targets: None
) -> None:
    """The same move is accepted when the new player really is an MA player."""
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "media_player", "music_assistant", "kitchen-uid", suggested_object_id="kitchen"
    )
    hass.states.async_set(OTHER_PLAYER, "idle", {})

    entry = _loaded_entry(hass)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_STRATEGY: "music_assistant"}
    )

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_MEDIA_PLAYER: OTHER_PLAYER, CONF_TTS_ENTITY: TTS_ENTITY},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert entry.data[CONF_MEDIA_PLAYER] == OTHER_PLAYER


async def test_options_flow_refuses_a_silent_quiet_volume(
    hass: HomeAssistant,
) -> None:
    """A quiet volume of 0 is never what anyone wants.

    Speaking at volume 0 is a lie: the automation is told the announcement
    happened and nobody hears it. Leaving the field empty is how you ask
    for a refusal instead — and on a Music Assistant player, 0 is not even
    reachable (it is clamped to 1 %, see docs/known-issues.md).
    """
    entry, options_result = await _entry_with_options_flow(hass)

    result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        {
            **BASE_OPTIONS,
            CONF_QUIET_START: "22:00:00",
            CONF_QUIET_END: "07:00:00",
            CONF_QUIET_VOLUME: 0,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_QUIET_VOLUME: "quiet_volume_silent"}
    assert entry.options == {}
