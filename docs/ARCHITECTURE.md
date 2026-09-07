# Architecture

All API references below were verified against `home-assistant-core` at the
version this integration targets, 2026.9.1 (source checked out locally at
`_ref/home-assistant-core`, matching `pytest-homeassistant-custom-component`
0.13.364 and `homeassistant==2026.9.1` in `requirements_dev.txt`).

## Why this integration exists

Home Assistant has no native `notify.*` that speaks. Voice output is
`tts.speak`, an *action* (a service call with a target), not a notifier —
and the core `alert` integration (and most blueprints) can only call
`notify.*` services listed under `notifiers:`. There is no way to point
`alert.notifiers` at an arbitrary action. AirPlay Notifier closes that gap:
it wraps `tts.speak` (or, when the target is a Music Assistant player, an
equivalent play_announcement path) behind an ordinary `notify.*` surface.

## One entry, one player, two notify surfaces

One config entry manages exactly one `media_player`. It exposes:

- a **legacy `notify.airplay_<name>`** service, registered through Home
  Assistant's discovery helper
  (`homeassistant/helpers/discovery.py`, `async_load_platform`) the same
  way `mobile_app` discovers its own per-device notify services. This is
  the only way to make an entry's notifier available to `alert.notifiers:`,
  since `alert` only accepts legacy service names
  (`homeassistant/components/notify/legacy.py`: the discovered platform's
  `CONF_NAME`, once slugified via `homeassistant.util.slugify`, becomes the
  service name — `async_get_service`/`async_setup_platform`, around line
  48-131).
- a modern **`NotifyEntity`** (`homeassistant/components/notify/__init__.py`,
  `class NotifyEntity`, around line 128), the forward-looking surface for
  `notify.send_message` and future automations.

Both call into `delivery.async_deliver_message` with the same
`AirplayNotifierOptions`, so behavior never diverges between them (see
`__init__.py: AirplayNotifierRuntimeData`).

### Neither surface may cache its options

`BaseNotificationService.async_register_services`
(`homeassistant/components/notify/legacy.py`) ends with:

```python
if self.hass.services.has_service(DOMAIN, self._service_name):
    return
```

`async_setup_entry` re-dispatches `discovery.async_load_platform` on every
setup, and `async_load_platform` sends its dispatcher signal
unconditionally (`homeassistant/helpers/discovery.py`, around line
137-175), so a reload really does call `async_get_service` again and build
a fresh service object — but that object is never wired to
`notify.airplay_<name>`, because the service already exists. The **first**
instance keeps answering the service for the lifetime of the Home Assistant
process.

Consequently both `AirplayNotifierNotificationService` and
`AirplayNotifierEntity` store only the `entry_id` and resolve
`hass.config_entries.async_get_entry(entry_id).runtime_data` on every
`async_send_message` (`notify._async_live_runtime_data`). Anything captured
by value at construction time would be frozen at first setup and no options
change would ever take effect.

### Unload has to undo the legacy registration by hand

The same module has no unload path for a discovery-registered platform: it
appends to `hass.data[NOTIFY_SERVICES][<integration>]` (the `NOTIFY_SERVICES`
`HassKey`, literally `notify_services`) and only removes services through
`async_reset_platform`, which the config-entry lifecycle never calls for us.
`async_setup_entry` therefore registers an `entry.async_on_unload` hook
(`_async_remove_legacy_service`) that removes the service and drops this
entry's instance from `hass.data`. Without it an unloaded entry leaves a
live service pointing at a dead entry, and every reload leaks one more
instance.

## Delivery strategies

### Strategy selection (`auto`)

`delivery._resolve_strategy` looks up the target `media_player`'s owning
integration in the entity registry:
`homeassistant/helpers/entity_registry.py`, `async_get(hass)` returns the
`EntityRegistry`; `EntityRegistry.async_get(entity_id)` returns a
`RegistryEntry` whose `.platform` field holds the integration domain that
set up the entity (`class RegistryEntry`, `platform: str = attr.ib()`,
around line 217). If `platform == "music_assistant"`, the Music Assistant
strategy is used; otherwise, Direct. `strategy: music_assistant` or
`strategy: direct` in the options bypasses this detection entirely.

