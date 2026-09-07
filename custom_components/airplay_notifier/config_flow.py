"""Config flow for AirPlay Notifier.

One config entry manages exactly one `media_player`. Setup asks for the
target player and the TTS engine to speak with; both can be changed later
through the reconfigure flow, which is also the only way to move an entry
to a different player without losing its options. Everything else
(language, voice, volume, restore behavior, strategy override, announce
prefix, deny-list, quiet hours) is tuned through the options flow.

Two suitability checks run before an entry is created or changed
(`test-before-configure`), both deliberately permissive when the
information they need is absent:

- the target must advertise `MediaPlayerEntityFeature.PLAY_MEDIA`
  (`_supports_play_media`);
- the Music Assistant strategy is only offered for a player Music
  Assistant actually provides (`_is_music_assistant_player`).
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.media_player.const import MediaPlayerEntityFeature
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import ATTR_SUPPORTED_FEATURES, CONF_LANGUAGE
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er, selector

from .const import (
    CONF_ANNOUNCE_PREFIX,
    CONF_DENY_DOMAINS,
    CONF_MEDIA_PLAYER,
    CONF_QUIET_END,
    CONF_QUIET_START,
    CONF_QUIET_VOLUME,
    CONF_RESTORE_VOLUME,
    CONF_STRATEGY,
    CONF_TTS_ENTITY,
    CONF_VOICE,
    CONF_VOLUME,
    DEFAULT_ANNOUNCE_PREFIX,
    DEFAULT_DENY_DOMAINS,
    DEFAULT_RESTORE_VOLUME,
    DEFAULT_STRATEGY,
    DEFAULT_VOLUME,
    DOMAIN,
    MUSIC_ASSISTANT_DOMAIN,
    STRATEGY_AUTO,
    STRATEGY_DIRECT,
    STRATEGY_MUSIC_ASSISTANT,
)


def _supports_play_media(state: State) -> bool:
    """Return whether a media_player state advertises `PLAY_MEDIA`.

    Everything this integration does ends in `media_player.play_media`:
    `tts.speak` calls it (`homeassistant/components/tts/entity.py`,
    `TextToSpeechEntity.async_speak`) and Music Assistant's announcement
    path plays a URL on the player. Home Assistant refuses the call with
    `ServiceNotSupported` when the target does not advertise
    `MediaPlayerEntityFeature.PLAY_MEDIA`
    (`homeassistant/components/media_player/const.py`), so such a player
    can never speak — worth catching in the form rather than in the log of
    the first announcement that mattered.

    Permissive when the information is missing: a player with no
    `supported_features` attribute at all (a template entity, one that has
    never been seen) is accepted rather than refused on the strength of an
    absent attribute.
    """
    features = state.attributes.get(ATTR_SUPPORTED_FEATURES)
    if not isinstance(features, int):
        return True
    return bool(features & MediaPlayerEntityFeature.PLAY_MEDIA)


def _media_player_error(hass: HomeAssistant, media_player: str) -> str | None:
    """Return the form error for `media_player`, or `None` if it is usable."""
    state = hass.states.get(media_player)
    if state is not None and not _supports_play_media(state):
        return "unsupported_player"
    return None


def _is_music_assistant_player(hass: HomeAssistant, media_player: str) -> bool:
    """Return whether `media_player` is provided by Music Assistant.

    Reads `RegistryEntry.platform` (`homeassistant/helpers/entity_registry.py`),
    the integration domain that set the entity up — the same signal the
    `auto` strategy uses at delivery time. Permissive when the player is
    not in the registry at all (a YAML or template `media_player` never
    is): only a registry entry positively attributing the player to
    another integration counts as evidence.
    """
    entry = er.async_get(hass).async_get(media_player)
    return entry is None or entry.platform == MUSIC_ASSISTANT_DOMAIN


def _deny_domains_to_string(domains: list[str]) -> str:
    """Render the deny-list as a comma-separated string for the form."""
    return ", ".join(domains)


def _string_to_deny_domains(value: str) -> list[str]:
    """Parse the comma-separated deny-list string from the form."""
    return [part.strip() for part in value.split(",") if part.strip()]


def _options_schema(current: dict[str, Any]) -> vol.Schema:
    """Build the options-flow schema, pre-filled with `current` values."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_LANGUAGE,
                description={"suggested_value": current.get(CONF_LANGUAGE)},
            ): selector.TextSelector(),
            vol.Optional(
                CONF_VOICE, description={"suggested_value": current.get(CONF_VOICE)}
            ): selector.TextSelector(),
            vol.Optional(
                CONF_VOLUME, default=current.get(CONF_VOLUME, DEFAULT_VOLUME)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0, max=1.0, step=0.05, mode=selector.NumberSelectorMode.SLIDER
                )
            ),
            vol.Optional(
                CONF_RESTORE_VOLUME,
                default=current.get(CONF_RESTORE_VOLUME, DEFAULT_RESTORE_VOLUME),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_STRATEGY, default=current.get(CONF_STRATEGY, DEFAULT_STRATEGY)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[STRATEGY_AUTO, STRATEGY_MUSIC_ASSISTANT, STRATEGY_DIRECT],
                    translation_key="strategy",
                )
            ),
            vol.Optional(
                CONF_ANNOUNCE_PREFIX,
                default=current.get(CONF_ANNOUNCE_PREFIX, DEFAULT_ANNOUNCE_PREFIX),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_DENY_DOMAINS,
                default=_deny_domains_to_string(
                    current.get(CONF_DENY_DOMAINS, DEFAULT_DENY_DOMAINS)
                ),
            ): selector.TextSelector(),
            # Quiet hours: all three fields use `suggested_value` rather
            # than `default`, so clearing one really does remove the key
            # from the submitted input — which is how quiet hours are
            # turned off again. `TimeSelector` validates through `cv.time`
            # (`homeassistant/helpers/selector.py`) and cannot carry an
            # "empty" value, and a slider cannot be emptied either, hence
            # the box for the quiet volume.
            vol.Optional(
                CONF_QUIET_START,
                description={"suggested_value": current.get(CONF_QUIET_START)},
            ): selector.TimeSelector(),
            vol.Optional(
                CONF_QUIET_END,
                description={"suggested_value": current.get(CONF_QUIET_END)},
            ): selector.TimeSelector(),
            vol.Optional(
                CONF_QUIET_VOLUME,
                description={"suggested_value": current.get(CONF_QUIET_VOLUME)},
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0, max=1.0, step=0.05, mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
    )


class AirplayNotifierConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AirPlay Notifier."""

    # Bump MINOR_VERSION for backwards-compatible entry changes (a new
    # option with a default), VERSION for breaking ones. Core refuses to
    # load an entry whose stored version is *newer* than the handler's
    # (`homeassistant/config_entries.py`, `async_migrate_entry` /
    # `_async_migrate_and_setup`), so both must exist from the start for a
    # downgrade to fail cleanly instead of silently.
    # 1.2 (0.2.0): the quiet-hours option keys. Additive, and an absent
    # key means "off" — but a 0.1.x build would ignore them silently and
    # start speaking at 03:00, which is what the version stamp exists to
    # refuse.
    VERSION = 1
    MINOR_VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the target media player and TTS engine."""
        errors: dict[str, str] = {}

        if user_input is not None:
            media_player = user_input[CONF_MEDIA_PLAYER]
            await self.async_set_unique_id(media_player)
            self._abort_if_unique_id_configured()

            if (error := _media_player_error(self.hass, media_player)) is not None:
                errors[CONF_MEDIA_PLAYER] = error
            else:
                state = self.hass.states.get(media_player)
                title = (
                    state.name if state is not None else media_player.split(".", 1)[-1]
                )
                return self.async_create_entry(title=title, data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_MEDIA_PLAYER): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="media_player")
                ),
                vol.Required(CONF_TTS_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="tts")
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Point an existing entry at a different player or TTS engine.

        The two setup-time fields are the only ones here; everything else
        stays in the options flow, and both are kept by
        `async_update_reload_and_abort`'s `data_updates`
        (`homeassistant/config_entries.py`), which merges into the entry's
        data and reloads it rather than replacing the entry.

        Two things deliberately do *not* change:

        - the entry title, and therefore the legacy `notify.airplay_<name>`
          service name derived from it. Renaming that service silently
          would break every `alert.notifiers:` that refers to it, so a
          rename stays an explicit action by the user;
        - the unique-id rule. The unique id follows the new player (one
          entry per player, still), which is why the duplicate check here
          has to ignore the entry being reconfigured — otherwise changing
          only the TTS engine would abort as `already_configured`.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        current: dict[str, Any] = {**entry.data}

        if user_input is not None:
            current = {**current, **user_input}
            media_player = user_input[CONF_MEDIA_PLAYER]
            owner = self.hass.config_entries.async_entry_for_domain_unique_id(
                DOMAIN, media_player
            )

            if owner is not None and owner.entry_id != entry.entry_id:
                errors[CONF_MEDIA_PLAYER] = "already_configured"
            elif (error := _media_player_error(self.hass, media_player)) is not None:
                errors[CONF_MEDIA_PLAYER] = error
            else:
                # `async_update_reload_and_abort` logs a transitional notice
                # while the entry also has an update listener
                # (`homeassistant/config_entries.py`: "has an update listener
                # and should use it for scheduling a reload", breaking in
                # 2026.12.0). Both paths end in the same reload, and the
                # listener — which is what makes an options change take
                # effect — will be the only one left afterwards, so this
                # keeps working either way.
                return self.async_update_reload_and_abort(
                    entry, unique_id=media_player, data_updates=user_input
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MEDIA_PLAYER, default=current[CONF_MEDIA_PLAYER]
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="media_player")
                    ),
                    vol.Required(
                        CONF_TTS_ENTITY, default=current[CONF_TTS_ENTITY]
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="tts")
                    ),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> AirplayNotifierOptionsFlow:
        """Return the options flow for this handler."""
        return AirplayNotifierOptionsFlow()


class AirplayNotifierOptionsFlow(OptionsFlow):
    """Handle options for AirPlay Notifier.

    `media_player` and `tts_entity` are not editable here: they are the
    entry's identity (its unique_id and the legacy service it discovers).
    Pointing at a different player means adding a new entry.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the tunable options."""
        current = {**self.config_entry.data, **self.config_entry.options}
        errors: dict[str, str] = {}

        if user_input is not None:
            data = dict(user_input)
            data[CONF_DENY_DOMAINS] = _string_to_deny_domains(data[CONF_DENY_DOMAINS])

            if (CONF_QUIET_START in data) != (CONF_QUIET_END in data):
                # Half a window is ambiguous, and ignoring it silently
                # would leave the user believing their nights are
                # protected. Both bounds, or neither (which is off).
                errors[CONF_QUIET_START] = "quiet_hours_incomplete"
            elif data.get(
                CONF_STRATEGY
            ) == STRATEGY_MUSIC_ASSISTANT and not _is_music_assistant_player(
                self.hass, current[CONF_MEDIA_PLAYER]
            ):
                errors[CONF_STRATEGY] = "not_a_music_assistant_player"
            else:
                return self.async_create_entry(data=data)

            current = {**current, **data}

        return self.async_show_form(
            step_id="init", data_schema=_options_schema(current), errors=errors
        )
