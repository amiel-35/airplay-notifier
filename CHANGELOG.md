# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-09-07

### Added

- **Quiet hours**: `quiet_start` / `quiet_end` options (leave both empty to
  disable; an end earlier than the start crosses midnight) and an optional
  `quiet_volume`. Inside the window an announcement is spoken at
  `quiet_volume`, or refused with a translated `ServiceValidationError`
  logged at `INFO` when none is set. `data.priority: critical` bypasses the
  window; `data.volume` wins over `quiet_volume` but never turns a refusal
  into an announcement.
- **Reconfiguration flow**: the target `media_player` and the TTS engine can
  be changed on an existing entry, keeping its options *and* its title — so
  `notify.airplay_<name>`, and any `alert.notifiers:` pointing at it, keeps
  working and now speaks on the new player.
- **Availability**: the notify entity follows its `media_player` and TTS
  entity and reports itself `unavailable` while either is missing or
  unavailable, logging one line per transition in each direction.
- **Setup deferral**: an entry whose `media_player` or TTS entity is absent
  from the state machine raises `ConfigEntryNotReady` and is retried,
  instead of loading and failing at every announcement.
- **Suitability checks in the forms**: a `media_player` that does not
  support `play_media` is refused at setup and at reconfigure, and the
  Music Assistant strategy is refused for a player another integration
  provides. Both stay permissive when the information is absent.
- `icons.json`: the notify entity has its own icon.
- README sections for removal and troubleshooting (a symptom-to-cause
  table, the debug logger snippet, diagnostics).

### Changed

- The legacy notify service name still follows the entry title, but two
  entries whose titles slugify identically are now numbered
  (`airplay_bedroom`, `airplay_bedroom_2`) instead of the second silently
  having no service at all.
- Entry schema `MINOR_VERSION` 1 → 2 for the quiet-hours option keys.
  Nothing stored is rewritten; a downgrade to 0.1.x now refuses the entry
  rather than silently ignoring its quiet hours.
- `data.priority` is accepted by the legacy service and validated against
  `normal` / `critical`.

### Upgrade note

Nothing to do for a single entry. If you run **two entries whose titles are
the same** (two speakers both called "Bedroom", say), the second one now
gets its own `notify.airplay_<name>_2` service where before it had none;
check which name your `alert.notifiers:` refers to in Developer tools →
Actions. Downgrading to 0.1.x after this release leaves entries that 0.1.x
will refuse to load.

## [0.1.0] - 2026-09-07

### Added

- Repository scaffold: `custom_components/airplay_notifier`, CI (hassfest,
  HACS validation, lint, tests, release), HACS metadata.
- Config flow (media player + TTS engine, one instance per player) and
  options flow (language, voice, volume, restore behavior, strategy
  override, announce prefix, deny-list).
- Legacy `notify.airplay_<name>` service and a modern `NotifyEntity`, both
  speaking through the same delivery logic, both resolving their options
  from the live config entry on every call.
- Two delivery strategies: **Music Assistant**
  (`music_assistant.play_announcement`, with the TTS message resolved to a
  real URL first — see `docs/ARCHITECTURE.md` for why) and **Direct**
  (`tts.speak` targeting an `apple_tv` or other `play_media`-capable
  player), auto-selected from the target's entity registry platform or
  forced via the `strategy` option. The Music Assistant path falls back to
  Direct, with a warning, when Music Assistant is not loaded.
- Deny-list: a call whose `data.source_entity` belongs to a denied domain
  (`alarm_control_panel`, `lock` by default) is refused with a translated
  `ServiceValidationError` and logged, never spoken. `source_entity` is
  normalised (list, tuple, set, case, whitespace) before the check, and a
  value that is not a usable `domain.object_id` is refused rather than
  ignored.
- Per-call `data` overrides — `volume`, `language`, `voice`, `tts_entity` —
  validated by a voluptuous schema that also rejects unknown keys.
- Volume set/restore around Direct-strategy announcements: the original
  volume is remembered once per burst of overlapping announcements, exactly
  one restore is ever armed, it is armed even when speaking fails, it runs
  inside the same lock the announcements take (so a call starting during an
  in-flight restore cannot mistake announcement volume for the original),
  and unloading or reloading the config entry performs it immediately
  rather than dropping it.
- One service device per config entry, holding a single `notify.<player>`
  entity.
- Diagnostics (redaction plumbing with a documented empty `TO_REDACT`),
  `async_migrate_entry` (refusing both major and minor downgrades),
  translations (`en`, `fr`, `es`), a `quality_scale.yaml` self-assessment
  and `docs/known-issues.md`.
- Tests for the config flow, both notify surfaces, the delivery strategies
  and their failure modes, the real TTS URL resolution, diagnostics, the
  manifest, translation key parity, and hassfest's translation *value*
  rules (no placeholder inside single quotes, valid placeholder
  identifiers, no stray braces).

[Unreleased]: https://github.com/amiel-35/airplay-notifier/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/amiel-35/airplay-notifier/releases/tag/v0.2.0
[0.1.0]: https://github.com/amiel-35/airplay-notifier/releases/tag/v0.1.0