### Availability check (`_async_usable_strategy`)

`music_assistant` is an `after_dependencies` entry in the manifest, not a
hard dependency, so the target player can be registered to the
`music_assistant` platform while the integration itself is not loaded — not
set up yet during startup, unloaded, or in a failed setup retry. The
resolved strategy is therefore checked against
`hass.services.has_service("music_assistant", "play_announcement")` before
use: if the action is missing, the call falls back to Direct with a warning
(the announcement still happens), and only if `tts.speak` is missing too
does it raise a translated `no_delivery_path` error. Previously this failed
with a bare `ServiceNotFound`.

### Direct strategy: `tts.speak`

Calls the core `tts.speak` action
(`homeassistant/components/tts/__init__.py`, registered around line
448-456 as an entity service on the `tts` domain, schema requiring
`media_player_entity_id` + `message`, optional `language`/`options`/`cache`).
Internally (`homeassistant/components/tts/entity.py`,
`TextToSpeechEntity.async_speak`, around line 133), this builds a
`media-source://tts/<engine>?message=...` identifier and calls
`media_player.play_media` with `announce: true` on the target player.

For an `apple_tv` media player, `async_play_media`
(`homeassistant/components/apple_tv/media_player.py`, around line 353-372)
resolves that `media-source://` URI itself via
`media_source.async_resolve_media`, then streams it over RAOP
(`FeatureName.StreamFile`) or plays it via AirPlay
(`FeatureName.PlayUrl`), depending on what the device reports
(`SUPPORT_FEATURE_MAPPING`, around line 78-97). So the Direct strategy needs
no manual URL resolution at all — it is exactly the two-line call the task
describes.

### Music Assistant strategy: manual TTS resolution + `play_announcement`

