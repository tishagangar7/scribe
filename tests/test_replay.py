"""DA-121/122/124 -- replay reconstructs runs offline, at $0, from the log alone.

The core proof technique: give each workflow a "real work" function that
raises if it's ever actually called, then assert replay still produces the
right result -- which is only possible if every step came from the log.
"""

from __future__ import annotations

import pytest

from scribe.decorators import clear_registry, execute_run, start_run, workflow
from scribe.errors import RunNotFoundError
from scribe.replay import ReplayIncompleteError, replay, replay_all
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


def _loud(name: str):
    """A step body that fails the test loudly if replay ever calls it for real."""

    def _fn():
        raise AssertionError(f"step {name!r} executed for real during replay")

    return _fn


async def test_replay_reconstructs_result_without_real_execution(store):
    @workflow(name="two_step")
    async def two_step(ctx):
        a = await ctx.step("a", lambda: "real-a")
        b = await ctx.step("b", lambda: "real-b")
        return f"{a}-{b}"

    run_id = await start_run(store, "two_step")
    original = await execute_run(store, run_id)
    assert original == "real-a-real-b"

    result = await replay(store, run_id)
    assert result.passed
    assert result.result == "real-a-real-b"
    assert result.step_sequence == ["a", "b"]


async def test_replay_never_calls_the_real_step_body(store):
    """Swap in a step body that fails loudly if called -- replay must not call it."""

    @workflow(name="loud")
    async def loud(ctx):
        return await ctx.step("s", lambda: "recorded-value")

    run_id = await start_run(store, "loud")
    await execute_run(store, run_id)

    # Same workflow name, but "s" would now raise if actually executed.
    clear_registry()

    @workflow(name="loud")
    async def loud_v2(ctx):
        return await ctx.step("s", _loud("s"))

    result = await replay(store, run_id)
    assert result.passed
    assert result.result == "recorded-value"


async def test_replay_of_incomplete_run_reports_failure_not_a_live_call(store):
    """A run that crashed mid-way has a truncated log; replay must not paper over it."""

    should_fail = {"v": True}

    @workflow(name="partial")
    async def partial(ctx):
        await ctx.step("a", lambda: "a")

        def maybe_fail():
            if should_fail["v"]:
                raise RuntimeError("boom")
            return "b"

        return await ctx.step("b", maybe_fail)

    run_id = await start_run(store, "partial")
    with pytest.raises(RuntimeError):
        await execute_run(store, run_id)

    # "b" never completed, so the log only has "a". replay must not execute
    # "b" for real to paper over the gap -- it should report the gap instead.
    result = await replay(store, run_id)
    assert not result.passed
    assert ReplayIncompleteError.__name__ in result.error
    assert result.step_sequence == ["a"]


async def test_replay_raises_for_unknown_run(store):
    with pytest.raises(RunNotFoundError):
        await replay(store, "nonexistent")


async def test_fault_injection_forces_failure_at_chosen_step(store):
    """DA-124: force a failure at an arbitrary already-succeeded step."""

    @workflow(name="two_step_inject")
    async def two_step_inject(ctx):
        a = await ctx.step("a", lambda: "a")
        b = await ctx.step("b", lambda: "b")
        return f"{a}-{b}"

    run_id = await start_run(store, "two_step_inject")
    await execute_run(store, run_id)

    clean = await replay(store, run_id)
    assert clean.passed

    injected = await replay(store, run_id, inject={"b": TimeoutError})
    assert not injected.passed
    assert "TimeoutError" in injected.error
    assert injected.step_sequence == ["a"], "the group failed before reaching b"


async def test_replay_all_reports_pass_and_fail_across_a_store(store):
    @workflow(name="good")
    async def good(ctx):
        return await ctx.step("s", lambda: "ok")

    @workflow(name="crashes")
    async def crashes(ctx):
        await ctx.step("a", lambda: "a")

        def boom():
            raise RuntimeError("nope")

        return await ctx.step("b", boom)

    good_run = await start_run(store, "good")
    await execute_run(store, good_run)

    crash_run = await start_run(store, "crashes")
    with pytest.raises(RuntimeError):
        await execute_run(store, crash_run)

    suite = await replay_all(store)
    assert suite.total == 2
    assert suite.passed == 1
    assert not suite.all_passed
    assert {f.run_id for f in suite.failures} == {crash_run}


async def test_replay_all_on_all_healthy_runs_passes(store):
    @workflow(name="healthy")
    async def healthy(ctx):
        return await ctx.step("s", lambda: "ok")

    for _ in range(5):
        run_id = await start_run(store, "healthy")
        await execute_run(store, run_id)

    suite = await replay_all(store)
    assert suite.total == 5
    assert suite.all_passed
