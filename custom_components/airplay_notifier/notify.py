"""Notify platform for AirPlay Notifier.

Exposes the two surfaces described in `__init__.py`:

- `async_get_service` registers the legacy `notify.airplay_<name>` service
  (discovered by `__init__.py`'s `async_setup_entry`, with the service name
  already slugified).
- `async_setup_entry` registers a `NotifyEntity` for the same config entry.

Neither surface may capture `AirplayNotifierOptions` by value. Core's
`BaseNotificationService.async_register_services`
(`homeassistant/components/notify/legacy.py`) returns early when a service
with that name already exists, so after a reload the *original* service
instance keeps answering `notify.airplay_<name>` even though a fresh
instance was created by the re-dispatched discovery. Both classes therefore
look the options up from the live config entry
(`hass.config_entries.async_get_entry(...).runtime_data`) on every call.

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
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import AirplayNotifierConfigEntry, AirplayNotifierRuntimeData
from .const import DOMAIN
from .delivery import AnnouncementDenied, async_deliver_message

_LOGGER = logging.getLogger(__name__)


def _async_live_runtime_data(
    hass: HomeAssistant, entry_id: str
) -> AirplayNotifierRuntimeData:
    """Return the *current* runtime data of `entry_id`.

    Never cache what this returns: a reload replaces
    `entry.runtime_data` wholesale, and core keeps the first legacy service
    instance alive across reloads (see the module docstring).
    `entry.runtime_data` is deleted by core when an entry is unloaded
    (`homeassistant/config_entries.py`, `object.__delattr__(self,
    "runtime_data")`), so `getattr` is the correct guard.
    """
    entry = hass.config_entries.async_get_entry(entry_id)
    runtime_data = getattr(entry, "runtime_data", None) if entry is not None else None
    if runtime_data is None:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="entry_not_loaded"
        )
    return runtime_data  # type: ignore[no-any-return]


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

    entry_id: str = discovery_info["entry_id"]
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or getattr(entry, "runtime_data", None) is None:
        _LOGGER.error("AirPlay Notifier config entry is not loaded")
        return None

    return AirplayNotifierNotificationService(hass, entry_id)


class AirplayNotifierNotificationService(BaseNotificationService):
    """Legacy notify service that speaks the message via `delivery`."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the service.

        Only the `entry_id` is kept: the options are re-read from the live
        config entry on every call so an options change (which reloads the
        entry) takes effect even though core keeps this instance registered.
        """
        self.hass = hass
        self.entry_id = entry_id

    async def async_send_message(self, message: str, **kwargs: Any) -> None:
        """Speak `message`, applying any per-call `data` overrides.

        `kwargs[ATTR_TITLE]`, if provided, is intentionally ignored: there is
        nothing meaningful to do with a title when the output is speech.
        """
        runtime_data = _async_live_runtime_data(self.hass, self.entry_id)
        data = dict(kwargs.get(ATTR_DATA) or {})
        try:
            await async_deliver_message(self.hass, runtime_data.options, message, data)
        except AnnouncementDenied:
            # Logged here *and* re-raised: a refusal must be visible in the
            # log (the automation author may never look at the service call
            # result) and must fail the calling action rather than silently
            # doing nothing.
            _LOGGER.warning(
                "Refused to speak notification on %s: data.source_entity is "
                "unusable or its domain is in deny_domains (security is never "
                "spoken)",
                runtime_data.options.media_player,
            )
            raise


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
        self._entry_id = entry.entry_id

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
        runtime_data = _async_live_runtime_data(self.hass, self._entry_id)
        await async_deliver_message(self.hass, runtime_data.options, message)
