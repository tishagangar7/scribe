# Engineering journal

Daily standup format. Three lines minimum. This file becomes the writeup in
Sprint 6, so write for a reader, not just for yourself.

---

## Sprint 1 — Aug 3

**Done:** DA-101 models, DA-102 schema, DA-104 store, DA-106/107/108 context,
DA-109 decorators, DA-105 store tests, DA-110 crash recovery tests. 19 tests
green. Demo script showing crash/resume across separate processes.

**Learned:** The commit-before-return ordering in `ctx.step` is not a detail —
returning first would open a window where a paid-for step is lost. Wrote it up
as ADR 0002.

**Blockers:** none.

**Next:** DA-201 Docker sandbox, DA-301 SWE-bench loader.

---

## Sprint 1 — Aug 7

**Done:** DA-201 `agent/sandbox.py` -- `run_in_sandbox(repo_path, patch, test_command)`
copies the repo to a throwaway temp dir, applies the patch on the host via
`patch -p1` (raising `PatchApplyError` before any container starts if it
doesn't apply cleanly), then runs the test command in a container with
`network_mode="none"`, a memory cap, and a wall-clock timeout enforced by
killing the container if `wait()` doesn't return in time. Container always
removed in `finally`. DA-202 `tests/test_sandbox.py`: pass/fail detection,
patch flipping a failing check to passing, a bad patch rejected pre-container,
no network reachable inside the sandbox, timeout actually kills a sleeping
container. All 6 green against real Docker.

**Learned:** `flake8-tidy-imports.banned-api` in `pyproject.toml` was scoped
too broadly -- as written it banned importing `agent`/`api`/`eval` from
*any* file, not just from `scribe/` as ADR 0003 actually requires. Fixed with
`per-file-ignores` scoping `TID251` off outside `scribe/**`. Also: patch
application belongs on the host, not inside the container -- it's just
rewriting text files, and keeping it host-side means the sandboxed container
never needs `git`/`patch` baked in or network access to install them.

**Blockers:** none.

**Next:** DA-301 SWE-bench loader, DA-302 one issue end-to-end with the
dataset's known-correct patch.

---

## Sprint 1 — Aug 8

**Done:** DA-301 `eval/swebench.py` -- `load_lite()`/`get_instance()` pull
SWE-bench-lite from HuggingFace (`princeton-nlp/SWE-bench_Lite`) into a
`SWEBenchInstance` model; `checkout_repo()` clones the real repo and checks
out `base_commit`; `test_command()` builds a `pytest` invocation from
FAIL_TO_PASS/PASS_TO_PASS (pytest-based repos only -- django/sympy use
their own runners, out of scope). DA-302 `eval/validate_harness.py`: picked
`pylint-dev__pylint-5859` (one FAIL_TO_PASS test, no live-network calls in
its suite, unlike `psf/requests` which hits real HTTP test servers). Built
a Docker image with the instance's pinned deps, ran the harness through the
DA-201 sandbox twice via the real `run_in_sandbox`: with just `test_patch`
applied (issue reproduces, fails) and with `test_patch + patch` applied
(gold fix resolves it, passes). **Baseline recorded: harness confirmed
end-to-end on a real SWE-bench-lite issue** -- this is the Week 1 exit
criterion from the PRD.

**Learned:** `requirements_test_min.txt` pinned `pytest-benchmark~=3.4`,
whose latest matching release drags in a `py` version missing
`py.io.TerminalWriter` that the benchmark plugin still imports --
unrelated to the one test file being run, but it broke pytest collection
entirely until dropped from the install. Installing only what a target
test actually needs, rather than a repo's full test-requirements file, is
safer for one-off validation. Also confirmed empirically: `psf/requests`'s
own test suite makes real outbound HTTP calls, which would fail under
`network_mode="none"` -- a good repo to avoid for early sandboxed
instances.

**Blockers:** none.

**Next:** Sprint 2 -- determinism interception (`ctx.now`/`random`/`uuid`),
divergence detection hardening, `ctx.gather` deterministic ordering.

---

## Sprint 2 — Aug 10

**Done:** DA-111 `Context.now()`/`random()`/`uuid()` in `scribe/context.py` --
each is an ordinary `ctx.step` under the hood (`ctx.now:0`, `ctx.random:0`,
`ctx.uuid:0`, ...), so the value is recorded on first execution and served
from the log on every replay instead of hitting the real clock/RNG again.
Repeated calls within one workflow get distinct auto-incrementing step_ids so
they don't collide as duplicates. DA-112 `tests/test_determinism.py`: a
workflow branching on `ctx.now().hour`, recorded with the clock monkeypatched
to 9am, then "resumed" (status forced back to RUNNING, simulating a crash
right before RUN_COMPLETED lands) with the clock monkeypatched to 11pm --
replay still returns the 9am branch. Same technique for `ctx.random()` and
`ctx.uuid()`. 5 new tests green, 24 total (sandbox suite needs Docker, run
separately).

**Learned:** DA-113 (divergence detection) turned out to already exist --
`Context._check_divergence` and the position check in the execute path were
built into `scribe/context.py` back in Sprint 1, and `DivergenceError`'s
message already pointed at `ctx.now`/`ctx.random`/`ctx.uuid` as the fix. So
this sprint's real remaining scope was narrower than planned: just DA-111/112.
Testing replay-stability for a completed run needs care -- `execute_run`
short-circuits and returns the cached result without touching the workflow
body at all once `status == COMPLETED`, so proving replay (not just
memoization) requires forcing the run back to `RUNNING` first, the same
"crash before the final status write lands" scenario the durability tests
already use.

**Blockers:** none.

**Next:** DA-116/117/118 static lint (`runtime/lint.py`, flagging
`datetime.now`/`random.*`/`uuid.uuid4`/`os.environ` inside `@workflow`
functions, wired into CI), then DA-119/120 `ctx.gather` deterministic
ordering.

---

## Sprint 2 — Aug 10, continued (CI fix)

**Done:** The push that landed DA-301/302 also triggered the first CI run
ever to exercise `tests/test_sandbox.py`, and it failed: `PermissionError`
removing `__pycache__` out of the sandbox's temp dir during cleanup.
`agent/sandbox.py`'s `_run_container` never set a container user, so it ran
as root inside `python:3.12-slim`; on GitHub's real Linux bind mount (unlike
Docker Desktop's file-sharing shim on macOS, which had been quietly masking
this), anything the container wrote into the bind-mounted repo landed on the
host owned by root, and the non-root CI runner couldn't clean it up
afterward. Fixed by passing `user=f"{os.getuid()}:{os.getgid()}"` to
`containers.run` so the container can never leave root-owned files behind.

