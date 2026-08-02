# ADR 0004 — Divergence halts the run; it is never silently tolerated

**Status:** Accepted · **Date:** 2026-08-03

## Context

Replay works by re-invoking the workflow function from line one and serving
each `ctx.step` call from the log instead of executing it, provided the
`step_id` requested matches what the log expects next (`Context._check_divergence`).
Sometimes it won't match: workflow source changed while a run was in flight,
a conditional branched on data read outside a step, or a step read real
nondeterministic state (`datetime.now`, `random`, `uuid4`) instead of going
through `ctx.now`/`ctx.random`/`ctx.uuid`.

When that happens, there are two options. Either halt immediately
(`DivergenceError`), or let the workflow continue, appending new events after
whatever prefix of the log it still agrees with.

Continuing looks appealing — the run makes progress instead of failing outright.
But the log's meaning depends on it describing one coherent execution. A replay
that diverges and keeps going produces a log whose early events came from the
original code path and whose later events came from a different one, spliced
together as if they were a single run. Nothing downstream — the run's `result`,
its event count, a human reading the log — can tell the difference between that
and a normal successful run. It is not a crash; it is a wrong answer that looks
right.

## Decision

Any mismatch between the requested `step_id` and the log's expected step at
that position raises `DivergenceError` immediately, naming both the expected
and actual step IDs, and executes nothing further. The run is left in
`FAILED` — resumable only in the sense that fixing the root cause (usually:
route the offending call through `ctx.now`/`ctx.random`/`ctx.uuid`, or don't
deploy workflow-source changes over in-flight runs) and re-running from the
same `run_id` will retry cleanly, because no divergent events were ever
written.

This is why `ctx.now`/`ctx.random`/`ctx.uuid` (ADR-adjacent to DA-111) and the
static lint over `@workflow` bodies (DA-116) both exist: they are the ways to
avoid ever reaching this error, not alternatives to it.

## Consequences

**Good**
- A divergent run fails loudly, at the exact point of disagreement, with
  enough detail (expected vs. actual step ID) to diagnose it immediately.
- The log's invariant — "every event in this log came from one execution of
  the current workflow code" — never has an exception. Anything reading the
  log can trust it without first checking whether it happens to be one of the
  divergent ones.

**Bad**
- A run that hits this needs a human (or an automated redeploy-and-retry) to
  resume it; it does not self-heal by continuing partially. For this
  project's target workloads (agent runs costing real LLM tokens, not
  latency-critical request paths), failing loud and re-running from a clean
  `run_id` is cheaper than debugging a plausible-looking wrong answer later.
