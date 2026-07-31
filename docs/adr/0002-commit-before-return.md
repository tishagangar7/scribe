# ADR 0002 — A step's result is committed before it is returned

**Status:** Accepted · **Date:** 2026-08-03

## Context

`ctx.step` executes a function and records the result. The ordering of "return
to caller" versus "commit to disk" is not arbitrary.

If we returned first and committed asynchronously, there is a window where the
workflow has the value in memory but the log does not. A crash inside that
window loses a completed, *already paid for* step. On resume it re-executes —
which is precisely the failure this project exists to prevent.

## Decision

`append_event` commits synchronously, and `ctx.step` does not return until the
commit has landed.

## Consequences

**Good**
- The invariant holds without qualification: if the workflow saw a result, that result is durable.

**Bad**
- One fsync per step. At ~20–60 steps per run against steps that each take seconds of network I/O, this is noise — well under 1% of wall clock. Revisit only if step latency ever drops to the millisecond range, which for LLM workloads it will not.