**Learned:** local-only testing on macOS is not sufficient evidence a Docker
sandbox is correct -- Docker Desktop's file-sharing layer remaps container-
root writes back to the host user, hiding exactly this class of bug. The
real check only happened once CI (a genuine Linux host) ran it.

**Blockers:** none.

---

## Sprint 2 — Aug 11–16 (close-out)

**Done:** Finished everything remaining in the sprint plan.
- **DA-115** `docs/adr/0004-divergence-is-fatal.md` (the plan calls it ADR
  0002, but 0001-0003 were already taken by Sprint 0/1 decisions).
- **DA-116/117/118** `scribe/lint.py` -- AST-based checker walking every
  `@workflow` function's body for `datetime.now`/`.utcnow`, `time.time`,
  `random.*`, `uuid.uuid4`, and `os.environ`/`os.getenv`, resolving simple
  `import x [as y]` / `from x import y [as z]` aliasing so it isn't fooled
  by renaming. Wired into `.github/workflows/ci.yml` as its own step, run
  over `agent scribe eval`. 12 tests in `tests/test_lint.py`.
- **DA-119/120** `Context.gather(*specs)` -- runs step bodies concurrently
  but records the whole group as *one* atomic step (payload holds the
  results plus the real completion order, kept for observability). Replay
  never re-runs the group, so completion order can vary freely between the
  recording and any number of replays without ever counting as divergence.
  Trade-off, noted in the docstring: a crash mid-gather re-runs the whole
  group on resume rather than only the unfinished member, same as any other
  single `ctx.step`. 4 tests in `tests/test_gather.py`, including 10 forced
  resumes with randomized durations each time, asserting identical ordering
  every time.
