# Known issues and limitations

Things this integration deliberately does not do, or cannot do, as of
0.1.0. Each one is a real constraint that was verified, not a guess. See
[`quality_scale.yaml`](../custom_components/airplay_notifier/quality_scale.yaml)
for the wider self-assessment.

## Per-call `data` only works through the legacy service

`data.volume`, `data.language`, `data.voice`, `data.tts_entity` and
`data.source_entity` are only reachable through
`notify.airplay_<name>`. Home Assistant's modern `notify.send_message`
action is registered with a fixed `message`/`title` schema
(`homeassistant/components/notify/__init__.py`,
`async_register_entity_service(SERVICE_SEND_MESSAGE, ...)`), so there is no
way to pass a generic `data` payload to a `NotifyEntity`. Calls made
through `notify.<player>` therefore always use the entry's configured
defaults, and the deny-list is never exercised on that path — it has no
`source_entity` to inspect.

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

## Availability is not tracked

The `notify` entity is always available, and setup does not verify that the
target `media_player` or the TTS entity still exist. A renamed or removed
target fails at the first announcement, with an error from the underlying
action, rather than showing up as unavailable or raising a repair issue.

## The target player and TTS engine cannot be changed

`media_player` and `tts_entity` are the entry's identity (the `unique_id`
and the source of the legacy service name), so there is no reconfigure
flow: pointing at a different player means deleting the entry and adding a
new one. The legacy service name follows the entry title, so renaming the
entry renames `notify.airplay_<name>` — and anything referencing the old
name in `alert.notifiers:` must be updated.

## Refusals fail the calling action

A deny-list refusal (or an invalid `data` payload) raises
`ServiceValidationError`, which fails the automation or script that called
it, in addition to being logged. That is deliberate — a silently ignored
announcement is worse than a visible failure — but it does mean an `alert`
whose `notifiers:` includes a refused call will report an error.

## Spanish translations are machine-translated

`translations/es.json` was not written by a Spanish speaker. Corrections are
welcome.
