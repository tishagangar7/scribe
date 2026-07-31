# Execution Plan — `(this is the old name: durable-agents)`
# Execution Plan — `scribe`
**Owner:** Tisha Gangar
**Duration:** Week 0 (Jul 29 – Aug 2) + 6 sprints (Aug 3 – Sep 13, 2026)
**Companion doc:** `PRD.md`

---

## Part 0 — Operating model

### 0.1 Capacity — read this before anything else

This plan is built around a **hard budget of 18 hours/week**, because that is what actually remains after a 4 hr/day internship and 1 hr/day of DSA. Every ticket below is estimated in hours and every sprint sums to ≤ 18. If a sprint runs over, the overflow is cut, not absorbed by longer days.

```
Mon–Fri    Morning     4h   Altheros internship
           Afternoon   2.5h Project
           Evening     1h   LeetCode

Sat                    5.5h Project (the long block — hardest tickets go here)
Sun                    OFF  — non-negotiable
```

**5 × 2.5 + 5.5 = 18 hrs/week.**

Sunday off is a scheduling decision, not a reward. Six-week sprints fail in week 4 when there is no recovery day.

### 0.2 Sprint calendar

| Sprint | Dates | Theme | Demoable outcome |
|---|---|---|---|
| **0** | Jul 29 – Aug 2 | Setup | Repo scaffolded, accounts live, CI green on an empty test |
| **1** | Aug 3 – Aug 9 | Spine | Kill a run, resume it, zero re-execution |
| **2** | Aug 10 – Aug 16 | Determinism | 20 runs replay identically, offline, $0 |
| **3** | Aug 17 – Aug 23 | Agent + fork | Multi-agent beats baseline; run forks 3 ways |
| **4** | Aug 24 – Aug 30 | Quality (HARD STOP) | Results table with ablation deltas |
| **5** | Aug 31 – Sep 6 | Distributed | 4 Fargate workers; kill one, run survives |
| **6** | Sep 7 – Sep 13 | Ship | README, video, writeup, published |

### 0.3 Ceremonies (solo-adapted)

| When | Ritual | Duration |
|---|---|---|
| Mon start of block | **Sprint planning** — move that sprint's tickets to `Todo`, re-estimate, cut anything that doesn't fit 18h | 20 min |
| Daily, first 5 min | **Standup, written** — append to `docs/journal.md`: yesterday / today / blockers | 5 min |
| Daily, last 10 min | **Wrap** — commit, push, update ticket status, note tomorrow's first task | 10 min |
| Sat end of block | **Demo + retro** — record a 30s screen capture of the sprint's exit criterion; write 3 bullets in `docs/journal.md`: what worked, what didn't, what changes next sprint | 30 min |

The written standup and the weekly 30-second demo clips are not busywork — the clips become raw footage for the final video, and `journal.md` becomes the writeup.

### 0.4 Definition of Done

A ticket is Done only when **all** of these hold:

1. Code merged to `main` via PR
2. Tests written and passing (unit for logic, integration for anything touching the store)
3. `ruff check` and `ruff format` clean
4. `mypy --strict` clean on `runtime/` (agent code exempt)
5. Public functions have docstrings stating the contract
6. CI green
7. If it changes a design decision → an ADR added to `docs/adr/`

### 0.5 Conventions

**Branches:** `da-101-event-store-append`
**Commits:** Conventional Commits — `feat(store): add lease claim with SKIP LOCKED`
**PRs:** even solo, always PR into `main`. Self-review the diff before merging — it catches a surprising amount. Squash merge.
**Ticket IDs:** `DA-1xx` runtime · `DA-2xx` agent · `DA-3xx` eval · `DA-4xx` infra · `DA-5xx` docs

**Board:** GitHub Projects, columns `Backlog / Todo / In Progress / Review / Done`. WIP limit of 1 in `In Progress`.

**ADRs:** `docs/adr/0001-event-log-append-only.md` etc. Format: Context / Decision / Consequences. Write one whenever you make a choice you'd have to defend in an interview. These are worth real points with reviewers.

### 0.6 Estimation legend

`[S]` ≤ 1h · `[M]` 1–2.5h · `[L]` 2.5–5h

Anything larger than `[L]` is not a ticket, it's an epic — split it.

---

# Sprint 0 — Setup
**Jul 29 – Aug 2 · 6 hours total · Goal: zero friction on Aug 3**

