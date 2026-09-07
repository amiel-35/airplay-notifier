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

import logging
from dataclasses import dataclass, field
from functools import partial

from homeassistant.components.notify.const import DOMAIN as NOTIFY_DOMAIN
from homeassistant.components.notify.legacy import NOTIFY_SERVICES
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import discovery
from homeassistant.util import slugify
from homeassistant.util.hass_dict import HassKey

from .config_flow import AirplayNotifierConfigFlow
from .const import (
    CONF_ANNOUNCE_PREFIX,
    CONF_DENY_DOMAINS,
    CONF_LANGUAGE,
    CONF_MEDIA_PLAYER,
    CONF_QUIET_END,
    CONF_QUIET_START,
    CONF_QUIET_VOLUME,
    CONF_RESTORE_VOLUME,
    CONF_SERVICE_NAME,
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
from .delivery import AirplayNotifierOptions, VolumeRestoreState

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.NOTIFY]

# Which config entry registered which `notify.<name>` service, so an entry
# can only ever retract a name it actually holds. Claimed synchronously in
# `async_setup_entry`, because the registration itself happens later, in a
# discovery task: two entries setting up in the same event-loop iteration
# would otherwise both find the name free.
LEGACY_SERVICE_OWNERS: HassKey[dict[str, str]] = HassKey(f"{DOMAIN}_legacy_services")


@dataclass(slots=True)
class AirplayNotifierRuntimeData:
    """Runtime data stored on the config entry."""

    options: AirplayNotifierOptions
    legacy_service_name: str
    volume_state: VolumeRestoreState = field(default_factory=VolumeRestoreState)


type AirplayNotifierConfigEntry = ConfigEntry[AirplayNotifierRuntimeData]


def _build_options(entry: AirplayNotifierConfigEntry) -> AirplayNotifierOptions:
    """Merge entry data (setup-time) and options (editable later) into one config.

    `media_player` and `tts_entity` live in the entry's *data*: they are
    set at setup time and changed through the reconfigure flow, which
    reloads the entry. Everything else is options, tuned from the options
    flow without re-adding the entry.
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
        quiet_start=settings.get(CONF_QUIET_START),
        quiet_end=settings.get(CONF_QUIET_END),
        quiet_volume=settings.get(CONF_QUIET_VOLUME),
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

    Only a name this entry actually registered is retracted. Core's early
    return is silent, so an entry whose name was already taken looks exactly
    like one that registered successfully — and removing the service on its
    unload would delete somebody else's.
    """
    owners = hass.data.setdefault(LEGACY_SERVICE_OWNERS, {})
    if owners.get(service_name) != entry_id:
        return
    del owners[service_name]

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


def _legacy_service_base(title: str) -> str:
    """Return the unsuffixed service name a title asks for."""
    return f"airplay_{slugify(title)}"


def _derives_from(service_name: str, base: str) -> bool:
    """Return whether `service_name` is `base` or one of its `_<n>` variants.

    This is how a stored name is recognised as still belonging to the
    entry's current title: `airplay_bedroom` and `airplay_bedroom_2` both
    derive from `airplay_bedroom`, `airplay_kitchen` does not.
    """
    if service_name == base:
        return True
    prefix = f"{base}_"
    return service_name.startswith(prefix) and service_name[len(prefix) :].isdigit()


@callback
def _async_legacy_service_name(
    hass: HomeAssistant, entry: AirplayNotifierConfigEntry
) -> str:
    """Return — and, the first time, persist — the `notify.<name>` of `entry`.

    The name follows the entry *title* — the player's friendly name at
    setup time, editable by renaming the entry — and not the player's
    entity id, because that is what a user recognises in
    `alert.notifiers:`.

    Two entries can want the same name (two speakers really can be called
    "Bedroom"). Core would give it to the first and silently leave the
    second without any legacy service at all:
    `BaseNotificationService.async_register_services`
    (`homeassistant/components/notify/legacy.py`) returns early when the
    service already exists — and unloading the first entry would then
    remove the service both were sharing. Colliding entries are numbered
    instead.

    The number is **not** a position in `async_entries(DOMAIN)`. That list
    is the domain index, and `_EntryIndex.update_unique_id`
    (`homeassistant/config_entries.py`) re-indexes an entry by removing it
    and appending it again — so reconfiguring the earlier of two colliding
    entries moved it behind the other and handed it that entry's name. The
    chosen name is instead written to `entry.data[CONF_SERVICE_NAME]` the
    first time the entry is set up (which is also how an entry created by
    0.1.x acquires one), and every later setup returns it unchanged. It is
    recomputed only when the entry title no longer derives it, i.e. when
    the user renames the entry — and then against the *persisted* names of
    the other entries of this domain, disabled and ignored ones included,
    so a name stays reserved for an entry that is not currently loaded.
    """
    base = _legacy_service_base(entry.title)
    stored = entry.data.get(CONF_SERVICE_NAME)
    if isinstance(stored, str) and _derives_from(stored, base):
        return stored

    taken: set[str] = set()
    for other in hass.config_entries.async_entries(DOMAIN):
        if other.entry_id == entry.entry_id:
            continue
        other_name = other.data.get(CONF_SERVICE_NAME)
        if isinstance(other_name, str):
            taken.add(other_name)

    service_name = base
    index = 1
    while service_name in taken:
        index += 1
        service_name = f"{base}_{index}"

    # No update listener is registered yet at this point in
    # `async_setup_entry`, so this write cannot reload the entry it is
    # setting up; it only persists the name and schedules a save.
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_SERVICE_NAME: service_name}
    )
    return service_name


