"""Config flow for AirPlay Notifier.

One config entry manages exactly one `media_player`. Setup asks for the
target player and the TTS engine to speak with (fixed for the entry's
lifetime: pointing at a different player is a new entry, not an edit).
Everything else (language, voice, volume, restore behavior, strategy
override, announce prefix, deny-list) is tuned afterwards through the
options flow.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_LANGUAGE
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ANNOUNCE_PREFIX,
    CONF_DENY_DOMAINS,
    CONF_MEDIA_PLAYER,
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
    STRATEGY_AUTO,
    STRATEGY_DIRECT,
    STRATEGY_MUSIC_ASSISTANT,
)


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
    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the target media player and TTS engine."""
        errors: dict[str, str] = {}

        if user_input is not None:
            media_player = user_input[CONF_MEDIA_PLAYER]
            await self.async_set_unique_id(media_player)
            self._abort_if_unique_id_configured()

            state = self.hass.states.get(media_player)
            title = state.name if state is not None else media_player.split(".", 1)[-1]

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
        if user_input is not None:
            data = dict(user_input)
            data[CONF_DENY_DOMAINS] = _string_to_deny_domains(data[CONF_DENY_DOMAINS])
            return self.async_create_entry(data=data)

        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(current)
        )
