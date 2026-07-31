# PRD — Durable Execution Runtime for Multi-Agent LLM Systems

**Codename:** `durable-agents`
**Author:** Tisha Gangar
**Status:** Draft v1
**Date:** July 29, 2026
**Target ship date:** September 9, 2026 (6 weeks)

---

## 1. One-line summary

A durable execution runtime for LLM agents: agent runs survive process crashes and resume mid-execution, and recorded runs replay deterministically for zero-cost regression testing. Demonstrated on a multi-agent automated code-repair workload evaluated against SWE-bench-lite.

---

## 2. Problem statement

LLM agents are long-running, expensive, and nondeterministic. Three consequences:

**2.1 Crashes destroy all progress.**
A typical agent run is 20–60 steps, several minutes of wall-clock time, and $0.50–$3.00 in API spend. Agent state lives in Python variables — local process memory. When the process dies (rate limit, network timeout, OOM, container restart, deploy), every completed step is lost. Restarting means re-paying and re-waiting for work that was already done correctly.

**2.2 Agents cannot be tested.**
Because model output is nondeterministic, the same input produces different behavior on each run. There is no way to write an assertion that holds. Consequently there is no regression testing: a developer changes a prompt and has no mechanism to discover what broke.

**2.3 Failure paths are untestable.**
Recovery logic (retries, fallbacks, partial-failure handling) is the least-exercised and most bug-prone code in an agent system, because the failures that trigger it are hard to reproduce on demand.

This project addresses all three with a single mechanism: an append-only event log with replay-based resumption.

---

## 3. Goals

**G1.** A run that is killed at any point resumes from the exact step of failure, with zero re-execution of completed steps and zero re-spent tokens.

**G2.** Any recorded run can be replayed deterministically — identical step sequence, identical outputs — offline and at $0 cost.

**G3.** Replay-based testing is exposed as a usable primitive: record real runs, replay them as a regression suite, and inject faults at arbitrary steps.

**G4.** A run can be forked from an arbitrary step into N children that share the parent's prefix without re-execution.

**G5.** A multi-agent code-repair workload runs entirely on the runtime and produces a measured resolution rate on SWE-bench-lite.

**G6.** The system runs distributed on AWS with multiple workers, defined entirely in Terraform.

---

## 4. Non-goals

**N1. Competing with Temporal, DBOS, Restate, or Inngest.** These are mature, funded products. This is a from-scratch implementation built to understand the problem class, with agent-specific features they do not prioritize.

**N2. State-of-the-art SWE-bench performance.** Funded labs exceed 50% on SWE-bench-lite with extensive scaffolding. Expected result here is in the low-to-mid teens. The benchmark is a *measurement instrument for the runtime*, not the headline result.

**N3. Production readiness.** No multi-tenancy, no auth, no SLA, no horizontal scale testing beyond a handful of workers.

**N4. A general agent framework.** LangGraph is used as-is. This project is the layer underneath it, not a replacement for it.

**N5. Users.** This is a portfolio and learning artifact. No user acquisition, no hosting for others.

---

## 5. Positioning and prior art

| System | What it does | Relationship to this project |
|---|---|---|
| **Temporal** | Industry-standard durable execution; deterministic replay, versioning, workers | The reference implementation. Read its determinism constraints before Week 2. |
| **DBOS** | Durable execution on Postgres | Closest architectural analogue to the storage layer here. |
| **Restate / Inngest / Trigger.dev** | Durable workflow execution for application code | Same problem class, general-purpose framing. |
| **LangGraph Platform** | LangChain's own persistence/durability layer | Direct overlap on the agent angle. |

**Original contribution (small but real):** the agent-specific features. Token and dollar budget enforcement at step boundaries; replay-with-recorded-model-responses as a first-class *testing* primitive rather than only a recovery mechanism; and fork-from-checkpoint used for parallel candidate exploration by the agent itself.

**Honest framing for the README:** "Built from scratch to understand how systems like Temporal work, extended with agent-specific primitives, and validated on a real workload."

---

## 6. Core concepts