This is the one place this integration does more work than the naive
reading of the task suggests, and it is worth documenting precisely because
the obvious assumption ("Music Assistant accepts media-source URLs
directly") turned out to be **false** for the code path this integration
actually uses.

**What was verified:**

- `homeassistant/components/music_assistant/services.py` registers
  `play_announcement` (`SERVICE_PLAY_ANNOUNCEMENT`) as a platform entity
  service (around line 148-158) whose `url` field is `cv.string` — a plain
  string, with **no** media-source handling in its schema — dispatched to
  `MusicAssistantPlayer._async_handle_play_announcement`.
- `homeassistant/components/music_assistant/media_player.py`,
  `_async_handle_play_announcement` (around line 564-577), forwards `url`
  unchanged to `self.mass.players.play_announcement(self.player_id, url,
  pre_announce=..., pre_announce_url=..., volume_level=...)`. There is no
  `media_source.async_resolve_media` call anywhere on this path.
- The **only** place Music Assistant resolves a `media-source://` URI is
  `MusicAssistantPlayer.async_play_media`
  (`homeassistant/components/music_assistant/media_player.py`, around line
  384-411), the handler for the generic `media_player.play_media` service:
  it resolves the media source and *then*, only if `announce=True` was
  passed as a kwarg to `play_media` (not to `play_announcement`), forwards
  to the same `_async_handle_play_announcement`.

The task specifies calling `music_assistant.play_announcement` directly, so
this integration resolves the TTS message to a real HTTP(S) URL itself,
before calling that action, using the same sequence Home Assistant's own
`assist_satellite` entity uses for the identical problem
(`homeassistant/components/assist_satellite/entity.py`,
`AssistSatelliteEntity._resolve_announcement_media_id`, lines 638-700):

1. `homeassistant.components.tts.generate_media_source_id(hass, message,
   engine=tts_entity, language=..., options=..., cache=True)` — re-exported
   from `homeassistant/components/tts/media_source.py`
   (`generate_media_source_id`) — builds the
   `media-source://tts/<engine>?message=...&cache=true` identifier.
   `cache=True` is passed explicitly so this path uses the same TTS file
   cache the Direct path gets from `tts.speak`'s `cache: true`:
   `generate_media_source_id` encodes it in the identifier and
   `parse_media_source_id` turns it back into the `use_file_cache` flag on
   the `ResultStream`. Omitting it leaves the flag at `None` (engine
   default), so the two strategies would disagree about caching identical
   text.
2. `homeassistant.components.media_source.async_resolve_media(hass,
   media_content_id, target_media_player)` — `async_resolve_media` at
   `homeassistant/components/media_source/helper.py:115` — resolves it to a
   `PlayMedia` with a real `.url` (this is what actually invokes the TTS
   engine to synthesize the clip and returns a servable path/URL).
3. `homeassistant.components.media_player.async_process_play_media_url(hass,
   url)` — `homeassistant/components/media_player/browse_media.py:34` —
   turns a relative path into an absolute URL and signs it if it points
   back at this Home Assistant instance, so an external device (the Music
   Assistant server, potentially off-box) can fetch it without
   authenticating.

   **Which hostname that is, is not this integration's choice.** To make the
   path absolute the helper calls
   `homeassistant.helpers.network.get_url(hass)` (same file, around line
   71-90), which walks `[internal, external]` and returns whichever it can
   resolve — so with no internal URL configured, the signed
   `/api/tts_proxy/...` link is built on the **external** URL and the clip
   is reachable from outside the LAN for the lifetime of the signature
   (`CONTENT_AUTH_EXPIRY_TIME`). Worse, `get_url` *reverses* that order
   outright when Home Assistant's own API is served over SSL
   (`prefer_external = hass.config.api is not None and
   hass.config.api.use_ssl`, `homeassistant/helpers/network.py:133-141`),
   so an SSL-terminating instance prefers the external URL even when an
   internal one exists. Configuring an internal URL in Settings → System →
   Network is the only way to keep the clip on the LAN. The Direct strategy
   never hits this: `tts.speak` hands the media-source id to the player,
   which resolves it locally. Recorded in `docs/known-issues.md`.

Only then is `music_assistant.play_announcement` called, with that URL.
Music Assistant's own `announce_volume` field
(`homeassistant/components/music_assistant/const.py`,
`ATTR_ANNOUNCE_VOLUME`) is used to carry this integration's `volume`
setting (converted from Home Assistant's 0.0-1.0 scale to MA's 1-100
scale); MA's own player library raises to that level for the announcement
and restores it afterward on its own, so `restore_volume` has nothing to do
for this strategy — see `delivery._async_deliver_music_assistant`'s
docstring.

## Volume set/restore (Direct strategy only)

`delivery._async_deliver_direct` reads the target's current
`volume_level` state attribute
(`homeassistant/components/media_player/const.py`,
`ATTR_MEDIA_VOLUME_LEVEL = "volume_level"`), calls
`media_player.volume_set` (`homeassistant/const.py`,
`SERVICE_VOLUME_SET = "volume_set"`) to the configured `volume`, then
calls `tts.speak`.

Home Assistant's TTS pipeline does not expose the duration of the
synthesized clip anywhere accessible before playback starts — there is no
`duration` field on `PlayMedia`
(`homeassistant/components/media_source/models.py`) — so there is no signal
to wait for "the announcement has finished". This integration uses a
documented heuristic instead (`const.py`:
`SPEECH_SECONDS_PER_CHARACTER = 0.07`, `RESTORE_DELAY_PADDING_SECONDS =
2.0`, `MIN_RESTORE_DELAY_SECONDS = 3.0`) to size a delay, then schedules the
restore via `homeassistant.helpers.event.async_call_later`
(`homeassistant/helpers/event.py:1552`) so the notify call itself returns
immediately — nothing in this integration blocks the event loop waiting for
a player to finish talking.

### `VolumeRestoreState`: what a scheduled restore has to survive

Scheduling rather than awaiting means the volume window outlives the call
that opened it, and five things can happen inside it. All five are handled
by one `VolumeRestoreState` per config entry (one entry = one player),
living on `entry.runtime_data`:

- **the announcement fails.** `tts.speak` can raise (unknown engine, a
  player that refuses `play_media`). The restore is armed in a `finally`,
  so a failed announcement never leaves the speaker at announcement volume.
- **a second announcement starts while the first is still speaking.** Each
  call reading the current volume for itself meant the second one captured
  the *announcement* volume; whichever restore fired last then made that
  permanent. `state.original_volume` is now filled in only when it is
  `None`, i.e. once per burst, behind `state.lock` — which also serialises
  the read-then-set so two calls cannot interleave between them.
- **restores stack.** `async_call_later` returns a cancel callback that was
  being discarded, so every overlapping announcement armed its own restore
  and they all fired. Exactly one is armed at a time
  (`_async_schedule_restore` cancels the previous one first), and the
  handle is disarmed on unload instead of moving a player's volume on
  behalf of an integration that is gone.
- **an announcement starts while a restore is already in flight.** The
  restore used to clear `original_volume` and *then* call
  `media_player.volume_set`, outside the lock. A call landing between the
  two read the still-raised level as the "original" and, when its own
  restore fired, made announcement volume permanent. The restore now runs
  entirely inside `state.lock` and clears `original_volume` only once the
  speaker is actually back down, so the next announcement waits and reads
  the true original.
- **the entry is unloaded or reloaded mid-announcement.** Merely cancelling
  the armed restore was silently destructive: an options change *reloads*
  the entry, and the reloaded entry starts from a fresh, empty
  `VolumeRestoreState` — nothing was left to put the speaker back down.
  `VolumeRestoreState.async_flush_pending_restore` is registered with
  `entry.async_on_unload` instead: it performs the pending restore
  immediately, inside the lock and best effort (a failing `volume_set` is
  logged, never raised, so a merely offline speaker cannot mark the entry
  `FAILED_UNLOAD`), and cancels the timer afterwards.

The Music Assistant strategy needs none of this: `announce_volume` is
handled inside Music Assistant's own player library.

## Per-call overrides are legacy-service-only

`data.volume`, `data.language`, `data.voice`, `data.tts_entity`, and
`data.source_entity` (checked by the deny-list) are only reachable through
the legacy `notify.airplay_<name>` service. This is a Home Assistant core
constraint discovered while testing, not a design choice made lightly: the
modern notify entity service (`notify.send_message`) is registered with a
fixed schema —
`homeassistant/components/notify/__init__.py`,
`component.async_register_entity_service(SERVICE_SEND_MESSAGE, {
vol.Required(ATTR_MESSAGE): cv.string, vol.Optional(ATTR_TITLE): cv.string
}, ...)`, around line 84-91 — that accepts only `message` and `title`.
Calling `notify.send_message` with an extra `data` key raises a
`voluptuous.Invalid` error (verified in
`tests/test_notify.py::test_notify_send_message_schema_has_no_data_field`);
there is no way to smuggle extra fields through this action. Consequently,
`AirplayNotifierEntity.async_send_message` never has a `source_entity` to
check and can never raise `AnnouncementDenied` — the deny-list is only ever
exercised through the legacy service path.

## Per-call `data` validation

`delivery.CALL_DATA_SCHEMA` validates the whole payload before anything
runs: `volume` as a float coerced into 0-1, `language` as a string, `voice`
as either a voice id or a full TTS `options` mapping, `tts_entity` through
`cv.entity_domain("tts")`, and `priority` through `vol.In(("normal",
"critical"))` — a closed set, so a typo in the value that decides whether
a 3am alarm is spoken fails the call instead of quietly behaving like
`normal`. Unknown keys are rejected (voluptuous'
`PREVENT_EXTRA` default) so a typo such as `volumne:` fails the call rather
than being ignored and playing at the wrong volume. A schema failure
becomes a `ServiceValidationError` with the `invalid_call_data` translation
key, carrying the voluptuous message as a placeholder.

## Deny-list ("security is never spoken")

`delivery._check_deny_list` inspects `data.source_entity`, if the caller
provided one, and raises `AnnouncementDenied` when its domain is in
`deny_domains` (default: `alarm_control_panel`, `lock`). This runs before
any TTS resolution or service call, so a misconfigured automation can never
cause an alarm state or a lock's state to be read aloud. There is no
override for this at call time — silencing it requires changing
`deny_domains` in the options, a deliberate, visible configuration change.

### Normalisation is part of the check, not a nicety

`source_entity` is caller-supplied and arrives in whatever shape an
automation produces. A raw comparison against `deny_domains` let three
shapes through:

- a **list** — the shape every Home Assistant `entity_id` field accepts —
  because `"." not in ["lock.front_door"]` is true, so the check returned
  having done nothing;
- any **capitalisation**, because entity domains are lower-case;
- a **non-string**, which raised `TypeError` deep inside delivery.

A **tuple or set** — which a template or a Python-side caller can equally
well produce — needs its own unwrapping: `cv.ensure_list` only unwraps a
`list`, so a tuple was wrapped whole and then refused as the single
unusable id `"('lock.front_door',)"`. Right outcome, wrong reason — and a
tuple of perfectly *allowed* entities was refused too.
`_as_source_entity_items` unwraps `list`, `tuple`, `set` and `frozenset`
explicitly. Deliberately not "any iterable": `str` is iterable.

`_normalise_source_entities` therefore runs `_as_source_entity_items` →
`str()` → `.strip().casefold()` over the value and validates each result
with
`homeassistant.core.valid_entity_id`; `deny_domains` entries are casefolded
too. Anything that is not a usable `domain.object_id` is **refused**, not
ignored: a message whose provenance cannot be checked is precisely what the
deny-list exists to stop.

### Refusals are errors, not silence

`AnnouncementDenied` subclasses `ServiceValidationError`
(`homeassistant/exceptions.py`), which Home Assistant renders to the user
without a stack trace, and every instance carries a `translation_key`
resolved from the `exceptions` section of `strings.json`
(`invalid_source_entity`, `source_domain_denied`; also `invalid_call_data`,
`entry_not_loaded`, `no_delivery_path`). The legacy notify service logs a
warning **and** re-raises, so the refusal is both visible in the log and
fails the calling automation. A silently dropped announcement is a worse
failure mode than a loud one — with the consequence, noted in
`docs/known-issues.md`, that an `alert` listing a refused notifier reports
an error.

## Config flow / options flow split

`media_player` and `tts_entity` live in the entry's **data**: they are the
entry's identity (its `unique_id` is the media player's entity_id) and are
changed through `async_step_reconfigure`, which reloads the entry.
Everything else — language, voice, volume, restore behavior, strategy
override, announce prefix, the deny-list and quiet hours — is **options**,
tunable from the options flow.

