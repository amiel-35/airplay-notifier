# Known issues and limitations

Things this integration deliberately does not do, or cannot do, as of
0.2.0. Each one is a real constraint that was verified, not a guess. See
[`quality_scale.yaml`](../custom_components/airplay_notifier/quality_scale.yaml)
for the wider self-assessment.

## Per-call `data` only works through the legacy service

`data.volume`, `data.language`, `data.voice`, `data.tts_entity`,
`data.source_entity` and `data.priority` are only reachable through
`notify.airplay_<name>`. Home Assistant's modern `notify.send_message`
action is registered with a fixed `message`/`title` schema
(`homeassistant/components/notify/__init__.py`,
`async_register_entity_service(SERVICE_SEND_MESSAGE, ...)`), so there is no
way to pass a generic `data` payload to a `NotifyEntity`. Calls made
through `notify.<player>` therefore always use the entry's configured
defaults: the deny-list is never exercised on that path — it has no
`source_entity` to inspect — and a call cannot mark itself `critical` to
get through quiet hours. Quiet hours themselves *do* apply to the entity,
using the entry's configured quiet volume.

Locked in by
`tests/test_notify.py::test_notify_send_message_schema_has_no_data_field`,
which fails the day core changes this.

## Volume restore is a timed guess, not an end-of-playback signal

Home Assistant's TTS pipeline does not expose the duration of the
synthesized clip before playback starts (there is no `duration` on
`PlayMedia`, `homeassistant/components/media_source/models.py`). The Direct
strategy therefore restores the player's volume after an estimated delay
(`const.py`: `SPEECH_SECONDS_PER_CHARACTER`, `RESTORE_DELAY_PADDING_SECONDS`,
`MIN_RESTORE_DELAY_SECONDS`).

Consequences:

- a very long message can have its volume restored before it finishes;
- the volume comes back a couple of seconds after a short one.

Overlapping announcements are handled (the original volume is remembered
once and exactly one restore is armed), but the delay itself remains an
estimate. Watching the target's state for a `playing` → `idle` transition
would be exact, at the cost of state-tracking complexity for what is
normally a three-second announcement.

## Music Assistant cannot announce silently: `volume: 0` becomes 1 %

`music_assistant.play_announcement`'s `announce_volume`
(`homeassistant/components/music_assistant/services.py`) is an integer
percentage, and Music Assistant treats it as "play at this level", with no
value meaning "do not play". A configured `volume: 0` is therefore clamped
up to 1 % and a warning is logged, rather than silently played at whatever
the player was already set to.

If you want an announcement not to be heard, disable the automation — do
not set the volume to 0. The Direct strategy does honour `volume: 0`
literally (it calls `media_player.volume_set` with `0`), so the two
strategies genuinely differ here.

## The clip URL may be exposed on your external URL

