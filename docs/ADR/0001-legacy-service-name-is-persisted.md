# 0001 — The legacy notify service name is persisted on the entry

**Status:** Accepted, 0.2.0.
**Applies to:** `custom_components/airplay_notifier/__init__.py`,
`_async_legacy_service_name` and `_async_remove_legacy_service`.

Core paths and line numbers below were verified against
`home-assistant-core` 2026.9.1, the version named at the top of
[`ARCHITECTURE.md`](../ARCHITECTURE.md).

## Context

The legacy service `notify.airplay_<slugify(title)>` follows the entry
**title**, because the title is what a user recognises when they write
`alert.notifiers:`. Two entries can therefore want one name — two speakers
really can both be called "Bedroom".

Core does not arbitrate that. `BaseNotificationService.async_register_services`
(`homeassistant/components/notify/legacy.py:312`) ends with

```python
if self.hass.services.has_service(DOMAIN, self._service_name):
    return
```

so the second entry to register gets **no legacy service at all**, without
a log line, and looks exactly like the one that succeeded. Worse, unloading
either entry then removed the single service they appeared to share.

The first fix numbered colliding entries by their position in
`hass.config_entries.async_entries(DOMAIN)`. That list is the domain index,
and `_EntryIndex.update_unique_id` (`homeassistant/config_entries.py`)
re-indexes an entry by removing it and **appending** it. Reconfiguring the
earlier of two colliding entries therefore moved it behind the other and
handed it that entry's name — a rename of a live `alert.notifiers:` target,
caused by an unrelated action, and then silently refused by the early
return above. Any name derived from a list that reorders itself has this
bug; the list was never the problem to fix, the derivation was.

## Decision

1. **The name lives in `entry.data[CONF_SERVICE_NAME]`.** It is written by
   `_async_legacy_service_name` the first time the entry is set up — which
   is also how an entry created by 0.1.x acquires one — and returned
   unchanged on every later setup. Entry *data*, not options: the name is
   part of the entry's identity, being what `alert.notifiers:` points at,
   and it is set at setup time rather than tuned by the user.
2. **It is recomputed only on a rename that stops deriving it.**
   `_derives_from(stored, base)` is true for `base` itself and for
   `base_<digits>`, so `airplay_bedroom_2` still derives from
   `airplay_bedroom` while `airplay_kitchen` does not. In practice that
   means a rename and nothing else: a reconfigure, a reload, a restart and
   the deletion of another entry all leave the name alone.
3. **A suffix is chosen against persisted names, not against live
   services.** The candidate set is the `CONF_SERVICE_NAME` of every other
   entry of this domain returned by `async_entries(DOMAIN)`, **including
   disabled and ignored entries**; `base`, then `base_2`, `base_3`, … until
   one is free. A disabled entry keeps its claim, because it is going to be
   re-enabled one day and should find its own target waiting.
4. **Registration goes through an ownership map.**
   `hass.data[LEGACY_SERVICE_OWNERS]` maps name → `entry_id` and is claimed
   **synchronously** in `async_setup_entry`, before the discovery task that
   actually registers the service runs — two entries setting up in the same
   event-loop iteration would otherwise both find the name free.
5. **No steal, and no retract of somebody else's.** A name already in the
   map, or already reported by `hass.services.has_service`, is logged at
   `ERROR` and left alone; the entry still loads and its `NotifyEntity` is
   unaffected. `_async_remove_legacy_service` returns immediately unless
   the map says this entry owns the name, because core's silent early
   return makes an entry that lost a name race indistinguishable from one
   that won it — without the guard, its unload deleted a service it had
   never registered.

The overriding rule behind all five: **a name a user has written down never
moves on its own.** Only an explicit rename changes it.

## Consequences

- **Names are reserved, including by entries that are not running.** A
  disabled or ignored entry holds its name against a new entry created in
  the meantime. That is the point, and it is also a way to "lose" a name to
  an entry nobody can see in the UI without opening the disabled list.
- **A stale suffix survives a rename round-trip.** Rename "Bedroom" to
  "Bedroom 2" and the stored `airplay_bedroom` stops deriving from the new
  base, so the name is recomputed to `airplay_bedroom_2`. Rename it back to
  "Bedroom" and `airplay_bedroom_2` *does* derive from `airplay_bedroom` —
  so it is kept, and the plain name is never reclaimed even though nothing
  holds it. Reclaiming it would mean renaming the service on a rename that
  was supposed to leave it alone, which is exactly the failure this record
  exists to prevent. Stable names over promotion. Pinned by
  `tests/test_notify.py::test_renaming_back_to_the_plain_title_keeps_the_suffixed_name`
  and listed in [`known-issues.md`](../known-issues.md).
- **Deleting the entry that holds the plain name promotes nobody.** The
  survivor keeps `airplay_bedroom_2`; the freed name is simply available to
  the next entry that asks for it.
- **The stored name can disagree with reality**, when the name was taken by
  another integration's notify service. Diagnostics report both
  `legacy_service_name` (what the entry wants) and
  `legacy_service_registered` (what the ownership map says it got), since
  `hass.services.has_service` cannot tell the two apart.
- **`entry.data` gains a key that the config flow never asks for.** It is
  written during setup, before any update listener is registered, so the
  write cannot reload the entry it is setting up.

Aligned with the Cast and Assist Satellite notifier adapters of Notify
Switchboard, which take the same five decisions; that alignment lets the
three be compared line by line, and is not what makes them right here.
