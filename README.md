# Scribe

*It writes down what happened, so nothing has to happen twice.*

**Durable execution for LLM agents.** Agent runs survive process death and resume mid-execution without re-spending tokens. Recorded runs replay deterministically, which makes agents testable.

> ⚠️ Work in progress — Sprint 1 of 6. Runtime core is working; agent layer, fork, and the distributed backend are in progress.

---

## The problem

A typical agent run is 20–60 steps, several minutes, and $0.50–$3.00 in API spend. That progress lives in Python variables — process memory. When the process dies (rate limit, timeout, OOM, container restart, deploy), every completed step is lost. Restarting means re-paying for work that already succeeded.

Two more consequences follow from the same root cause:

- **Agents cannot be regression tested.** Model output is nondeterministic, so there is no assertion that holds. Change a prompt, and you have no way to discover what broke.
- **Recovery paths are the least-tested code in the system,** because the failures that trigger them are hard to reproduce on demand.

## The mechanism

Run state lives in an append-only event log, not in memory. Every side effect goes through `ctx.step`:

```python
from scribe.decorators import workflow


@workflow(name="research")
async def research(ctx, topic: str):
    plan = await ctx.step("plan", lambda: llm(f"plan research on {topic}"))

    findings = []
    for i, q in enumerate(plan["queries"]):
        r = await ctx.step(f"search:{i}", lambda q=q: web_search(q))
        s = await ctx.step(f"summarize:{i}", lambda r=r: llm(summarize(r)))
        findings.append(s)

    return await ctx.step("synthesize", lambda: llm(combine(findings)))
```

`ctx.step(step_id, fn)` does one thing:

1. Look up `step_id` in this run's log.
2. **Found** → return the recorded result. Do not execute `fn`.
3. **Not found** → execute `fn`, append the result to the log, commit, return.

That is the entire idea. Resumption is not a special code path — after a crash you re-invoke the same workflow function from line one, and completed steps return instantly from disk instead of hitting the network. Execution fast-forwards to the point of failure and continues.

## See it work

```bash
python demo.py            # crashes randomly, like a real rate limit
python demo.py            # run again — watch it resume
```

```
▶ resuming demo-run-1 — 12 steps already in the log

  ⏩ replay plan             0.00s      cached
  ⏩ replay search:0         0.00s      cached
  ⏩ replay summarize:0      0.00s      cached
  ⏩ replay search:1         0.00s      cached
  ⏩ replay summarize:1      0.00s      cached
  ⏩ replay search:2         0.00s      cached
  ⏩ replay summarize:2      0.00s      cached
  ⏩ replay search:3         0.00s      cached
  ⏩ replay summarize:3      0.00s      cached
  ⏩ replay search:4         0.00s      cached
  ⏩ replay summarize:4      0.00s      cached
  ⏩ replay search:5         0.00s      cached
  ▸  execute summarize:5      0.60s   1,240 tok  $0.031
  ▸  execute synthesize       0.60s   1,240 tok  $0.031

  ✓ completed in 1.2s
    14 steps total, 6 findings
    2 executed, 12 replayed
    saved 14,880 tokens ($0.37) and 7.2s by resuming
```

Each invocation is a separate OS process. The log is the only thing that persists.

## Quickstart

```bash
git clone https://github.com/<you>/scribe && cd scribe
uv sync --all-extras
uv run pytest          # 19 tests
uv run python demo.py
```

## Why this is hard

Replay only works if the workflow takes the **same code path every time**. If it doesn't, step IDs stop lining up with the log and you get silent corruption — plausible-looking, wrong results.

So every source of nondeterminism has to be intercepted and recorded: `datetime.now()`, `random`, `uuid4()`, environment reads, and — the difficult one — concurrent step completion order.

The runtime detects violations rather than tolerating them. If replay requests a step that doesn't match the recorded order, it raises `DivergenceError` and halts:

```
Divergence in run 'demo-run-1' at seq 1: log expected step 'b' but
workflow requested 'NEW'. The workflow took a different path than when it
was recorded. Check for nondeterminism (use ctx.now/ctx.random/ctx.uuid)
or a code change since this run started.
```

Halting loudly beats continuing quietly. See `docs/adr/`.

## What falls out for free

Once the log exists:

- **Deterministic tests** — the log records every model response, so replaying with a stub LLM makes agent behavior reproducible. 2 seconds, $0.
- **Fault injection** — replay a real run, force a timeout at step 12, assert recovery works.
- **Time-travel debugging** — inspect exact state at any step.
- **Fork** — copy a log to step N and continue differently. Used by the agent to explore multiple candidate patches in parallel, sharing the expensive prefix.

## Status

| Component | State |
|---|---|
| Event store (SQLite) | ✅ |
| `ctx.step` replay/execute | ✅ |
| Crash recovery | ✅ |
| Divergence detection | ✅ basic |
| Determinism interception | 🚧 Sprint 2 |
| Deterministic concurrency | 🚧 Sprint 2 |
| Replay test harness | 🚧 Sprint 2 |
| Fork | 🚧 Sprint 3 |
| Budgets & cancellation | 🚧 Sprint 3 |
| Multi-agent code repair | 🚧 Sprint 3 |
| Postgres + distributed workers | 🚧 Sprint 5 |
| AWS (Fargate, SQS, Terraform) | 🚧 Sprint 5 |

## Prior art

This problem is well-studied and this is not the first solution to it.

- **[Temporal](https://temporal.io)** — the industrial standard for durable execution. Deterministic replay, workflow versioning, worker pools. The reference implementation for this design.
- **[DBOS](https://dbos.dev)** — durable execution on Postgres; closest analogue to the storage layer here.
- **[Restate](https://restate.dev)**, **[Inngest](https://inngest.com)**, **[Trigger.dev](https://trigger.dev)** — same problem class, general-purpose framing.
- **LangGraph Platform** — LangChain's own persistence layer; direct overlap on the agent angle.

Built from scratch to understand how these systems work, extended with agent-specific primitives they don't prioritize: token/dollar budget enforcement at step boundaries, replay-with-recorded-responses as a *testing* primitive rather than only a recovery mechanism, and fork-from-checkpoint for parallel candidate exploration.

Not production-ready. No multi-tenancy, no auth, no scale testing.

## Layout

```
scribe/      core — never imports from agent/ (enforced by ruff, ADR 0003)
agent/       LangGraph code-repair agent
eval/        SWE-bench-lite harness and ablations
api/         FastAPI control plane + trace viewer
infra/       Terraform
docs/adr/    architecture decision records
```

## License

MIT