Nothing here is technically interesting. All of it removes a blocker that would otherwise cost you a morning mid-sprint.

### Wed Jul 29 — Accounts (1h)

- **DA-001 [S]** GitHub Student Developer Pack — apply with `tgangar@ucsc.edu`. *Done: approval email or pending status confirmed.*
- **DA-002 [S]** Message Sebastian at Cara AI asking whether you can use company API credits for a personal learning project, or whether the team has unused free-tier allocation. *Done: message sent.*
- **DA-003 [S]** Anthropic + OpenAI accounts created, free credits claimed, keys stored in a password manager. *Done: `curl` to each API returns 200.*

### Thu Jul 30 — AWS (1.5h)

- **DA-004 [S]** AWS account, **Free Plan**. Root MFA enabled immediately.
- **DA-005 [S]** IAM user `tisha-dev` with `AdministratorAccess` + MFA. Access key created. Root logged out and never used again.
- **DA-006 [S]** Budgets: Zero-Spend Budget + a $10 budget with alerts at 50/80/100%. Free Tier alerts enabled in Billing preferences.
- **DA-007 [S]** Local CLI: `aws configure` with region `ap-south-1`. *Done: `aws sts get-caller-identity` returns your ARN.*

> Region `ap-south-1` (Mumbai) is chosen once and never changed. Cross-region resource confusion is the single most common beginner AWS time sink.

### Fri Jul 31 — Repo scaffold (2h)

- **DA-008 [M]** Initialise repo:
  ```bash
  mkdir durable-agents && cd durable-agents && git init
  uv init --python 3.12
  uv add pydantic aiosqlite pytest pytest-asyncio hypothesis
  uv add --dev ruff mypy pre-commit
  mkdir -p runtime agent eval api infra tests docs/adr
  touch runtime/__init__.py agent/__init__.py
  ```
- **DA-009 [S]** `pyproject.toml`: ruff config, mypy strict scoped to `runtime/`, pytest asyncio mode.
- **DA-010 [S]** `.pre-commit-config.yaml` with ruff + ruff-format. `pre-commit install`.
- **DA-011 [S]** `.gitignore`, `.env.example`, `README.md` with a one-paragraph placeholder.
- **DA-012 [S]** Import-boundary lint rule: ruff `flake8-tidy-imports` banning `agent`/`api`/`eval` imports inside `runtime/`. *This is the layering rule from the PRD, enforced mechanically.*

### Sat Aug 1 — CI + reading (1.5h)

- **DA-013 [S]** `.github/workflows/ci.yml`: on push/PR → `uv sync`, `ruff check`, `mypy runtime/`, `pytest`. Add a trivial passing test so it goes green.
- **DA-014 [S]** GitHub Project board created, columns set, all Sprint 1 tickets entered.
- **DA-015 [M]** **Read, don't code:** Temporal's docs on workflow determinism constraints and workflow versioning. Take notes into `docs/adr/0000-prior-art.md`. *Done: you can state in two sentences why Temporal forbids `datetime.now()` in workflow code.*

**Sprint 0 exit criteria**
- [ ] `aws sts get-caller-identity` works; $10 budget alarm exists
- [ ] `git push` triggers green CI
- [ ] `pre-commit` blocks a badly formatted commit
- [ ] Board populated with Sprint 1

---

# Sprint 1 — The Spine
**Aug 3 – Aug 9 · 18 hours · Goal: crash a run, resume it, lose nothing**

This is the most important sprint. Everything else is an extension of what you build this week. If Sprint 1 slips, cut from Sprint 4, never from here.

### Mon Aug 3 (2.5h) — Schema

- **DA-101 [S]** `runtime/models.py` — Pydantic models: `RunStatus` enum, `EventType` enum, `Event`, `Run`. Include `parent_run_id` and `forked_at_seq` from the start.
- **DA-102 [M]** `runtime/schema.sql` — SQLite version of the PRD §8 schema: `runs`, `events`, both indexes. Lineage and budget columns included now even though nothing uses them until Sprint 3.
- **DA-103 [S]** ADR `0001-event-log-append-only.md` — why append-only, why `(run_id, step_id)` unique, why results commit before returning.

### Tue Aug 4 (2.5h) — Store, part 1

