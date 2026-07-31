# ADR 0003 — `scribe/` never imports from `agent/`

**Status:** Accepted · **Date:** 2026-08-03

## Context

The agent and the runtime are co-dependent at the *requirements* level: the
agent's need for parallel candidate exploration is why `fork` exists, and the
agent's search strategy is only possible because `fork` exists.

There is a real risk of letting that conceptual co-dependence leak into the
import graph, producing a tangle that cannot be reasoned about or reused.

## Decision

Strict one-way layering. `agent/`, `api/`, and `eval/` may import `scribe/`.
`scribe/` may import none of them. Enforced mechanically by a ruff
`flake8-tidy-imports` banned-api rule in `pyproject.toml`, so violations fail CI
rather than relying on discipline.

## Consequences

**Good**
- The runtime is independently testable and independently usable. Its test suite has no LLM dependency and costs nothing to run.
- Co-dependence expresses itself where it belongs: in the data (the agent consumes runtime artifacts) and in the requirements (the agent's needs drive runtime features).

**Bad**
- Occasionally a runtime feature must be designed slightly more generally than the single known caller requires. This is a feature, not a cost.
