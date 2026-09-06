# AirPlay Notifier

A Home Assistant `notify.*` that **speaks** on AirPlay players — HomePods,
Apple TVs, AirPlay speakers — whether or not they are managed by
[Music Assistant](https://www.music-assistant.io/).

## Why

Home Assistant has no native notifier that talks. Text-to-speech is
`tts.speak`, an *action* you target at a media player — not a `notify.*`
service. That means the core `alert` integration, and most blueprints, which
only accept `notify.*` service names under `notifiers:`, cannot make an
alert speak. AirPlay Notifier wraps `tts.speak` (and, for Music Assistant
players, an equivalent announcement path) behind an ordinary notifier, so
one line in `alert.notifiers:` is enough to have an alert announced out
loud.

One config entry = one player = one legacy service
(`notify.airplay_<name>`, usable in `alert.notifiers:`) plus one modern
`NotifyEntity` (`notify.send_message`), for new automations. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for exactly which Home
Assistant APIs are used, and why, with file references into
`home-assistant-core`.

## Install (HACS, custom repository)

1. HACS → the three-dot menu (top right) → **Custom repositories**.
2. Repository: `https://github.com/amiel-35/airplay-notifier`, category
   **Integration**.
3. Install **AirPlay Notifier**, restart Home Assistant.
4. Settings → Devices & services → **Add integration** → AirPlay Notifier.

## Configuration

Setup asks for two things, fixed for the life of the entry:

- **Media player**: the AirPlay/HomePod/Apple TV target.
- **Text-to-speech engine**: the `tts.*` entity to speak with.

Everything else is tunable afterward from the entry's **Configure** option:

| Option | Default | Meaning |
|---|---|---|
| Language | engine default | Language code passed to the TTS engine. |
| Voice | engine default | Engine-specific voice identifier. |
| Volume | `0.6` | 0 (silent) to 1 (maximum). |
| Restore volume after speaking | on | Direct strategy only — Music Assistant restores its own announcement volume. |
| Delivery strategy | Auto | Auto detects Music Assistant players; Music Assistant / Direct force a path. |
| Announcement prefix | *(none)* | Spoken before every message, e.g. a chime word. |
| Denied source domains | `alarm_control_panel, lock` | A call whose `data.source_entity` is in one of these domains is refused and logged — never spoken. |

Per-call `data` overrides (legacy `notify.airplay_<name>` service only —
see below): `volume`, `language`, `voice`, `tts_entity`.

## Examples

### An `alert` that speaks

```yaml
alert:
  dishwasher_finished:
    name: Dishwasher finished
    entity_id: binary_sensor.dishwasher_done
    state: "on"
    notifiers:
      - airplay_kitchen_homepod
```

### A "dishwasher finished" automation using the entity

```yaml
automation:
  - alias: Announce dishwasher finished
    trigger:
      - trigger: state
        entity_id: binary_sensor.dishwasher_done
        to: "on"
    action:
      - action: notify.send_message
        target:
          entity_id: notify.living_room_speak
        data:
          message: The dishwasher is finished.
```

### The deny-list in practice

```yaml
automation:
  - alias: Do not let a careless automation announce the alarm state
    trigger:
      - trigger: state
        entity_id: alarm_control_panel.home
    action:
      - action: notify.airplay_living_room
        data:
          message: "{{ trigger.to_state.state }}"
          data:
            source_entity: alarm_control_panel.home
```

This call is refused and logged (`custom_components.airplay_notifier:
debug` in `logger:` shows why) instead of announcing the alarm's state —
`alarm_control_panel` is in `deny_domains` by default.

Note: `data.source_entity` (and the other per-call overrides) only work
through the legacy `notify.airplay_<name>` service. Home Assistant's modern
`notify.send_message` action has a fixed `message`/`title` schema with no
generic `data` field, so calls made through a `notify.*_speak` entity
always use the entry's configured defaults — see
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Delivery strategies

- **Auto** (default): if the target `media_player` belongs to the
  `music_assistant` integration (checked via the entity registry), use the
  Music Assistant strategy; otherwise, Direct.
- **Music Assistant**: resolves the message to a real TTS URL, then calls
  `music_assistant.play_announcement`.
- **Direct**: calls `tts.speak` with `media_player_entity_id` set to the
  target player (works with `apple_tv` media players that support
  `play_media`).

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the verified details
of both paths — in particular, why the Music Assistant strategy needs to
resolve the TTS URL itself rather than handing Music Assistant a
`media-source://` identifier.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements_dev.txt

ruff format --check .
ruff check .
mypy custom_components/airplay_notifier
pytest --cov=custom_components.airplay_notifier
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

MIT © 2026 Amiel Lavon. See [`LICENSE`](LICENSE).
