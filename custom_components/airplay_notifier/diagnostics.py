"""Diagnostics support for AirPlay Notifier.

Nothing routed through this integration is a secret on its own: there are
no credentials, tokens or external accounts anywhere in a config entry, and
a spoken message is never stored, so a diagnostics download cannot leak
one — there is nothing kept to leak.

`TO_REDACT` is therefore intentionally empty, and it is applied anyway
through `homeassistant.components.diagnostics.async_redact_data`. Keeping
the plumbing in place means the day a sensitive key does appear (an API key
for a cloud TTS engine, say) redacting it is a one-line change to this set
rather than a change of shape, and the emptiness is a documented decision
instead of an omission a reader has to infer.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import AirplayNotifierConfigEntry, AirplayNotifierRuntimeData

TO_REDACT: set[str] = set()


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AirplayNotifierConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    `runtime_data` is absent while the entry is not loaded (core deletes the
    attribute on unload) — and a failed-to-load entry is exactly when
    someone downloads diagnostics, so the resolved settings are reported as
    unavailable instead of raising `AttributeError` and producing no
    diagnostics at all.
    """
    runtime_data: AirplayNotifierRuntimeData | None = getattr(
        entry, "runtime_data", None
    )

    diagnostics: dict[str, Any] = {
        "entry": {
            "version": entry.version,
            "minor_version": entry.minor_version,
            "state": str(entry.state),
            "title": entry.title,
        },
        "data": dict(entry.data),
        "options": dict(entry.options),
        "loaded": runtime_data is not None,
        "resolved_options": (
            asdict(runtime_data.options) if runtime_data is not None else None
        ),
        "legacy_service_name": (
            runtime_data.legacy_service_name if runtime_data is not None else None
        ),
    }
    return async_redact_data(diagnostics, TO_REDACT)
