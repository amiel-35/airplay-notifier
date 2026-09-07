# 0002 — `data.priority` is a closed set of four values

**Status:** Accepted, 0.2.0.
**Applies to:** `custom_components/airplay_notifier/const.py`
(`VALID_PRIORITIES`), `delivery.py` (`CALL_DATA_SCHEMA`,
`_quiet_hours_volume`).

## Context

Quiet hours need a way for an automation to say "this one matters enough to
wake the house". The obvious candidate is a per-call `priority`, and the
obvious implementation is a free string compared against `"critical"`.

A free string fails quietly, and this is the worst place for it to. The
value decides whether a smoke alarm speaks at 3am. `priority: Critical`,
`priority: urgent`, `priority: crticial` would each be accepted by a
schema that takes any string, then compare unequal, then behave exactly
like `normal` — a message the caller believed was a bypass, silently
demoted, at the one hour when nobody is watching the logs.

The set also has to be more than two values. An automation written against
a general notification vocabulary sends `info` and `high` as a matter of
course; refusing those would make a perfectly reasonable call fail for
saying something true about itself.

## Decision

`data.priority` accepts exactly `info`, `normal`, `high`, `critical` —
matched **exactly and in lower case**, through `vol.In(VALID_PRIORITIES)`
in `CALL_DATA_SCHEMA`. Anything else fails the call with
`invalid_call_data`. `Critical` is a typo like any other; there is no
case-folding, no aliasing and no default beyond "absent means normal".

**Only `critical` acts.** It is the sole value that bypasses quiet hours.
`info`, `normal` and `high` are accepted and then have no effect on
delivery whatsoever.

**`high` is deliberately not a bypass.** "Important" is not "wake the
house", and the two get conflated the moment a value in between is allowed
to do something. An automation that wants the 3am announcement has to say
`critical` and mean it.

## Consequences

- **A typo fails the call.** `priority: Critical` raises a
  `ServiceValidationError` carrying the voluptuous message, which fails the
  automation or script that made the call. That is the whole point — a
  visible failure at 3am beats a silent demotion — but it does mean an
  automation that was working on a lenient schema breaks loudly on upgrade
  rather than degrading.
- **Two of the four values are pure vocabulary.** `info` and `high` change
  nothing today. They are accepted so a caller is never refused for a value
  that is true, and they leave room for a later release to act on them
  without changing the accepted set.
- **The set is closed, so adding a value is a breaking-ish change in one
  direction only.** Widening it (a fifth value) never breaks a caller;
  narrowing it does. Any future narrowing needs a superseding record.
- **The modern `NotifyEntity` cannot use any of this.**
  `notify.send_message` is registered with a fixed `message`/`title` schema
  (`homeassistant/components/notify/__init__.py`), so a call on that
  surface can never mark itself `critical` and can never get through quiet
  hours. See [`known-issues.md`](../known-issues.md).

Aligned with the four-value vocabulary of Notify Switchboard, so an
automation written for one is not rewritten for the other; the reasons
above are what make it this integration's rule.
