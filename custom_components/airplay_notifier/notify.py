"""Notify platform for AirPlay Notifier.

Exposes the two surfaces described in `__init__.py`:

- `async_get_service` registers the legacy `notify.airplay_<name>` service
  (discovered by `__init__.py`'s `async_setup_entry`, with the service name
  already slugified).
- `async_setup_entry` registers a `NotifyEntity` for the same config entry.

Both forward the message to `delivery.async_deliver_message`, which
resolves the delivery strategy and speaks it — but only the legacy service
can carry per-call `data` overrides and the `source_entity` the deny-list
checks. Home Assistant's own `notify.send_message` entity service schema
(`homeassistant/components/notify/__init__.py`,
`component.async_register_entity_service(SERVICE_SEND_MESSAGE, {...})`,
around line 84-91) accepts only `message` and `title` — no generic `data`
field — so the `NotifyEntity` path always speaks with the entry's
configured defaults and can never carry a `source_entity` to check against
`deny_domains`.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.notify import NotifyEntity, NotifyEntityFeature
from homeassistant.components.notify.const import ATTR_DATA
from homeassistant.components.notify.legacy import BaseNotificationService
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import AirplayNotifierConfigEntry, AirplayNotifierRuntimeData
from .delivery import AirplayNotifierOptions, AnnouncementDenied, async_deliver_message

_LOGGER = logging.getLogger(__name__)


async def async_get_service(
    hass: HomeAssistant,
    config: ConfigType,
    discovery_info: DiscoveryInfoType | None = None,
) -> BaseNotificationService | None:
    """Set up the legacy `notify.airplay_<name>` service.

    `discovery_info` is populated by `__init__.py`'s `async_setup_entry`
    with the `entry_id` of the config entry it was discovered for.
    """
    if discovery_info is None or "entry_id" not in discovery_info:
        _LOGGER.error("AirPlay Notifier can only be set up through the UI")
        return None

    entry = hass.config_entries.async_get_entry(discovery_info["entry_id"])
    if entry is None or not hasattr(entry, "runtime_data"):
        _LOGGER.error("AirPlay Notifier config entry is not loaded")
        return None

    runtime_data: AirplayNotifierRuntimeData = entry.runtime_data
    return AirplayNotifierNotificationService(hass, runtime_data.options)


class AirplayNotifierNotificationService(BaseNotificationService):
    """Legacy notify service that speaks the message via `delivery`."""

    def __init__(self, hass: HomeAssistant, options: AirplayNotifierOptions) -> None:
        """Initialize the service."""
        self.hass = hass
        self._options = options

    async def async_send_message(self, message: str, **kwargs: Any) -> None:
        """Speak `message`, applying any per-call `data` overrides.

        `kwargs[ATTR_TITLE]`, if provided, is intentionally ignored: there is
        nothing meaningful to do with a title when the output is speech.
        """
        data = dict(kwargs.get(ATTR_DATA) or {})
        try:
            await async_deliver_message(self.hass, self._options, message, data)
        except AnnouncementDenied:
            _LOGGER.warning(
                "Refused to speak notification on %s: source entity domain is "
                "in deny_domains (security is never spoken)",
                self._options.media_player,
            )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AirplayNotifierConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the AirPlay Notifier entity for a config entry."""
    async_add_entities([AirplayNotifierEntity(entry)])


class AirplayNotifierEntity(NotifyEntity):
    """Modern notify entity that speaks the message via `delivery`."""

    _attr_has_entity_name = True
    _attr_name = "Speak"
    _attr_supported_features = NotifyEntityFeature.TITLE

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the entity."""
        self._attr_unique_id = f"{entry.entry_id}_notify_entity"
        self._options: AirplayNotifierOptions = entry.runtime_data.options

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Speak `message`.

        Overrides the base class instead of `send_message` so no executor
        job is scheduled: everything downstream (service calls, TTS
        resolution) is native async I/O. `title` is accepted for
        `NotifyEntityFeature.TITLE` compatibility but is not spoken.

        There is no `data` to forward here (see the module docstring), so
        this always speaks with the entry's configured defaults and can
        never raise `AnnouncementDenied`: the deny-list only ever inspects
        a `source_entity` that this surface has no way to carry.
        """
        await async_deliver_message(self.hass, self._options, message)