### Checking the target before committing to it

Two checks run before an entry is created or changed
(`test-before-configure`), both permissive when the evidence is absent:

- `_supports_play_media` refuses a player whose `supported_features`
  excludes `MediaPlayerEntityFeature.PLAY_MEDIA`. Everything here ends in
  `media_player.play_media`, which core refuses with `ServiceNotSupported`
  for such a player, so it can never speak. A player with no
  `supported_features` attribute at all (a template entity) is accepted:
  an absent attribute is not evidence.
- `_is_music_assistant_player` refuses `strategy: music_assistant` when
  the entity registry attributes the player to another integration —
  `music_assistant.play_announcement` is a platform entity action and only
  accepts Music Assistant's own players. A player absent from the registry
  is accepted, for the same reason.

### The reconfigure flow keeps the entry's name

`async_step_reconfigure` ends in `async_update_reload_and_abort`
(`homeassistant/config_entries.py`) with `data_updates`, so the entry keeps
its options and reloads onto the new targets. Two things deliberately do
not move: the entry **title**, because the legacy `notify.airplay_<name>`
service is derived from it and renaming it silently would break every
`alert.notifiers:` pointing at it; and the unique-id rule, one entry per
player — which is why the duplicate check ignores the entry being
reconfigured, or changing only the TTS engine would abort as
`already_configured`.

