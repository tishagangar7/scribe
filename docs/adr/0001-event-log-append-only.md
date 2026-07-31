# ADR 0001 — The event log is append-only

**Status:** Accepted · **Date:** 2026-08-03

## Context

A run's progress must survive process death. The two candidate designs:

1. **Mutable checkpoint** — store the current state, overwrite it after each step.
2. **Append-only log** — store the sequence of things that happened; derive state by reading.

## Decision

Append-only log. `events` rows are never `UPDATE`d or `DELETE`d. Corrections are new events.

## Consequences

**Good**
- State is always reconstructible. A checkpoint that is corrupted mid-write loses everything; a log that fails mid-append loses only the last entry, and the prefix is still valid.
- Replay for *resumption* and replay for *testing* are the same mechanism. This is the reason deterministic testing falls out for free rather than being a second system.
- Time-travel debugging and fork are possible at all — both require the history, not just the current state.

**Bad**
- Storage grows with step count, not state size. Acceptable: a run is tens of steps, and large payloads move to S3 above a threshold.
- Reading the current state costs a scan. Mitigated by the `(run_id, step_id)` partial index on completed steps, which is the only lookup on the hot path.

## Related

`ADR 0002` (divergence), PRD §8.