The Music Assistant strategy resolves the message to a real HTTP URL with
`media_player.async_process_play_media_url`
(`homeassistant/components/media_player/browse_media.py`), because Music
Assistant may run off-box and must be able to fetch the audio. That helper
calls `homeassistant.helpers.network.get_url(hass)`, which walks
`[internal, external]` and **falls back to the external URL** when no
internal URL is configured — and reverses that order outright when Home
Assistant's own API is served over SSL (`get_url`, `prefer_external =
hass.config.api is not None and hass.config.api.use_ssl`). In either case
the signed `/api/tts_proxy/...` link handed to Music Assistant points at
your public hostname.

The link is signed and short-lived, but if that matters to you, configure an
internal URL in **Settings → System → Network**.

## A missing target defers the entry instead of raising a repair issue

Since 0.2.0 the entry refuses to load while its `media_player` or TTS
entity is absent from the state machine, and retries with a backoff — the
right answer while the target's own integration is still starting. A
target that is gone *for good* (renamed, deleted) therefore leaves the
entry retrying forever, visible in Settings → Devices & services but not
raised as a repair issue walking you through the fix. Use **Reconfigure**
to point the entry at what exists now.

An `unavailable` target — a speaker that is switched off — is a different
case: the entry loads and the notify entity reports itself unavailable,
which is what you want to see.

## Renaming an entry renames its notify service

The legacy service name follows the entry title, so renaming the entry
renames `notify.airplay_<name>`, and anything referencing the old name in
`alert.notifiers:` must be updated. The reconfigure flow deliberately does
*not* rename it when you move the entry to another player, precisely so
that a change of speaker cannot silently break an alert.

Two entries whose titles slugify identically get numbered service names
(`airplay_bedroom`, `airplay_bedroom_2`). The name an entry ends up with is
stored on the entry itself and never moves again: a reconfigure, a reload,
a restart, disabling another entry or **deleting** the entry that holds the
plain name all leave it exactly as it was. A rename is the only thing that
changes it.

Three corollaries:

- a disabled entry keeps its name reserved. That is deliberate — you will
  re-enable it one day, and it should find its own `alert.notifiers:`
  target waiting rather than taken by an entry created in the meantime;
- renaming an entry onto a name another entry already holds gets you the
  next free number, not the name itself. Nothing is ever taken away from an
  entry that already has it;
- **renaming back does not give you the plain name back.** Rename
  "Bedroom" to "Bedroom 2" and the service becomes
  `notify.airplay_bedroom_2` — the stored `airplay_bedroom` no longer
  matches the new title. Rename it back to "Bedroom" and the service
  **stays** `notify.airplay_bedroom_2`, because a `_<n>` suffix still
  counts as deriving from `airplay_bedroom`; the plain name is left free
  and unclaimed.

That last one is deliberate, not an oversight. The alternative — reclaiming
the plain name whenever it happens to be free — would rename a live service
during a rename that was supposed to leave it alone, which is the exact
failure the persisted name exists to prevent. Stable names over promotion.
If you do want the plain name back, rename the entry to a genuinely
different title first ("Study", say) and then back to "Bedroom": the trip
through a title that derives nothing is what releases the suffix. The
reasoning is in [ADR 0001](ADR/0001-legacy-service-name-is-persisted.md).

If a name is unavailable for some other reason — another integration
registered `notify.airplay_<something>` first — the entry logs an `ERROR`
naming it and loads anyway, without a legacy service. Its notify **entity**
still works; rename the entry to give it a name of its own.

## An unavailable notify entity silently skips `notify.send_message`

While the player or the TTS engine is `unavailable`, the `notify.<player>`
entity is `unavailable` too — and Home Assistant does not fail a call that
targets it. `async_extract_referenced_entity_ids` filters the candidates
down to the available ones
(`homeassistant/helpers/service.py`, `entity_candidates = [e for e in
entity_candidates if e.available]`, line 722 in 2026.9.1) and
`SelectedEntities.log_missing` (`homeassistant/helpers/target.py:136`)
reports the remainder as a `WARNING`:

```
WARNING homeassistant.helpers.service: Referenced entities
notify.living_room are missing or not currently available
```

So `notify.send_message` on an unavailable entity **succeeds and speaks
nothing**. An automation that only checks whether the action failed will
believe it announced something. Check the entity's availability in a
condition if that matters to you, or use the legacy
`notify.airplay_<name>` service, which is a plain service with no
availability to filter on and which therefore either speaks or raises.

## Quiet hours use Home Assistant's time zone, and only at call time

The window is evaluated against Home Assistant's own local time when the
announcement arrives. There is no per-entry time zone, and an announcement
that starts one second before the window opens is not interrupted — quiet
hours decide whether a message is spoken, not what happens to one already
being spoken.

`data.volume` chooses how loud an announcement that gets through will be;
it does not get it through. Only `data.priority: critical` bypasses the
window, so an automation that sets a volume cannot opt itself out of quiet
hours by accident.

## Refusals fail the calling action

A deny-list refusal (or an invalid `data` payload) raises
`ServiceValidationError`, which fails the automation or script that called
it, in addition to being logged. That is deliberate — a silently ignored
announcement is worse than a visible failure.

### Under `alert`, one refusal produces two log lines

The core `alert` integration calls its notifiers *without* `blocking=True`
and catches only `ServiceNotFound`
(`homeassistant/components/alert/entity.py`, `_send_notification_message`).
Home Assistant therefore runs the call as a background task and reports the
refusal itself, on top of ours. A single refused announcement reached from
an `alert` logs, in order:

1. `WARNING homeassistant.components.airplay_notifier.notify: Refused to
   speak notification on media_player.…` — ours, one line, no traceback;
2. `ERROR homeassistant.core: Error executing service: <ServiceCall
   notify.airplay_…>` — core's, with a full `ServiceValidationError`
   traceback, from `HomeAssistant._run_service_call_catch_exceptions`
   (`homeassistant/core.py`).

Both are expected and describe the same refusal; the traceback is noise, not
a crash. Note the corollary: because that call is not blocking, the refusal
does **not** fail the alert — the alert carries on and retries at its next
notification interval. A refusal only fails its caller when the caller
awaits the service call, which automations and scripts do.

## Spanish translations are machine-translated

`translations/es.json` was not written by a Spanish speaker. Corrections are
welcome.

## Final findings, not fixed (2026-09-07, repository archived)

Recorded the day the repository was archived, so that anyone still
installing the code knows what to expect. The sibling Cast Notifier was
tested on real hardware the same day (Home Assistant 2026.9.1, a Google
speaker, Music Assistant 2.10, Cloud TTS); this integration's Direct
strategy was **not** exercised on a real AirPlay receiver. Timings are
relative to the service call.

### #6 — Nothing said what happens to the music

- **Direct strategy** (`tts.speak` on an `apple_tv` player): the receiver
  was being streamed to by some sender; the clip takes it over and, when it
  ends, Home Assistant has no handle on that sender's stream. Nothing can be
  resumed, not even best effort. Only the volume comes back, on a timer.
- **Music Assistant strategy**: Music Assistant owns the queue, so it pauses,
  announces at its own announce volume (+85 % by default, per-player
  setting: 0.36 → 0.66 measured) and resumes. Measured on a Music Assistant
  player: pause +0.5 s, voice +3.4 s, volume restored +9.5 s, radio resumed
  +11–12 s.

### #7 — Merge into Cast Notifier, then archive both

A single speaker notifier choosing its path from the entity's platform was
planned (amiel-35/cast-notifier#8), then dropped: the core already provides
the `notify` target (below), and the Music Assistant path is native.

### The core already does the job (cast-notifier#8)

`homeassistant/components/tts/notify.py` (legacy `notify: - platform: tts`,
still shipped in 2026.9.1) gives a `notify.<name>` on any `media_player`;
Cloud TTS uses the language's default voice (`cloud/tts.py`,
`DEFAULT_VOICES`). Measured end to end on a Music Assistant player: the call
returns in 0.05 s (fire-and-forget), pause +0.5 s, voice +2.1 → +8.1 s,
volume restored +9.5 s, radio resumed +11.4 s. Its gaps — no UI, no error
back to the caller, no per-call options, a Core restart to load it (no
`notify.reload` service) — were judged not worth a custom integration once
policy (deny list, quiet hours, priority) lives in a notify router.

### Findings on the Cast side that also apply here

From amiel-35/cast-notifier `docs/known-issues.md`, "Final findings":
refusals over the REST API surface as HTTP 500 (core's
`APIDomainServicesView` maps only `vol.Invalid` and `ServiceNotFound`); a
config-flow entity filter is frontend-only, a non-matching platform can be
picked through the API; Music Assistant's announce volume rule (+85 %)
applies whenever the native path is taken, whatever the entry's `volume`.

### #8 — Companion-only `data` keys are refused

Open at archive time: a `data` payload shared with a `mobile_app` notifier
(`tag`, `image`, `url`, `actions`, `group`, `channel`, `push`, `ttl`,
`notification_id`) is refused instead of ignored. Not fixed.
