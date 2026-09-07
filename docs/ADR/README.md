# Architecture decision records

Short records of the decisions this integration has actually taken, in the
form they were taken: the context that forced the question, the rule that
came out of it, and what that rule costs.

They are **this repository's rules**. Where a record notes that another
project agrees, that is an alignment worth keeping, not an authority being
cited: nothing outside this repository decides what AirPlay Notifier does,
and a record changes only when a new one supersedes it.

`docs/ARCHITECTURE.md` describes how the integration works and stays the
place to look for mechanism. An ADR exists for the handful of choices
where the mechanism is obvious once you know the decision, and baffling
until you do.

| # | Decision | Status |
|---|---|---|
| [0001](0001-legacy-service-name-is-persisted.md) | The legacy notify service name is persisted on the entry | Accepted (0.2.0) |
| [0002](0002-priority-values.md) | `data.priority` is a closed set of four values | Accepted (0.2.0) |

## Writing a new one

Number it sequentially, keep the three headings (Context / Decision /
Consequences), and state the consequences that hurt as plainly as the ones
that help — a record that only lists benefits is an advertisement, not a
decision. Cite Home Assistant core by path and line, verified against the
version named at the top of `docs/ARCHITECTURE.md`.
