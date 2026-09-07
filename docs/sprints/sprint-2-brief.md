# Sprint 2 brief — AirPlay Notifier v0.2.0 (quality-scale todos, quiet hours, reconfiguration)

> One coding agent, branch `feat/0.2.0`, in its own worktree. English. Tests
> first for every behavioural item. Paths from the orchestrator: core clone
> 2026.9.1 at `$HA_CORE_SRC`, venv at `$VENV`. Never touch
> `.github/workflows` or any Home Assistant instance.

## Scope (must)

1. **`test-before-configure` / `test-before-setup`** (`quality_scale.yaml`
   todos): the config flow refuses a media_player that lacks `PLAY_MEDIA`
   (and, for the Music Assistant strategy, a player that is not an MA
   player) with a form error; `async_setup_entry` raises
   `ConfigEntryNotReady` when the target media_player or the TTS entity is
   missing from the state machine, so HA retries instead of loading a dead
   entry.
2. **`entity-unavailable` / `log-when-unavailable`**: the notify entity is
   `unavailable` while its media_player or TTS entity is unavailable/missing,
   and logs once when that happens and once when it recovers (core pattern:
   log on transition only).
3. **`reconfiguration-flow`**: `async_step_reconfigure` lets the user change
   the media_player and the TTS entity of an existing entry (the options flow
   still handles the rest); unique id rules unchanged.
4. **`icon-translations`**: `icons.json` for the notify entity.
5. **Quiet hours** — same semantics as Cast Notifier 0.2.0, word for word:
   options `quiet_start` / `quiet_end` (`TimeSelector`, both empty =
   disabled, may cross midnight) and optional `quiet_volume`; inside the
   window speak at `quiet_volume` if set, otherwise refuse with a translated
   `ServiceValidationError` (reason `quiet_hours`, logged at INFO);
   `data.priority: "critical"` bypasses; per-call `data.volume` wins.
6. **Service name from the entry title** — verify the current derivation;
   if it comes from the entity id like Cast 0.1.x did, switch to
   `notify.airplay_<slugify(title)>` with deterministic `_<n>` fallback and
   document the upgrade note; if it already uses the title, add a test
   pinning it.
7. **Docs todos**: `docs-removal-instructions` and `docs-troubleshooting`
   sections in `README.md`; `docs/known-issues.md` refreshed; every silver
   and gold rule assessed in `quality_scale.yaml`; `CHANGELOG.md`.

## Out of scope (must not)

Chimes / pre-announce media, changing the two strategies' volume semantics
(known issue on `volume: 0`), the `NotifyEntity` `data` limitation, external
URL of the TTS clip (stays documented).

## Definition of done

Tests first and green; coverage stays ≥ 95 %; ruff / mypy / hassfest green;
`strings.json` + en/fr/es complete (reconfigure step, options, exception
`quiet_hours`); conventional commits; PR opened, not merged, not tagged; PR
description cites every core API with its path in `$HA_CORE_SRC`.