While an entry also has an update listener (which is what makes an options
change take effect), `async_update_reload_and_abort` logs a transitional
notice about scheduling the reload itself; both paths end in the same
reload and the listener is the one that survives in 2026.12.

### Entry versioning

`VERSION = 1`, `MINOR_VERSION = 2`. `async_migrate_entry` refuses every
downgrade — a newer major version *and* a newer minor version of the same
major both return `False`, which core turns into a `MIGRATION_ERROR`
instead of setting the entry up — and stamps an older entry with the
current minor version, because core never does that itself
(`ConfigEntry.async_migrate` only schedules a save once the hook returns
`True`). 1.1 → 1.2 rewrites nothing: it records that the entry may now
carry the quiet-hours keys, which a 0.1.x build would ignore silently and
start speaking at 03:00.

## Quiet hours

`delivery._quiet_hours_volume` runs after the deny-list and decides two
things at once: whether the announcement happens, and how loud.

The window is `[quiet_start, quiet_end)` in **local time**, half-open so a
window ending at 07:00 is over at 07:00 sharp. `start > end` crosses
midnight, which is the ordinary case for "the night". Both bounds are
required — the options form refuses half a window, and an entry that
carries one anyway is treated as *off*, because silencing announcements on
the strength of a bound whose other half is unknown is the failure this
feature exists to prevent. A zero-length window (`start == end`) is empty,
not permanent, for the same reason.

