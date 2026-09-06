# Security policy

AirPlay Notifier speaks messages on media players you already control; it makes no outbound network calls of its own beyond the TTS engine and media player entities you configured inside your own Home Assistant instance.

Security is never spoken by design: `deny_domains` defaults to `alarm_control_panel` and `lock`, and any call whose `data.source_entity` belongs to a denied domain is refused before anything is synthesized or played. If you find a way around that refusal, a way to make the integration speak on an unintended player, or any other vulnerability, please open a private security advisory on GitHub (Security → Report a vulnerability) rather than a public issue. You will get an answer within 14 days.

Supported versions: the latest minor release only.
