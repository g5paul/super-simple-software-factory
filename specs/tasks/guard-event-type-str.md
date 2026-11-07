# Skip an event whose type isn't a string

Guard run ea6e82a9 (build 2f1fa40) added an event-type filter to
`agents._event_forwarder`: `event.get("type") in RULE_EVENT_TYPES[harness]`.
Its review (`adws/adw_data/sessions/ea6e82a9/context_handoff/guard_report.md`,
broken promise 1) found that an event whose `type` is a list or dict, such
as `{"type": ["user"]}`, which an agent can write to its harness's stdout,
raises `TypeError` from the frozenset lookup. The spec promised such an
event skips the rule reads and still reaches the call tracker.

## What should change

The filter treats a `type` that isn't a `str` as not one of the harness's
types: the rule reads skip it, and it goes on to the call tracker as any
other skipped event does. Nothing else changes.

## Tests

- On each harness, an event with `type` a list, a dict and a number skips
  the rule reads and raises nothing.
- Every existing test passes unchanged.

## Out of scope

Everything else about the forwarder and the filter.