Inside the window, in order:

1. `data.priority: critical` lets the call through untouched;
2. no `quiet_volume` configured refuses it — `QuietHoursRefusal`, a
   `ServiceValidationError` with the `quiet_hours` translation key, logged
   at **INFO** because this is the configuration working, not a fault;
3. `data.volume`, if the caller set one, wins over `quiet_volume`;
4. `quiet_volume`.

`data.volume` therefore decides *how loud*, never *whether*: a call that
sets a volume but no priority is still refused by (2), or every automation
that happens to set a volume would opt itself out of quiet hours.

`QuietHoursRefusal` is deliberately **not** an `AnnouncementDenied`: the
legacy service logs a deny-list warning about a misconfigured automation
when it catches that one, and a quiet-hours refusal must not borrow that
line.

## Setup deferral and availability

Two different questions, answered in two places.

`async_setup_entry` raises `ConfigEntryNotReady` (translation key
`target_unavailable`) when either target is **missing from the state
machine**, naming both when both are. Home Assistant then retries with a
backoff, which is the right answer for the usual cause: the target's own
integration has not finished starting. `music_assistant` and `apple_tv`
are `after_dependencies` rather than hard ones, and a TTS engine
(`wyoming`, a cloud provider) is not a dependency at all.

Only absence defers setup. An `unavailable` target still loads the entry,
because the entity whose job is to *report* that unavailability has to
exist to report it: `AirplayNotifierEntity` tracks both targets with
`async_track_state_change_event` (the pattern of
`homeassistant/components/switch_as_x/entity.py`) and is `unavailable`
while either is missing or unavailable. Each transition is logged once, in
each direction — not once per state change, or an unavailable speaker
whose attributes keep moving would fill the log with the same line.

## Entities, devices and naming

