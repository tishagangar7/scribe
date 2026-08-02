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