- **DA-104 [M]** `runtime/store.py` — `Store` protocol + `SQLiteStore`: `create_run`, `get_run`, `append_event`, `get_completed_step`, `read_log`.
- **DA-105 [M]** `tests/test_store.py` — append then read returns events in `seq` order; `get_completed_step` returns `None` for an unknown step; duplicate `(run_id, step_id)` raises.

### Wed Aug 5 (2.5h) — Context

- **DA-106 [L]** `runtime/context.py` — `Context` class with `async def step(step_id, fn)`:
  - increment `seq`
  - look up completed step → if present, return payload, do not call `fn`
  - if absent: append `step_started`, `await fn()`, append `step_completed` with result, **commit**, return
  - on exception: append `step_failed` with error type + message, re-raise
- **DA-107 [S]** `DuplicateStepError` raised if a `step_id` is requested twice within one run.
- **DA-108 [S]** JSON-serializability of step results checked at write time with a clear error message.

*Acceptance: a 3-step workflow runs, log contains 6 events (3 started + 3 completed).*

### Thu Aug 6 (2.5h) — Crash and resume

- **DA-109 [M]** `runtime/decorators.py` — `@workflow(name, version)` registering a workflow function; `run_workflow(store, run_id, fn, input)` orchestrating start → execute → terminal status.
- **DA-110 [L]** `tests/test_crash_recovery.py` — **the flagship test.** A 5-step workflow with a counter tracking real executions. Force a failure at step 3. Re-invoke with the same `run_id`. Assert:
  - steps 1–2 executed exactly once total across both attempts
  - step 3 executed twice (failed, then retried)
  - steps 4–5 executed once
  - final result correct

*This test is the entire project in miniature. Get it green before touching anything else.*

### Fri Aug 7 (2.5h) — Sandbox

- **DA-201 [L]** `agent/sandbox.py` — Docker execution: given a repo path, a patch, and a test command, apply the patch in a container with no network, a memory cap, and a wall-clock timeout; return `(passed, stdout, stderr)`. Container destroyed after each run.
- **DA-202 [S]** `tests/test_sandbox.py` — a trivial repo with one passing and one failing test; assert correct detection of each.

### Sat Aug 8 (5.5h) — Baseline

- **DA-301 [M]** `eval/swebench.py` — load SWE-bench-lite via HuggingFace `datasets`; helpers to check out a repo at the instance's base commit and locate its test command.
- **DA-302 [L]** Get **one** SWE-bench issue running end to end: check out repo → apply the *known-correct* patch from the dataset → run tests → observe pass. *This validates the harness before any agent exists.*
- **DA-203 [M]** `agent/llm.py` — thin provider interface (`complete(messages, model) -> (text, tokens, cost)`) wrapping Anthropic/OpenAI, plus an Ollama backend for local development. **This interface is the future recording layer — keep it narrow.**
- **DA-204 [M]** `agent/naive.py` — the dumbest possible agent: one prompt containing the issue plus the file contents, asking for a unified diff. Wrapped in `ctx.step`.
- **DA-303 [M]** Run the naive agent on 10 issues. Record the resolution rate. **This is your baseline number and it is supposed to be bad.**

### Sat Aug 8, last 30 min — Demo + retro

Record: start a workflow, `Ctrl-C` it, restart it, watch it resume. Write the retro.

**Sprint 1 exit criteria**
- [ ] `test_crash_recovery.py` green
- [ ] A workflow killed mid-run resumes with zero re-execution of completed steps
- [ ] Docker sandbox correctly reports pass/fail on a real SWE-bench repo
- [ ] Naive agent baseline recorded on 10 issues (any number, even 0%)
- [ ] ADRs 0000 and 0001 written

**Risks this sprint**
| Risk | Response |
|---|---|
| SWE-bench Docker environments are notoriously fiddly | Timeboxed to Sat. If not working by hour 3, fall back to 3 hand-built toy repos with failing tests and defer real SWE-bench to Sprint 2. **Do not let this block the runtime work.** |
| `ctx.step` async design churns | Sequential-only this sprint. `gather` is explicitly Sprint 2. |

---

# Sprint 2 — Determinism and Replay
**Aug 10 – Aug 16 · 18 hours · Goal: recorded runs replay identically, offline, at $0**

The intellectually hardest sprint. This is the part that is genuinely difficult to vibe-code and the part you will talk about in interviews.

### Mon Aug 10 (2.5h) — Nondeterminism interception

