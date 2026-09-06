"""Diagnostics support for AirPlay Notifier.

Nothing routed through this integration is sensitive on its own (no
credentials, no external accounts), but a spoken message could echo private
information, so `message`-shaped content is never stored anywhere this
integration can report — there is nothing to redact because nothing is
kept.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from . import AirplayNotifierConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AirplayNotifierConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    return {
        "data": dict(entry.data),
        "options": dict(entry.options),
        "resolved_options": asdict(entry.runtime_data.options),
        "legacy_service_name": entry.runtime_data.legacy_service_name,
    }
