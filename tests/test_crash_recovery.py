"""DA-110 -- the flagship test.

This file is the entire project in miniature. If these pass, durable
execution works; everything in later sprints is an extension of what is
proven here.

The technique throughout: an execution counter per step. After a crash and
resume, we assert that completed steps ran EXACTLY ONCE across all attempts.
That is the claim -- not "it finishes eventually" but "it never re-does paid
work."
"""

from __future__ import annotations

import pytest

from scribe.decorators import clear_registry, execute_run, start_run, workflow
from scribe.errors import DivergenceError, DuplicateStepError
from scribe.models import EventType, RunStatus
from scribe.store import SQLiteStore


@pytest.fixture
async def store(tmp_path):
    s = await SQLiteStore.open(tmp_path / "test.db")
    yield s
    await s.close()


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


class Counter:
    """Tracks how many times each step's function actually executed."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    def hit(self, name: str) -> str:
        self.calls[name] = self.calls.get(name, 0) + 1
        return f"result-of-{name}"

    def __getitem__(self, name: str) -> int:
        return self.calls.get(name, 0)

    @property
    def total(self) -> int:
        return sum(self.calls.values())


# ---------------------------------------------------------------------------
# The core claim
# ---------------------------------------------------------------------------


async def test_crash_midway_resumes_without_reexecution(store):
    """Kill a 5-step workflow at step 3; resume; nothing re-executes.

    This is THE test. Steps 1 and 2 must have executed exactly once in total
    across both attempts -- not once per attempt.
    """
    counter = Counter()
    should_fail = {"value": True}

    @workflow(name="five_steps")
    async def five_steps(ctx):
        a = await ctx.step("one", lambda: counter.hit("one"))
        b = await ctx.step("two", lambda: counter.hit("two"))

        def three():
            if should_fail["value"]:
                counter.hit("three")
                raise RuntimeError("simulated rate limit")
            return counter.hit("three")

        c = await ctx.step("three", three)
        d = await ctx.step("four", lambda: counter.hit("four"))
        e = await ctx.step("five", lambda: counter.hit("five"))
        return [a, b, c, d, e]

    run_id = await start_run(store, "five_steps")

    # --- Attempt 1: crashes at step three ---
    with pytest.raises(RuntimeError, match="simulated rate limit"):
        await execute_run(store, run_id)

    assert counter["one"] == 1
    assert counter["two"] == 1
    assert counter["three"] == 1
    assert counter["four"] == 0, "step after the failure must not have run"

    # --- Attempt 2: same run_id, resumes ---
    should_fail["value"] = False
    result = await execute_run(store, run_id)

    # The claim: steps 1 and 2 were NOT re-executed on the second attempt.
    assert counter["one"] == 1, "step one re-executed -- durability is broken"
    assert counter["two"] == 1, "step two re-executed -- durability is broken"
    assert counter["three"] == 2, "step three should retry (it failed)"
    assert counter["four"] == 1
    assert counter["five"] == 1

    assert result == [
        "result-of-one",
        "result-of-two",
        "result-of-three",
        "result-of-four",
        "result-of-five",
    ]

    run = await store.get_run(run_id)
    assert run.status is RunStatus.COMPLETED


async def test_repeated_crashes_still_converge(store):
    """Fail at a different step each attempt. Total executions stay minimal."""
    counter = Counter()
    fail_at = {"step": "two"}

    @workflow(name="flaky")
    async def flaky(ctx):
        out = []
        for name in ["one", "two", "three", "four"]:

            def make(n=name):
                def f():
                    counter.hit(n)
                    if fail_at["step"] == n:
                        raise ConnectionError(f"boom at {n}")
                    return f"ok-{n}"

                return f

            out.append(await ctx.step(name, make()))
        return out

    run_id = await start_run(store, "flaky")

    for failing in ["two", "three", "four"]:
        fail_at["step"] = failing
        with pytest.raises(ConnectionError):
            await execute_run(store, run_id)

    fail_at["step"] = None
    result = await execute_run(store, run_id)

    assert result == ["ok-one", "ok-two", "ok-three", "ok-four"]
    # one succeeded first try; the rest failed once then succeeded.
    assert counter["one"] == 1
    assert counter["two"] == 2
    assert counter["three"] == 2
    assert counter["four"] == 2
    # Naive re-execution from scratch would have cost 1+2+3+4 = 10 calls.
    assert counter.total == 7


async def test_completed_run_is_not_reexecuted(store):
    """Calling execute_run on a finished run returns the cached result."""
    counter = Counter()

    @workflow(name="once")
    async def once(ctx):
        return await ctx.step("only", lambda: counter.hit("only"))

    run_id = await start_run(store, "once")
    first = await execute_run(store, run_id)
    second = await execute_run(store, run_id)

    assert first == second == "result-of-only"
    assert counter["only"] == 1


async def test_replay_counters_report_savings(store):
    """ctx tracks replayed vs executed -- this feeds the CLI savings line."""
    counter = Counter()
    fail = {"v": True}

    @workflow(name="counted")
    async def counted(ctx):
        await ctx.step("a", lambda: counter.hit("a"))
        await ctx.step("b", lambda: counter.hit("b"))

        def c():
            if fail["v"]:
                raise RuntimeError("nope")
            return counter.hit("c")

        return await ctx.step("c", c)

    run_id = await start_run(store, "counted")
    with pytest.raises(RuntimeError):
        await execute_run(store, run_id)

    fail["v"] = False
    await execute_run(store, run_id)

    log = await store.read_log(run_id)
    completed = [e for e in log if e.event_type is EventType.STEP_COMPLETED]
    assert {e.step_id for e in completed} == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------


async def test_duplicate_step_id_raises(store):
    """Two steps sharing an ID makes resumption ambiguous -- reject it."""

    @workflow(name="dupe")
    async def dupe(ctx):
        await ctx.step("same", lambda: 1)
        await ctx.step("same", lambda: 2)

    run_id = await start_run(store, "dupe")
    with pytest.raises(DuplicateStepError):
        await execute_run(store, run_id)


async def test_loop_steps_need_indexed_ids(store):
    """The correct pattern for loops: include the index in the step_id."""
    counter = Counter()

    @workflow(name="looped")
    async def looped(ctx):
        results = []
        for i in range(4):
            results.append(
                await ctx.step(f"item:{i}", lambda i=i: counter.hit(f"item:{i}"))
            )
        return results

    run_id = await start_run(store, "looped")
    result = await execute_run(store, run_id)
    assert len(result) == 4
    assert counter.total == 4


async def test_divergence_detected_when_workflow_changes(store):
    """Sprint-1 divergence detection: a changed code path halts loudly.

    Recording runs steps a, b. Then the workflow is edited to insert a new
    step between them. Replay must raise rather than silently interleave.
    """
    fail = {"v": True}

    @workflow(name="v1")
    async def v1(ctx):
        await ctx.step("a", lambda: "a")
        await ctx.step("b", lambda: "b")

        def c():
            if fail["v"]:
                raise RuntimeError("stop here")
            return "c"

        return await ctx.step("c", c)

    run_id = await start_run(store, "v1")
    with pytest.raises(RuntimeError):
        await execute_run(store, run_id)

    # Simulate a code change: a new step appears between a and b.
    clear_registry()

    @workflow(name="v1")
    async def v1_changed(ctx):
        await ctx.step("a", lambda: "a")
        await ctx.step("NEW", lambda: "new")
        await ctx.step("b", lambda: "b")
        return await ctx.step("c", lambda: "c")

    with pytest.raises(DivergenceError) as exc:
        await execute_run(store, run_id)

    assert exc.value.expected == "b"
    assert exc.value.actual == "NEW"


async def test_non_serializable_result_rejected(store):
    """Caught at write time, not surfaced hours later during a replay."""
    from scribe.errors import NonSerializableResultError

    class NotJSON:
        pass

    @workflow(name="bad")
    async def bad(ctx):
        return await ctx.step("oops", lambda: NotJSON())

    run_id = await start_run(store, "bad")
    with pytest.raises(NonSerializableResultError):
        await execute_run(store, run_id)


async def test_async_step_functions_supported(store):
    """Steps may be sync or async -- LLM SDKs are async."""
    import asyncio

    @workflow(name="asyncish")
    async def asyncish(ctx):
        async def slow():
            await asyncio.sleep(0.01)
            return "async-result"

        return await ctx.step("s", slow)

    run_id = await start_run(store, "asyncish")
    assert await execute_run(store, run_id) == "async-result"


async def test_failed_step_recorded_with_error_detail(store):
    """Failures are events too -- the log records what went wrong."""

    @workflow(name="failing")
    async def failing(ctx):
        def boom():
            raise ValueError("specific failure detail")

        return await ctx.step("x", boom)

    run_id = await start_run(store, "failing")
    with pytest.raises(ValueError):
        await execute_run(store, run_id)

    log = await store.read_log(run_id)
    failures = [e for e in log if e.event_type is EventType.STEP_FAILED]
    assert len(failures) == 1
    assert failures[0].error_type == "ValueError"
    assert "specific failure detail" in failures[0].error_message