- **DA-111 [M]** `runtime/determinism.py` — `ctx.now()`, `ctx.random()`, `ctx.uuid()` implemented as ordinary recorded steps.
- **DA-112 [M]** `tests/test_determinism.py` — a workflow branching on `ctx.now().hour`; record at a fixed time, replay later, assert the same branch is taken.

### Tue Aug 11 (2.5h) — Divergence detection

- **DA-113 [L]** Divergence detection in `Context`: during replay, the requested `step_id` must match the log's expected step at that position. Mismatch → `DivergenceError` with a message naming both the expected and actual step IDs.
- **DA-114 [S]** Test: record a run, edit the workflow to insert a step in the middle, replay, assert `DivergenceError`.
- **DA-115 [S]** ADR `0002-divergence-is-fatal.md` — why halting loudly beats silent partial-correctness.

### Wed Aug 12 (2.5h) — Static safety net

- **DA-116 [M]** `runtime/lint.py` — AST-based checker flagging `datetime.now`, `time.time`, `random.*`, `uuid.uuid4`, `os.environ` inside `@workflow`-decorated functions.
- **DA-117 [S]** Wire it into CI as a failing check.
- **DA-118 [S]** Test: a deliberately unsafe workflow is flagged; a safe one is not.

### Thu Aug 13 (2.5h) — Deterministic concurrency

- **DA-119 [L]** `ctx.gather(*step_specs)` — run steps concurrently, but **record completion order** as an event. On replay, resolve in the recorded order regardless of actual timing.
- **DA-120 [S]** Test: gather 3 steps with randomized durations; record; replay 10 times; assert identical ordering each time.

### Fri Aug 14 (2.5h) — Replay harness

- **DA-121 [L]** `runtime/replay.py` — `replay(run_id)` reconstructs a run with a stub LLM serving recorded responses, network disabled. Returns the final result plus the step sequence.
- **DA-122 [S]** `replay_all(dir)` — run a directory of recorded logs as a regression suite; report pass/fail and diffs.

### Sat Aug 15 (5.5h) — Property tests, fault injection, evidence

- **DA-123 [L]** Hypothesis property test: generate random workflow shapes (varying step counts, branches, gathers), record, replay, assert identical step sequences and results. *This is the strongest correctness evidence in the project and a genuine interview talking point.*
- **DA-124 [M]** Fault injection: `replay(run_id, inject={step_id: TimeoutError})` forces a failure at a chosen step so recovery paths can be asserted.
- **DA-125 [M]** Record 20 real naive-agent runs; replay all 20; assert 20/20 identical. Measure replay overhead vs. direct execution and record it in `docs/journal.md`.
- **DA-126 [S]** `da replay --all` CLI command.

**Sprint 2 exit criteria**
- [ ] 20/20 recorded runs replay to identical results, offline, at $0, in seconds
- [ ] Hypothesis property test green over ≥ 100 generated workflows
- [ ] A nondeterministic workflow is caught by CI lint *and* by `DivergenceError` at runtime
- [ ] Fault injection can force a failure at an arbitrary step
- [ ] Replay overhead measured and recorded

---

# Sprint 3 — Multi-Agent, Fork, Budgets
**Aug 17 – Aug 23 · 18 hours · Goal: a real agent on the runtime, forking into parallel candidates**

### Mon Aug 17 (2.5h) — Retrieval

- **DA-205 [L]** `agent/retrieval.py` — tree-sitter chunking of Python repos at function/class granularity, preserving file path and line span.
- **DA-206 [S]** Test on a small repo; assert chunk boundaries land on real function boundaries.

### Tue Aug 18 (2.5h) — Hybrid search

- **DA-207 [M]** BM25 index over chunks (`rank_bm25`).
- **DA-208 [M]** Dense index: `all-MiniLM-L6-v2` embeddings, numpy brute-force cosine search.
- **DA-209 [S]** Reciprocal-rank fusion of the two; `retrieve(issue, repo, k)` returns top-k chunks. Keep BM25-only and dense-only paths callable — Sprint 4 ablations need them.

### Wed Aug 19 (2.5h) — The graph

- **DA-210 [L]** `agent/graph.py` — LangGraph with nodes `retrieve → plan → code → critic → test`, a retry edge from `test` back to `plan` on failure, and a step cap. **Every node body is a `ctx.step`.**
- **DA-211 [S]** Run one issue through the full graph end to end.