| Term | Definition |
|---|---|
| **Run** | One execution of a workflow. Has a stable `run_id`. |
| **Workflow** | A user-written async Python function that receives a `Context` and performs steps. |
| **Step** | A single side-effecting operation (LLM call, tool call, sandbox execution) wrapped in `ctx.step(step_id, fn)`. |
| **Event** | An immutable row in the log recording that a step started, completed, or failed. |
| **Event log** | Append-only, per-run ordered sequence of events. The single source of truth for run state. |
| **Replay** | Re-executing a workflow from line one, where steps present in the log return cached results instead of executing. |
| **Determinism** | The property that a workflow, replayed against the same log, requests exactly the same step IDs in the same order. |
| **Divergence** | A replay requesting a step ID that does not match the log. Always an error, never silently tolerated. |
| **Fork** | Creating a child run that inherits the parent's log up to a chosen sequence number, then proceeds independently. |
| **Lease** | A time-bounded claim by a worker on a run, so exactly one worker executes a run at a time. |

---

## 7. Architecture

```
                        ┌──────────────────────────────┐
                        │   FastAPI control plane      │
                        │  submit / status / trace     │
                        │  fork / cancel               │
                        └──────────────┬───────────────┘
                                       │
                        ┌──────────────▼───────────────┐
                        │        EVENT LOG             │
                        │  SQLite (dev) / Postgres     │
                        │  append-only, per-run        │
                        └──────────────┬───────────────┘
                                       │  lease (SKIP LOCKED)
              ┌────────────────────────┼────────────────────────┐
              │                        │                        │
        ┌─────▼─────┐            ┌─────▼─────┐            ┌─────▼─────┐
        │ Worker 1  │            │ Worker 2  │            │ Worker 3  │
        │           │            │           │            │           │
        │ replay →  │            │           │            │           │
        │ execute → │            │           │            │           │
        │ commit    │            │           │            │           │
        └─────┬─────┘            └───────────┘            └───────────┘
              │
              │ runs the workflow, which is:
              │
        ┌─────▼──────────────────────────────────────────────┐
        │  AGENT (LangGraph)  — every node is a ctx.step      │
        │                                                     │
        │   Retriever ──► Planner ──► Coder ──► Critic        │
        │       ▲                                  │          │
        │       └────────── retry loop ◄───────────┘          │
        │                                                     │
        │   tools: tree-sitter chunking, BM25 + dense search, │
        │          Docker sandbox test execution              │
        └─────────────────────────────────────────────────────┘
```