Each config entry creates one **service device**
(`DeviceEntryType.SERVICE`, identifiers `{(DOMAIN, entry.entry_id)}`, named
after the entry title — the target player's friendly name at creation
time), holding one `NotifyEntity`. That entity is the device's main entity,
so it sets `_attr_has_entity_name = True` with `_attr_name = None` and
takes the device's name: `notify.living_room`, not the `notify.speak` /
`notify.speak_2` collision a hard-coded entity name produced across two
entries. `_attr_translation_key = "speak"` exists for `icons.json` alone
and can never name the entity: `Entity._name_internal`
(`homeassistant/helpers/entity.py`) opens with `if hasattr(self,
"_attr_name"): return self._attr_name`, and declaring `_attr_name = None`
makes that `hasattr` true, so the name lookup on the next branch is never
reached. That is why there is still no `entity` section in `strings.json`
— it would promise a name the entity can never display — while
`icons.json`, resolved by key alone, works.

### The legacy service name, and what happens when two collide

`notify.airplay_<slugify(title)>` follows the entry **title**, not the
player's entity id: the title is what a user recognises in
`alert.notifiers:`, and the two really do differ in the wild. Two entries
can therefore want the same name — two speakers can both be called
"Bedroom". Core would give it to whichever registers first and return
early for the second (`BaseNotificationService.async_register_services`),
leaving that entry with no legacy service at all and making an unload of
the first remove the service they shared.
`_async_legacy_service_name` numbers colliding entries in config-entry
order — creation order, restored from storage — so an entry keeps its name
across reloads and restarts.

## Manifest classification

`integration_type: helper` and `iot_class: calculated`. This integration
does not talk to any hardware or service of its own: it derives a notify
surface from a `media_player` and a `tts` entity that other integrations
already provide, and computes its behaviour from their state. `device` /
`local_push` claimed a device that does not exist and push updates that are
never received.

## Diagnostics

`TO_REDACT` is empty *and applied*. Nothing sensitive is ever stored — no
credentials, no tokens, and a spoken message is never kept anywhere this
integration can report — so there is nothing to redact today; keeping
`async_redact_data` in the path means the day something sensitive does
appear, redacting it is one line in a set rather than a change of shape.
`entry.runtime_data` is read through `getattr`, because core deletes that
attribute on unload (`homeassistant/config_entries.py`,
`object.__delattr__(self, "runtime_data")`) and a broken entry is exactly
when someone downloads diagnostics.

## Not implemented / open questions

- **Pre-announce chime**: `music_assistant.play_announcement` supports
  `pre_announce_url` / `use_pre_announce`; this integration does not expose
  either yet (out of scope for the task's field list).
- **Exact playback-end detection**: the volume-restore delay is a heuristic
  (see above), not an exact signal. A future version could poll the target
  media player's state for `idle`/`playing` transitions instead, at the
  cost of extra state-tracking complexity for what is, in practice, a
  short-lived announcement.
- **AirPlay speakers exposed through the generic `media_player` integration
  in some HA setups** (i.e. not `apple_tv`, not `music_assistant`) fall
  under the Direct strategy by default; whether `tts.speak` +
  `media_player.play_media` works end-to-end depends entirely on that
  integration's own `PLAY_MEDIA` support and its own media-source
  resolution, which this integration does not control and has not audited
  integration-by-integration.
- **Repair issues**: a target that disappears for good shows up as an
  unavailable entity and, at startup, as an entry in setup-retry, but no
  `repair` issue is raised to walk the user through fixing it.
- **Quiet hours follow Home Assistant's own time zone** and are evaluated
  when the announcement arrives. There is no per-entry time zone, and an
  announcement that starts just before the window closes is not cut short.

See [`known-issues.md`](known-issues.md) for the user-facing version of
these, and
[`quality_scale.yaml`](../custom_components/airplay_notifier/quality_scale.yaml)
for the full rule-by-rule self-assessment.