### Thu Aug 20 (2.5h) — Budgets and cancellation

- **DA-127 [M]** `runtime/budget.py` — token and USD accounting per run; check before every LLM step; exceeding a cap sets status `budget_exceeded` and leaves the log **resumable**.
- **DA-128 [M]** Cancellation: cancelling a run marks it and all descendants cancelled; workers check for cancellation at step boundaries.
- **DA-129 [S]** Test: a run with a tiny budget halts cleanly, then completes after the cap is raised.

### Fri Aug 21 (2.5h) — Fork, part 1

- **DA-130 [L]** `runtime/fork.py` — `fork(run_id, at_seq)` creates a child with `parent_run_id` and `forked_at_seq`; step resolution walks the lineage chain so prefix steps resolve from the parent's log without copying rows.
- **DA-131 [S]** Test: fork at step 3 of a 6-step run; assert the child re-executes nothing at or before step 3.

### Sat Aug 22 (5.5h) — Fork in anger + demo

- **DA-132 [M]** Recursive fork support + `fork_tree(run_id)` returning the lineage tree.
- **DA-212 [L]** Agent uses fork: when the Coder produces N candidate patches, fork N children, run tests in parallel, first passing child wins, siblings cancelled.
- **DA-213 [M]** Run the multi-agent graph on the same 10 issues as the Sprint 1 baseline. Compare. Record in `docs/journal.md`.
- **DA-133 [S]** `da fork <run_id> --at <seq>` CLI command.

**Sprint 3 exit criteria**
- [ ] Multi-agent graph resolves strictly more issues than the naive baseline
- [ ] A run forks into 3 children sharing the parent prefix with zero re-execution
- [ ] Cancelling a parent cancels every descendant; no orphaned runs keep spending
- [ ] A run halted by budget resumes cleanly after the cap is raised

---

# Sprint 4 — Quality and Ablations
**Aug 24 – Aug 30 · 18 hours · Goal: a results table — then stop, on time, whatever the numbers say**

> **HARD STOP.** This sprint ends Saturday Aug 30 regardless of results. Agent tuning is unbounded work; the runtime is the deliverable. A shipped project at 14% beats an unshipped one at 19%. Write that on a sticky note.

### Mon Aug 24 (2.5h) — Eval harness

- **DA-304 [L]** `eval/harness.py` — run N issues concurrently with per-issue budget caps, persist results to `eval/results/*.json`, resume a partially completed eval (using the runtime itself — dogfooding).
- **DA-305 [S]** Fix a 50-issue development subset with a stable random seed. All ablations use this same subset.

### Tue Aug 25 (2.5h) — Ablations 1 and 2

- **DA-306 [M]** Ablation A: AST chunking vs. fixed-size chunking.
- **DA-307 [M]** Ablation B: hybrid retrieval vs. dense-only vs. BM25-only.

### Wed Aug 26 (2.5h) — Ablations 3–5

- **DA-308 [M]** Ablation C: with Critic vs. without.
- **DA-309 [S]** Ablation D: fork-parallel candidates vs. sequential retry.
- **DA-310 [S]** Ablation E: retry budget 1 vs. 3 vs. 5.

### Thu Aug 27 (2.5h) — Prompt iteration

- **DA-214 [L]** Read 10 failure traces in detail. Fix the two most common failure modes. Re-run the 50-issue subset. **Two fixes only — this is the bottomless pit.**

### Fri Aug 28 (2.5h) — Runtime benchmarks

- **DA-311 [M]** `eval/bench.py` measuring the PRD §11.1 metrics: replay overhead, recovery time, tokens saved on resume, fork prefix savings.
- **DA-312 [S]** Record all numbers into `docs/RESULTS.md`.

### Sat Aug 29 (5.5h) — The full run

- **DA-313 [L]** Full SWE-bench-lite run (300 issues) with the best configuration. Budget-capped. Expect several hours of wall clock — start it first thing and work on other tickets while it runs.
- **DA-314 [M]** `eval/report.py` — matplotlib charts: ablation bar chart, throughput curve, cost-per-issue distribution.
- **DA-315 [M]** `docs/RESULTS.md` complete with every table and chart.