**Strict layering rule:** `agent/` imports `runtime/`. `runtime/` **never** imports `agent/`. Enforced by lint rule. Co-dependence exists at the data and requirements level (the agent's needs drive runtime features; the agent consumes runtime artifacts), never at the import level.

---

## 8. Data model

```sql
-- One row per run.
CREATE TABLE runs (
    run_id           TEXT PRIMARY KEY,
    workflow_name    TEXT NOT NULL,
    workflow_version INTEGER NOT NULL,
    input            JSONB NOT NULL,
    status           TEXT NOT NULL,        -- pending|running|completed|failed|cancelled|budget_exceeded
    result           JSONB,
    error            TEXT,

    -- lineage (fork). Present from day one — expensive to retrofit.
    parent_run_id    TEXT REFERENCES runs(run_id),
    forked_at_seq    INTEGER,

    -- budget enforcement
    token_budget     INTEGER,
    tokens_used      INTEGER NOT NULL DEFAULT 0,
    cost_budget_usd  NUMERIC(10,4),
    cost_used_usd    NUMERIC(10,4) NOT NULL DEFAULT 0,

    -- leasing
    leased_by        TEXT,
    lease_expires_at TIMESTAMPTZ,

    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only. Never UPDATE, never DELETE.
CREATE TABLE events (
    run_id      TEXT NOT NULL REFERENCES runs(run_id),
    seq         INTEGER NOT NULL,          -- monotonic within a run
    step_id     TEXT NOT NULL,             -- workflow-assigned, stable across replay
    event_type  TEXT NOT NULL,             -- step_started|step_completed|step_failed|run_started|run_completed
    payload     JSONB,                     -- result, or error detail
    tokens      INTEGER,
    cost_usd    NUMERIC(10,4),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (run_id, seq)
);

-- Fast lookup during replay.
CREATE UNIQUE INDEX events_step_lookup
    ON events (run_id, step_id)
    WHERE event_type = 'step_completed';

-- Worker polling.
CREATE INDEX runs_claimable
    ON runs (status, lease_expires_at)
    WHERE status IN ('pending', 'running');
```

**Invariants**
- `events` is append-only. Corrections are new events, never mutations.
- A step's result is written to the log and committed **before** `ctx.step` returns it.
- `(run_id, step_id)` is unique among completed steps.
- A run is executed by at most one worker at a time, enforced by lease.

---

## 9. Functional requirements

### 9.1 Event store — `runtime/store.py`

- **F1.1** Append an event atomically and durably; the write commits before the caller proceeds.
- **F1.2** Look up a completed step by `(run_id, step_id)` in O(1).
- **F1.3** Read a full run log in `seq` order.
- **F1.4** Claim a run via lease: atomically select a claimable run and set `leased_by` / `lease_expires_at`. Postgres uses `SELECT ... FOR UPDATE SKIP LOCKED`.
- **F1.5** Reclaim runs whose lease has expired (worker died) and return them to the claimable pool.
- **F1.6** Two backends behind one interface: SQLite (dev) and Postgres (prod).

### 9.2 Execution context — `runtime/context.py`

- **F2.1** `await ctx.step(step_id, fn)` returns the recorded result if `step_id` exists as a completed event; otherwise executes `fn`, records the result, and returns it.
- **F2.2** Step results must be JSON-serializable. Non-serializable returns raise immediately with a clear error.
- **F2.3** Step failures are recorded as `step_failed` events with the error type and message.
- **F2.4** Duplicate `step_id` within a single run raises `DuplicateStepError`.
- **F2.5** **Divergence detection:** during replay, if the workflow requests a step ID inconsistent with the log's recorded order, raise `DivergenceError` and halt. Never proceed on a divergent replay.
- **F2.6** `ctx` exposes read-only run metadata: `run_id`, `workflow_version`, `is_replaying`, remaining budget.

### 9.3 Determinism — `runtime/determinism.py`

- **F3.1** `ctx.now()` — current time as a recorded step.
- **F3.2** `ctx.random()`, `ctx.uuid()` — recorded steps.
- **F3.3** `ctx.gather(*steps)` — concurrent step execution with **recorded completion order**. Replay enforces the recorded order regardless of actual timing.
- **F3.4** A detector that flags direct use of `datetime.now`, `random`, `uuid`, and `os.environ` inside workflow modules (static check + optional runtime patching in test mode).
- **F3.5** Property-based determinism test: for randomly generated workflow shapes, record then replay, and assert identical step sequences. (Hypothesis.)

### 9.4 Worker — `runtime/worker.py`

- **F4.1** Loop: claim a run → load log → replay workflow → execute remaining steps → mark terminal status → release lease.
- **F4.2** Heartbeat: extend the lease while executing; a stalled worker's lease expires and the run is reclaimed.
- **F4.3** Graceful shutdown on SIGTERM: stop claiming new runs, finish or cleanly abandon the current one (the log makes abandonment safe).
- **F4.4** N workers run concurrently against one store with no double-execution.

### 9.5 Fork — `runtime/fork.py`

- **F5.1** `fork(run_id, at_seq, overrides)` creates a child run with `parent_run_id` and `forked_at_seq` set.
- **F5.2** The child resolves steps at or before `forked_at_seq` from the parent's log without copying rows (prefix sharing).
- **F5.3** Steps after the fork point are written to the child's own log only.
- **F5.4** Forking is recursive: a child may be forked.
- **F5.5** The API exposes a run's fork tree.

### 9.6 Budgets — `runtime/budget.py`

- **F6.1** Before executing any LLM step, check `tokens_used` and `cost_used_usd` against the run's caps.
- **F6.2** Exceeding a cap halts the run with status `budget_exceeded` and a **resumable** log — raising the cap allows resumption.
- **F6.3** Child runs draw from the parent's remaining budget; a parent's cap bounds its whole subtree.
- **F6.4** Cancelling a parent cancels all descendants. No orphaned runs continue spending.

### 9.7 Replay testing — `runtime/replay.py`

- **F7.1** Replay a recorded run against a stub LLM that serves recorded responses, with no network access.
- **F7.2** Assert reproduction: identical step sequence and identical final result.
- **F7.3** **Fault injection:** replay with a directive to raise a specified exception at a chosen step, to exercise recovery paths.
- **F7.4** A test helper that runs a directory of recorded logs as a regression suite and reports diffs.

### 9.8 Control plane — `api/`

- **F8.1** `POST /runs` submit a run.
- **F8.2** `GET /runs/{id}` status, budget usage, lineage.
- **F8.3** `GET /runs/{id}/trace` full event log.
- **F8.4** `POST /runs/{id}/fork` fork at a sequence number.
- **F8.5** `POST /runs/{id}/cancel` cancel run and descendants.
- **F8.6** Trace viewer (server-rendered): run list, event timeline, prompt/response inspection per step, fork tree.

---

## 10. The agent workload

### 10.1 Task

Given a repository at a specific commit, an issue description, and a failing test, produce a patch that makes the test pass without breaking others.

### 10.2 Graph

| Node | Responsibility | Notes |
|---|---|---|
| **Retriever** | Find code relevant to the issue | tree-sitter AST chunking (function/class granularity), hybrid BM25 + dense retrieval, reranked |
| **Planner** | Propose the change to make | Small model acceptable |
| **Coder** | Produce a unified diff | Strongest model; this is where quality lives |
| **Critic** | Review the patch before running tests | Cheap model; rejects obviously wrong patches before paying for sandbox execution |
| **Runner** | Apply patch, run tests in Docker | Deterministic tool step; memory cap and timeout |

Loop: patch → test → on failure feed the error back to Planner → retry. Bounded by step limit and budget cap.

Every node is a `ctx.step`. Every LLM call and tool call is recorded.

### 10.3 Fork usage (the co-dependence)

When the Coder produces multiple plausible candidate patches, the agent forks the run at the current step into N children, one per candidate. Each child inherits all prior retrieval and planning work at zero cost, applies its own patch, and runs tests. The first child whose tests pass wins; siblings are cancelled.

This is the design justification for F5: fork exists in the runtime **because the agent needs it**, and the agent's parallel search strategy is possible **because the runtime provides it**.

### 10.4 Sandbox

Docker container per test run. No network. Memory limit. Wall-clock timeout. Container destroyed after each run. Generated code is never executed on the host.

---

## 11. Evaluation

### 11.1 Runtime metrics (primary)

| Metric | Definition | Target |
|---|---|---|
| **Replay overhead** | Replay wall-clock ÷ direct execution wall-clock, excluding step execution | < 5% |
| **Recovery time** | `kill -9` → run resumed on another worker | < 10 s |
| **Tokens saved on resume** | Tokens that would have been re-spent without durability | Report mean and total |
| **Replay determinism** | Recorded runs reproduced with identical step sequence and result | 100 / 100 |
| **Throughput** | Runs completed per minute at N=1,2,4,8 workers | Report curve |
| **Fork prefix savings** | Tokens avoided by prefix sharing vs. independent runs | Report ratio |

### 11.2 Agent metrics (secondary)

| Metric | Notes |
|---|---|
| Resolution rate on SWE-bench-lite | Expected low-to-mid teens. Report honestly. |
| Cost per issue | USD mean |
| Steps per issue | Mean and p95 |

### 11.3 Ablations

Each reported as a delta on a fixed 50-issue development subset:

1. AST-aware chunking vs. fixed-size chunking
2. Hybrid retrieval vs. dense-only
3. With Critic vs. without
4. Fork-parallel candidates vs. sequential retry
5. Retry budget 1 vs. 3 vs. 5

Ablations are the single highest-signal artifact for a resume. They demonstrate experimental discipline, not just building.

---

## 12. Milestones

Exit criteria are binary. Do not advance without meeting them.

### Week 1 — Spine
Event store (SQLite), `ctx.step`, crash/resume on a linear workflow. Docker sandbox that runs a repo's tests. SWE-bench-lite loader.

**Exit:** A 10-step workflow is killed mid-run, restarted, resumes at the failed step, and completes with zero re-execution. One SWE-bench issue runs end-to-end with a naive single-prompt agent and a baseline score is recorded.

### Week 2 — Determinism
Time/random/uuid interception. Divergence detection. Deterministic concurrent ordering. Replay harness with stubbed LLM. Hypothesis property tests.

**Exit:** 20 recorded runs replay to identical results, offline, at $0, in seconds. A deliberately nondeterministic workflow is caught by `DivergenceError`.

### Week 3 — Multi-agent + fork
LangGraph graph (retriever, planner, coder, critic) on the runtime. Fork with prefix sharing. Budget enforcement. Cancellation propagation. tree-sitter retrieval.

**Exit:** Multi-agent version beats the Week 1 baseline. A run forks into 3 children that share the parent prefix without re-execution. Cancelling a parent cancels all children.

### Week 4 — Agent quality (HARD STOP)
Iterate on prompts and retrieval against the 50-issue subset. Run all five ablations. One full SWE-bench-lite run at the end.

**Exit:** A results table with numbers. **This week does not extend under any circumstances.** Agent tuning is unbounded work; the runtime is the deliverable.

### Week 5 — Distributed
Postgres store. Fargate workers. SQS. S3 artifacts. Secrets Manager. CloudWatch. Terraform for everything. GitHub Actions with OIDC running the replay suite on PRs.

**Exit:** 4 workers running in parallel on AWS. A Fargate task is killed mid-run; another worker picks it up and completes it. `terraform apply` from zero produces a working system.

### Week 6 — Ship
Fault-injection suite. Benchmark charts. Trace viewer. Architecture diagram. README. Writeup. Demo video.

**Exit:** A stranger can clone the repo, read the README, run `docker compose up`, and understand what it does and why in under five minutes.

### Weeks 7–8 — Applications
Small iterative commits. v2 features as time permits. Resume, LinkedIn, portfolio updated. Applications sent.

---

## 13. Tech stack

**Runtime:** Python 3.12 (asyncio), SQLite/`aiosqlite` → Postgres 16/`asyncpg`, raw SQL (no ORM), Alembic, Pydantic v2, pytest + pytest-asyncio + Hypothesis

**Agent:** LangGraph, Anthropic + OpenAI SDKs behind a thin self-written interface (this interface is the recording layer), py-tree-sitter, rank_bm25, sentence-transformers (`all-MiniLM-L6-v2`), numpy brute-force vector search, docker SDK, SWE-bench-lite via HuggingFace `datasets`

**Service:** FastAPI, Uvicorn, Jinja2 + HTMX (no React)

**Infra:** Docker, docker-compose, Terraform, AWS (ECR, ECS Fargate, ALB, RDS Postgres, SQS, S3, Secrets Manager, CloudWatch), GitHub Actions with OIDC

**Tooling:** uv, ruff, mypy (strict on `runtime/` only), pre-commit

**Explicitly excluded:** Kubernetes, Celery, Redis, Kafka, full LangChain, React/Next, FAISS, Poetry, ORMs, observability SaaS.

---

## 14. Repository layout

```
durable-agents/
├── runtime/                 # core. never imports from agent/
│   ├── store.py             # event log: append, read, lease, reclaim
│   ├── context.py           # ctx.step, replay, divergence detection
│   ├── determinism.py       # time/random/uuid/gather interception
│   ├── fork.py              # lineage, prefix sharing
│   ├── worker.py            # claim → replay → execute → commit
│   ├── budget.py            # token/cost caps, cancellation propagation
│   └── replay.py            # replay harness, fault injection
├── agent/                   # imports runtime. never the reverse
│   ├── graph.py             # LangGraph nodes
│   ├── retrieval.py         # tree-sitter chunking, hybrid search
│   ├── sandbox.py           # Docker test execution
│   └── llm.py               # provider interface + recording hooks
├── eval/
│   ├── swebench.py          # dataset loader, harness
│   ├── ablations.py
│   └── report.py            # charts
├── api/
│   ├── main.py              # FastAPI
│   └── templates/           # Jinja2 + HTMX trace viewer
├── infra/                   # terraform
├── tests/
│   ├── test_store.py
│   ├── test_determinism.py
│   ├── test_replay.py
│   ├── test_fork.py
│   ├── test_budget.py
│   └── test_crash_recovery.py
├── docs/
│   ├── ARCHITECTURE.md
│   ├── RESULTS.md
│   └── diagram.png
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

---

## 15. Budget

| Item | Estimate | Mitigation |
|---|---|---|
| AWS | $0–25 | $100–200 signup credits. No NAT Gateway. `terraform destroy` after every session. $10 budget alarm on day one. `db.t4g.micro`, smallest Fargate size. |
| LLM API | $0–40 | Ask Cara AI for credits. GitHub Student Developer Pack. Provider free tiers. Ollama + Qwen2.5-Coder-7B locally for Weeks 1–4 (patch quality is irrelevant while testing replay). Cheap model for Critic. 20-issue subset during development; full set once. **Replay makes all reruns free — this is the project's own cost mitigation.** |
| Everything else | $0 | All open source. GitHub Actions free tier. |

**Target total: under $40.**

---

## 16. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Week 4 agent tuning consumes Weeks 5–6 | **High** | Hard stop enforced. A shipped project at 14% beats an unshipped one at 19%. |
| Determinism bugs surface late and are subtle | High | Hypothesis property tests in Week 2, before the agent exists. Divergence detection fails loudly rather than silently. |
| Scope creep (this has already happened three times) | High | v2 backlog is fixed below. Nothing moves from v2 to v1. |
| API costs exceed budget | Medium | Local models for development. Hard budget caps are a product feature, used on self. |
| SWE-bench Docker environments are notoriously fiddly | Medium | Solve in Week 1 on a single issue before building anything on top of it. |
| Concurrent `ctx.gather` ordering is harder than expected | Medium | Ship sequential-only in Week 1; add concurrency in Week 2 as a distinct milestone. |
| AWS surprise bill | Low | Budget alarm, no NAT Gateway, destroy-after-session. |

---

## 17. Deliverables

**Code:** public GitHub repo, `docker compose up` runs the full local stack, `terraform apply` provisions AWS from zero.

**Documentation:**
- README: problem, mechanism, demo GIF, quickstart, results table, honest prior-art section
- `ARCHITECTURE.md`: the mechanism explained, determinism constraints, design tradeoffs, what was deliberately not built
- `RESULTS.md`: all metrics from §11 with charts, ablation table
- Architecture diagram

**Demo video (≤ 90 s):**
1. Start a 40-step run
2. `kill -9` the process at step ~30
3. Restart — it resumes mid-run and completes, with a counter showing tokens saved
4. Run the replay suite: same workflow, deterministic, 2 seconds, $0

**Writeup:** blog post or LinkedIn post with the diagram and the results table.

**Resume line:**
> *Durable execution runtime for multi-agent LLM systems (Python, LangGraph, AWS Fargate/SQS/RDS, Terraform): event-sourced workflows resume mid-run after process failure and replay deterministically for zero-cost regression testing. Demonstrated on automated code repair — X% of SWE-bench-lite resolved at $Y/issue; replay overhead under 5%.*

---

## 18. v2 backlog (do not build before shipping v1)

1. **Log as agent memory** — the Critic reads the run's own event log as context ("you already tried modifying `parse_config` at step 12; it failed with the same error")
2. **Auto-generated fault tests** — production failure logs automatically become fault-injection regression cases
3. **Self-hosting capstone** — run the bug-fixing agent on this repository's own test suite; every issue it resolves is both an eval data point and a real commit
4. Web-based fork-tree visualizer with diffing between siblings
5. Additional workload domains (SQL generation, data analysis) to show the runtime is workload-agnostic
6. Postgres `LISTEN/NOTIFY` instead of polling for run claims

---

## 19. Open questions

1. Should the LLM stub in replay match on step ID or on prompt hash? (Prompt hash is stricter and catches prompt drift — probably better, decide in Week 2.)
2. How are large step payloads handled — inline JSONB, or S3 with a pointer in the log? (Threshold-based, likely 256 KB.)
3. Should forked children write to a separate table or share `events` with lineage resolution at read time? (Sharing is simpler; measure read cost.)
4. Does the Critic need to be a separate LangGraph node, or can it be a step inside the Coder node? (Ablation 3 answers this empirically.)

---

## 20. Immediate next actions

- [ ] Sign up for GitHub Student Developer Pack (ucsc.edu email)
- [ ] Ask Sebastian at Cara AI about API credits for a personal learning project
- [ ] Create AWS account, Free Plan, MFA on root, IAM user, $10 budget alarm
- [ ] `mkdir durable-agents && git init`
- [ ] Read Temporal's documentation on determinism constraints and workflow versioning
- [ ] Build `store.py` and `context.py` with the Week 1 schema
- [ ] Write `test_crash_recovery.py` before writing the agent