@callback
def _async_missing_targets(
    hass: HomeAssistant, options: AirplayNotifierOptions
) -> list[str]:
    """Return the configured targets that are absent from the state machine.

    Only *absence* counts, not `unavailable`: a speaker that is switched
    off overnight would otherwise keep the whole entry in setup-retry, and
    the notify entity that exists precisely to report that unavailability
    would never be created.
    """
    return [
        entity_id
        for entity_id in (options.media_player, options.tts_entity)
        if hass.states.get(entity_id) is None
    ]


async def async_setup_entry(
    hass: HomeAssistant, entry: AirplayNotifierConfigEntry
) -> bool:
    """Set up AirPlay Notifier from a config entry."""
    options = _build_options(entry)

    # `test-before-setup`: an entry pointing at entities that do not exist
    # can only fail, once per announcement, in the logs. Deferring instead
    # makes Home Assistant retry with a backoff, which is the right answer
    # for the common case — the target's own integration has simply not
    # finished starting up. `music_assistant` and `apple_tv` are
    # `after_dependencies`, not hard ones, and a TTS engine
    # (`wyoming`, a cloud provider, …) is not a dependency at all.
    if missing := _async_missing_targets(hass, options):
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="target_unavailable",
            translation_placeholders={"entities": ", ".join(missing)},
        )

    legacy_service_name = _async_legacy_service_name(hass, entry)
    entry.runtime_data = AirplayNotifierRuntimeData(
        options=options,
        legacy_service_name=legacy_service_name,
    )

    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    # A volume restore is scheduled, not awaited, so it must not survive the
    # entry: the timer would still fire (and move the player's volume) after
    # the entry, and possibly the whole integration, is gone. Cancelling is
    # not enough on its own — an options change *reloads* the entry, and a
    # reload landing inside the announcement window would cancel the restore
    # and never re-arm it. So a pending restore is performed immediately
    # here, then disarmed. Coroutines are supported by `async_on_unload` and
    # awaited before the unload completes (`homeassistant/config_entries.py`,
    # `_async_process_on_unload`).
    entry.async_on_unload(
        partial(entry.runtime_data.volume_state.async_flush_pending_restore, hass)
    )
    entry.async_on_unload(
        lambda: _async_remove_legacy_service(hass, entry.entry_id, legacy_service_name)
    )

    # Legacy `notify.airplay_<name>` service, discovered the same way
    # `mobile_app` discovers its own per-device notify services (see
    # `homeassistant/components/notify/legacy.py`: the discovered platform's
    # `CONF_NAME` becomes the slugified service name).
    #
    # Core would take the name if it were free and return early, without a
    # word, if it were not. Neither is acceptable for a name an
    # `alert.notifiers:` points at, so the claim is made here: an owned or
    # otherwise occupied name is reported and left alone, and the entry
    # still loads — its `NotifyEntity` works regardless.
    owners = hass.data.setdefault(LEGACY_SERVICE_OWNERS, {})
    if legacy_service_name in owners or hass.services.has_service(
        NOTIFY_DOMAIN, legacy_service_name
    ):
        _LOGGER.error(
            "Cannot register notify.%s for %s: that action name is already in "
            "use. Rename this entry to give it a name of its own; its "
            "notify entity is unaffected",
            legacy_service_name,
            entry.title,
        )
    else:
        owners[legacy_service_name] = entry.entry_id
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


async def async_migrate_entry(
    hass: HomeAssistant, entry: AirplayNotifierConfigEntry
) -> bool:
    """Migrate a config entry to the current schema.

    1.1 → 1.2 (0.2.0) added the quiet-hours option keys. Nothing stored
    has to be rewritten — an absent key already means "off" — but the
    stamp does have to move forward, because core never bumps it itself
    (`homeassistant/config_entries.py`, `ConfigEntry.async_migrate`, which
    only schedules a save once this hook returns `True`).

    A *downgrade* is refused outright — both a newer major version and a
    newer minor version of the same major. Core only calls this hook when
    the stored version differs from the flow's, and it treats `False` as a
    migration failure (`homeassistant/config_entries.py`,
    `async_migrate_entry`: the entry lands in `MIGRATION_ERROR` instead of
    being set up), which is what we want: an entry written by a newer
    AirPlay Notifier may carry keys this code does not understand, and a
    minor bump is by definition backwards-compatible *forwards only*.
    """
    if entry.version != AirplayNotifierConfigFlow.VERSION:
        return False
    if entry.minor_version > AirplayNotifierConfigFlow.MINOR_VERSION:
        return False
    hass.config_entries.async_update_entry(
        entry, minor_version=AirplayNotifierConfigFlow.MINOR_VERSION
    )
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