**Sprint 4 exit criteria**
- [ ] Five ablations run on the fixed subset, each with a reported delta
- [ ] One full SWE-bench-lite run completed, resolution rate and cost/issue recorded
- [ ] All runtime benchmarks measured
- [ ] `RESULTS.md` written
- [ ] **Sprint closed Aug 30. No extension.**

---

# Sprint 5 — Distributed
**Aug 31 – Sep 6 · 18 hours · Goal: multiple workers on AWS; kill one, the run survives**

### Mon Aug 31 (2.5h) — Postgres backend

- **DA-134 [M]** `PostgresStore` implementing the same `Store` protocol via `asyncpg`.
- **DA-135 [M]** Alembic migrations. Full test suite passes against both backends (parametrised fixture).

### Tue Sep 1 (2.5h) — Leasing

- **DA-136 [L]** `claim_run()` using `SELECT ... FOR UPDATE SKIP LOCKED`; lease with expiry; heartbeat extension; reclaim of expired leases.
- **DA-137 [S]** Test: 4 concurrent workers against 20 runs — every run executed exactly once.

### Wed Sep 2 (2.5h) — Worker + local compose

- **DA-138 [M]** `runtime/worker.py` — claim → replay → execute → commit → release, with SIGTERM graceful shutdown.
- **DA-139 [M]** `docker-compose.yml` — Postgres + API + 3 workers. `docker compose up` runs the whole system locally.

### Thu Sep 3 (2.5h) — API and viewer

- **DA-401 [M]** FastAPI: `POST /runs`, `GET /runs/{id}`, `GET /runs/{id}/trace`, `POST /runs/{id}/fork`, `POST /runs/{id}/cancel`.
- **DA-402 [M]** Jinja + HTMX trace viewer: run list, event timeline with prompt/response inspection, fork tree.

### Fri Sep 4 (2.5h) — Terraform, part 1

- **DA-403 [L]** `infra/` — VPC with public subnets (**no NAT Gateway**), security groups, ECR repository, RDS `db.t4g.micro`, S3 bucket, SQS queue, Secrets Manager entries.

### Sat Sep 5 (5.5h) — Terraform part 2, deploy, chaos

- **DA-404 [L]** ECS cluster, Fargate task definitions for API and worker, ALB, task IAM roles, CloudWatch log groups.
- **DA-405 [M]** GitHub Actions deploy workflow using **OIDC** (no long-lived AWS keys), building and pushing to ECR on merge to `main`.
- **DA-406 [M]** Deploy. Run 20 issues across 4 Fargate workers.
- **DA-407 [M]** **The chaos test:** `aws ecs stop-task` on a worker mid-run. Assert the lease expires, another worker claims the run, replays the log, and completes it. **Record this on video — it is the closing shot of the demo.**
- **DA-408 [S]** `terraform destroy`. Confirm the AWS bill.

**Sprint 5 exit criteria**
- [ ] Test suite green against both SQLite and Postgres
- [ ] `docker compose up` runs Postgres + API + 3 workers locally
- [ ] `terraform apply` from zero produces a working deployment
- [ ] A killed Fargate task's run is picked up and completed by another worker
- [ ] CI deploys on merge via OIDC
- [ ] `terraform destroy` clean; spend under $25

---

# Sprint 6 — Ship
**Sep 7 – Sep 13 · 18 hours · Goal: a stranger understands it in five minutes**

Do not write new features this sprint. Presentation *is* the work.

### Mon Sep 7 (2.5h) — CLI polish

- **DA-140 [M]** `da` CLI unified: `run`, `resume`, `trace`, `fork`, `replay --all`, `bench`. Rich-formatted output with the `▸ executed` / `⏩ replayed` distinction and a token-savings summary line.
- **DA-141 [S]** `da demo` — a self-contained scripted workflow for the video, needing no SWE-bench setup.

### Tue Sep 8 (2.5h) — Architecture doc

- **DA-501 [L]** `docs/ARCHITECTURE.md` — the mechanism explained from first principles, the determinism constraints and why they exist, design tradeoffs, what was deliberately not built and why.
- **DA-502 [S]** Architecture diagram in Excalidraw → `docs/diagram.png`.

### Wed Sep 9 (2.5h) — README

- **DA-503 [L]** `README.md`: one-line summary → demo GIF → 20-line code example → results table → quickstart → architecture link → **honest prior-art section** naming Temporal/DBOS/Restate and stating what this does differently.
- **DA-504 [S]** Verify the quickstart on a clean clone. If `docker compose up` doesn't work first try, fix it now.

