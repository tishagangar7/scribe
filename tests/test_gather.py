"""DA-119/120 -- ctx.gather runs steps concurrently, replays deterministically.

Real timing is randomized on purpose (`asyncio.sleep` with jittered
durations) so completion order for real varies attempt to attempt. The
claim under test: however it actually completed the first time, replay
returns exactly that recorded order and those results, every time, with no
re-execution.
"""

from __future__ import annotations

import asyncio
import random

import pytest

from scribe.decorators import clear_registry, execute_run, start_run, workflow
from scribe.errors import DuplicateStepError
from scribe.models import RunStatus
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


async def _force_resume(store, run_id: str) -> None:
    """Simulate a crash after the last step completed but before RUN_COMPLETED."""
    run = await store.get_run(run_id)
    run.status = RunStatus.RUNNING
    await store.update_run(run)


async def test_gather_returns_results_in_spec_order(store):
    @workflow(name="three_way")
    async def three_way(ctx):
        async def slow(name, delay):
            await asyncio.sleep(delay)
            return f"result-{name}"

        return await ctx.gather(
            ("a", lambda: slow("a", 0.03)),
            ("b", lambda: slow("b", 0.01)),
            ("c", lambda: slow("c", 0.02)),
        )

    run_id = await start_run(store, "three_way")
    result = await execute_run(store, run_id)

    # "b" finishes first for real, but the return order matches the specs.
    assert result == ["result-a", "result-b", "result-c"]


async def test_gather_replays_identical_order_across_ten_resumes(store):
    """Randomized durations each attempt; replay never re-derives the order."""
    call_count = {"n": 0}

    @workflow(name="racer")
    async def racer(ctx):
        call_count["n"] += 1

        async def slow(name):
            await asyncio.sleep(random.uniform(0.001, 0.05))
            return name

        return await ctx.gather(
            ("x", lambda: slow("x")),
            ("y", lambda: slow("y")),
            ("z", lambda: slow("z")),
        )

    run_id = await start_run(store, "racer")
    first = await execute_run(store, run_id)
    assert call_count["n"] == 1

    for _ in range(10):
        await _force_resume(store, run_id)
        replayed = await execute_run(store, run_id)
        assert replayed == first, "replay must not re-derive completion order"

    # The workflow body ran on every forced resume, but ctx.gather's cached
    # step meant `slow()` itself was never invoked again after the first time.
    assert call_count["n"] == 11


async def test_duplicate_step_id_within_gather_rejected(store):
    @workflow(name="dupe_gather")
    async def dupe_gather(ctx):
        return await ctx.gather(
            ("same", lambda: 1),
            ("same", lambda: 2),
        )

    run_id = await start_run(store, "dupe_gather")
    with pytest.raises(DuplicateStepError):
        await execute_run(store, run_id)


async def test_gather_is_all_or_nothing_on_crash(store):
    """A failing member fails the whole group; nothing partial is recorded."""
    attempts = {"n": 0}

    @workflow(name="flaky_gather")
    async def flaky_gather(ctx):
        attempts["n"] += 1
        should_fail = attempts["n"] == 1

        async def maybe_fail(name):
            if should_fail and name == "b":
                raise RuntimeError("boom")
            return name

        return await ctx.gather(
            ("a", lambda: maybe_fail("a")),
            ("b", lambda: maybe_fail("b")),
        )

    run_id = await start_run(store, "flaky_gather")
    with pytest.raises(RuntimeError):
        await execute_run(store, run_id)

    result = await execute_run(store, run_id)
    assert result == ["a", "b"]
    assert attempts["n"] == 2, "the whole group re-ran, not just the failed member"
