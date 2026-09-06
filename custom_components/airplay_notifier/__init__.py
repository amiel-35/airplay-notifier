"""The AirPlay Notifier integration.

AirPlay Notifier turns one `media_player` (an AirPlay speaker, HomePod, or
Apple TV, whether or not it is managed by Music Assistant) into a `notify`
target that *speaks*: a legacy `notify.airplay_<name>` service, usable in
`alert.notifiers:` (the core `alert` integration only accepts legacy notify
service names — there is no way to point it at a `NotifyEntity`), and a
modern `NotifyEntity` (`notify.send_message`) for new automations.

One config entry manages exactly one `media_player`. Both notify surfaces
share the same `AirplayNotifierOptions`, rebuilt from the entry's data and
options on every setup/reload and looked up from `entry.runtime_data` on
every call (never captured — see notify.py); see delivery.py for how a
message actually gets spoken.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.notify.const import DOMAIN as NOTIFY_DOMAIN
from homeassistant.components.notify.legacy import NOTIFY_SERVICES
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import discovery
from homeassistant.util import slugify

from .const import (
    CONF_ANNOUNCE_PREFIX,
    CONF_DENY_DOMAINS,
    CONF_LANGUAGE,
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
    DOMAIN,
)
from .delivery import AirplayNotifierOptions

PLATFORMS: list[Platform] = [Platform.NOTIFY]


@dataclass(slots=True)
class AirplayNotifierRuntimeData:
    """Runtime data stored on the config entry."""

    options: AirplayNotifierOptions
    legacy_service_name: str


type AirplayNotifierConfigEntry = ConfigEntry[AirplayNotifierRuntimeData]


def _build_options(entry: AirplayNotifierConfigEntry) -> AirplayNotifierOptions:
    """Merge entry data (setup-time) and options (editable later) into one config.

    `media_player` and `tts_entity` are fixed at setup time (changing the
    target player is a new config entry, not an option); everything else can
    be tuned from the options flow without re-adding the entry.
    """
    settings = {**entry.data, **entry.options}
    return AirplayNotifierOptions(
        media_player=settings[CONF_MEDIA_PLAYER],
        tts_entity=settings[CONF_TTS_ENTITY],
        language=settings.get(CONF_LANGUAGE),
        voice=settings.get(CONF_VOICE),
        volume=settings.get(CONF_VOLUME),
        restore_volume=settings.get(CONF_RESTORE_VOLUME, DEFAULT_RESTORE_VOLUME),
        strategy=settings.get(CONF_STRATEGY, DEFAULT_STRATEGY),
        announce_prefix=settings.get(CONF_ANNOUNCE_PREFIX, DEFAULT_ANNOUNCE_PREFIX),
        deny_domains=list(settings.get(CONF_DENY_DOMAINS, DEFAULT_DENY_DOMAINS)),
    )


@callback
def _async_remove_legacy_service(
    hass: HomeAssistant, entry_id: str, service_name: str
) -> None:
    """Unregister `notify.<service_name>` and drop its service instance.

    The legacy notify machinery has no unload hook for discovery-registered
    platforms: `homeassistant/components/notify/legacy.py` only ever appends
    to `hass.data[NOTIFY_SERVICES][<integration>]` (the `NOTIFY_SERVICES`
    `HassKey`, `notify_services`), and
    `BaseNotificationService.async_register_services` short-circuits when the
    service name already exists. Without this cleanup an unloaded entry would
    leave a live `notify.airplay_<name>` service pointing at a dead entry,
    and every reload would leak one more instance into `hass.data`.
    """
    hass.services.async_remove(NOTIFY_DOMAIN, service_name)

    services = hass.data.get(NOTIFY_SERVICES, {}).get(DOMAIN)
    if services is None:
        return
    remaining = [
        service
        for service in services
        if getattr(service, "entry_id", None) != entry_id
    ]
    if remaining:
        hass.data[NOTIFY_SERVICES][DOMAIN] = remaining
    else:
        hass.data[NOTIFY_SERVICES].pop(DOMAIN, None)


async def async_setup_entry(
    hass: HomeAssistant, entry: AirplayNotifierConfigEntry
) -> bool:
    """Set up AirPlay Notifier from a config entry."""
    legacy_service_name = f"airplay_{slugify(entry.title)}"
    entry.runtime_data = AirplayNotifierRuntimeData(
        options=_build_options(entry),
        legacy_service_name=legacy_service_name,
    )

    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    entry.async_on_unload(
        lambda: _async_remove_legacy_service(hass, entry.entry_id, legacy_service_name)
    )

    # Legacy `notify.airplay_<name>` service, discovered the same way
    # `mobile_app` discovers its own per-device notify services (see
    # `homeassistant/components/notify/legacy.py`: the discovered platform's
    # `CONF_NAME` becomes the slugified service name).
    hass.async_create_task(
        discovery.async_load_platform(
            hass,
            Platform.NOTIFY,
            DOMAIN,
            {CONF_NAME: legacy_service_name, "entry_id": entry.entry_id},
            {},
        ),
        eager_start=True,
    )

    # Modern `NotifyEntity`.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: AirplayNotifierConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_options(
    hass: HomeAssistant, entry: AirplayNotifierConfigEntry
) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