- **DA-121/122/124** `scribe/replay.py` -- `replay(store, run_id, inject=...)`
  re-invokes the workflow body through a `_ReplayContext` that raises
  `ReplayIncompleteError` instead of ever executing a step the log doesn't
  already have (the "network disabled" requirement, enforced structurally),
  and can force an arbitrary already-succeeded step to fail via `inject=
  {step_id: ExceptionType}` for testing recovery paths without needing a
  real historical failure. `replay_all(store)` runs every run in a store as
  a regression suite. 7 tests in `tests/test_replay.py`, including one that
  swaps in a step body that fails the test outright if replay ever calls it
  for real -- the strongest possible proof replay never executes live.
- **DA-123** `tests/test_property_replay.py` -- Hypothesis generates random
  workflow shapes (plain steps, gathers, and branches nested two levels
  deep) interpreted by one generic `shape_runner` workflow; for each of 100
  generated shapes, record then replay and assert identical result and step
  sequence. Far stronger evidence than any hand-picked example.
- **DA-125** `replay_evidence.py` (standalone script, not CI -- deliberately
  runs the real-latency `research` workflow from `demo.py`, sleeps and
  simulated failures included): recorded 20 real runs, replayed all 20.
  **20/20 identical.** Direct execution: 261.5s total (13.1s/run average,
  including retries against the ~35% simulated failure rate). Replay: 0.061s
  total. **~4,310x speedup, offline, $0.**
- **DA-126** `scribe/cli.py`, `da` console script (`[project.scripts]`,
  required adding a `[build-system]`/hatchling section since the project had
  never been packaged before). `da replay --all --db PATH --import MODULE`.
  The `--import` flag exists because the workflow registry is in-process
  only -- a real `da` invocation starts with an empty registry, so it has to
  be told which module's `@workflow` decorators to run first, or every
  replay fails with `WorkflowNotFoundError`. Caught this by actually running
  the built `da` binary end-to-end against a real db in a fresh process,
  not just calling `main()` in-process the way the unit tests do.

**Learned:** the property test (DA-123) and the swap-in-a-failing-step-body
test in `test_replay.py` are the two pieces of evidence worth leading with
in an interview -- one is breadth (100 generated shapes), the other is a
single airtight example (replay provably never executes anything for real).
Also: a CLI wrapping in-process state (the workflow registry) needs its own
end-to-end smoke test run as a real subprocess -- calling `main()` directly
from a test that already populated the registry hides exactly the failure
mode a real user hits first.

**Blockers:** none.

**Sprint 2 exit criteria: all met.**
- [x] 20/20 recorded runs replay to identical results, offline, at $0, in seconds
- [x] Hypothesis property test green over >= 100 generated workflows
- [x] A nondeterministic workflow is caught by CI lint *and* by `DivergenceError` at runtime
- [x] Fault injection can force a failure at an arbitrary step
- [x] Replay overhead measured and recorded

**Next:** Sprint 3 -- retrieval (tree-sitter chunking), hybrid BM25/dense
search, the LangGraph agent graph (retrieve -> plan -> code -> critic ->
test), budgets and cancellation, fork into parallel candidates. Needs an
LLM API key (none configured yet -- `.env` doesn't exist, only
`.env.example`) and new heavy dependencies not yet installed: `langgraph`,
`tree-sitter`, `sentence-transformers`, `rank_bm25`.
