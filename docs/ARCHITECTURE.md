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
(`homeassistant/components/assist_satellite/entity.py`, around line
655-696):

1. `homeassistant.components.tts.generate_media_source_id(hass, message,
   engine=tts_entity, language=..., options=...)` — re-exported from
   `homeassistant/components/tts/media_source.py`
   (`generate_media_source_id`) — builds the
   `media-source://tts/<engine>?message=...` identifier.
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

## Deny-list ("security is never spoken")

`delivery._check_deny_list` inspects `data.source_entity`, if the caller
provided one, and raises `AnnouncementDenied` when its domain is in
`deny_domains` (default: `alarm_control_panel`, `lock`). This runs before
any TTS resolution or service call, so a misconfigured automation can never
cause an alarm state or a lock's state to be read aloud. There is no
override for this at call time — silencing it requires changing
`deny_domains` in the options, a deliberate, visible configuration change.

## Config flow / options flow split

`media_player` and `tts_entity` are fixed at setup time (they are the
entry's identity — its `unique_id` is the media player's entity_id, and the
legacy service name is derived from the entry title, which defaults to the
player's friendly name at creation time). Everything else — language,
voice, volume, restore behavior, strategy override, announce prefix, and
the deny-list — is tunable afterward from the options flow without
recreating the entry.

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
