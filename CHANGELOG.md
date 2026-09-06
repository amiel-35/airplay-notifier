# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/amiel-35/airplay-notifier/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/amiel-35/airplay-notifier/releases/tag/v0.1.0
