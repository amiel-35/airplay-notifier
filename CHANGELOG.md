# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Repository scaffold: `custom_components/airplay_notifier`, CI (hassfest,
  HACS validation, lint, tests, release), HACS metadata.
- Config flow (media player + TTS engine, one instance per player) and
  options flow (language, voice, volume, restore behavior, strategy
  override, announce prefix, deny-list).
- Legacy `notify.airplay_<name>` service and a modern `NotifyEntity`, both
  speaking through the same delivery logic.
- Two delivery strategies: **Music Assistant**
  (`music_assistant.play_announcement`, with the TTS message resolved to a
  real URL first — see `docs/ARCHITECTURE.md` for why) and **Direct**
  (`tts.speak` targeting an `apple_tv` or other `play_media`-capable
  player), auto-selected from the target's entity registry platform or
  forced via the `strategy` option.
- Deny-list: a call whose `data.source_entity` belongs to a denied domain
  (`alarm_control_panel`, `lock` by default) is refused and logged, never
  spoken.
- Per-call `data` overrides: `volume`, `language`, `voice`, `tts_entity`.
- Volume set/restore around Direct-strategy announcements.
- Diagnostics, translations (`en`, `fr`, `es`), and tests for the config
  flow, the notify platform, the delivery strategies, and translation key
  parity.