### Thu Sep 10 (2.5h) — Video

- **DA-505 [L]** Record and cut the ≤ 90s demo:
  1. 40-step run starting, trace viewer filling live
  2. `kill -9` mid-run
  3. resume — `⏩ replayed` scrolls, execution continues, tokens-saved line
  4. `da replay --all` — 50 runs, deterministic, 2 s, $0.00
  5. fork tree, one branch going green
  6. Fargate task killed, another worker completes the run
- **DA-506 [S]** Export a GIF of shots 2–3 for the README header.

### Fri Sep 11 (2.5h) — Writeup

- **DA-507 [L]** Blog/LinkedIn post from `docs/journal.md`: the problem, the mechanism, the hardest bug you hit (determinism-related, guaranteed), the results, what you'd do differently.

### Sat Sep 12 (5.5h) — Portfolio integration

- **DA-508 [M]** Resume line added with real numbers substituted.
- **DA-509 [M]** GitHub profile: pin this repo plus Overture, CSE 130 HTTP server, PlotBot. Write proper READMEs for the other three in the same format.
- **DA-510 [M]** LinkedIn: project entry with the demo video, description in the same shape as the format that impressed you (what it does → what you built → what you focused on → skills).
- **DA-511 [M]** Portfolio site updated with all four projects.
- **DA-512 [S]** Publish the post. Repo made public.

**Sprint 6 exit criteria**
- [ ] Clean clone → README → `docker compose up` → working demo in under 5 minutes
- [ ] Video published and embedded
- [ ] `RESULTS.md` and `ARCHITECTURE.md` complete
- [ ] Resume, LinkedIn, GitHub, portfolio all updated
- [ ] Repo public

---

## Part 7 — Cross-sprint policies

### 7.1 Scope control

The v2 backlog in `PRD.md` §18 is closed. Nothing moves from v2 to v1. New ideas go to `docs/BACKLOG.md` and are not discussed again until Sep 14.

This plan has already survived three scope expansions. The fourth is the one that sinks it.

### 7.2 Cut order

If a sprint is running over, cut in this order:

1. Ablations D and E (Sprint 4)
2. The full 300-issue run — report the 50-issue subset instead (Sprint 4)
3. Recursive fork — single-level fork is enough (Sprint 3)
4. Trace viewer fork-tree page — CLI output suffices (Sprint 5)
5. **Never cut:** crash recovery, deterministic replay, divergence detection, the README, the video

### 7.3 Daily discipline

- **Commit every single day**, even a one-line docs fix. The contribution graph is a real signal to recruiters, and daily commits over six weeks read as sustained work rather than a weekend sprint.
- Push before closing the laptop.
- Update the board at end of block.
- Append to `docs/journal.md` daily — three lines minimum. This becomes the writeup and costs nothing at the time.

### 7.4 Cost discipline

- `terraform destroy` at the end of every AWS session, without exception
- Ollama + Qwen2.5-Coder locally for Sprints 1–3; patch quality is irrelevant while testing replay mechanics
- Paid models only for Sprint 4 measurement runs
- Check the AWS billing dashboard every Saturday

### 7.5 When you get stuck

Timebox to 90 minutes. Then: write the problem out in `docs/journal.md` in full sentences (this alone solves maybe a third of them), then move to a different ticket and return the next day. Do not spend a whole block stuck on one thing — the schedule has no slack for it.

### 7.6 Parallel track — recruiting

This runs alongside and is not optional:

| Week | Recruiting task |
|---|---|
| Sprint 1 | Resume rewritten with existing projects |
| Sprint 2 | LinkedIn headline + About + experience entries rewritten |
| Sprint 3 | Overture and CSE 130 READMEs written |
| Sprint 4 | Target company list built; referral outreach begins |
| Sprint 5 | First 10 applications sent |
| Sprint 6 | 30 more applications; mock interviews begin |

LeetCode: 3 problems/day throughout, mediums, weekly review of missed problems. Target ~150 by Sep 13.

---

## Part 8 — Tomorrow morning

1. `DA-001` — GitHub Student Pack
2. `DA-002` — message Sebastian about API credits
3. `DA-004`–`DA-007` — AWS account, MFA, IAM user, $10 budget alarm, CLI verified

Ninety minutes total. Then Sprint 0 continues Thursday.