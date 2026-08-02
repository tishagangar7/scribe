"""DA-111/DA-112 -- ctx.now/ctx.random/ctx.uuid replay to the same value.

The technique: record with the real clock/RNG monkeypatched to a known
value, run to completion, then simulate resuming a not-yet-finalized run
(status forced back to RUNNING, as would happen on a crash right after the
last step but before RUN_COMPLETED lands) with the clock/RNG monkeypatched
to a *different* value. If replay is working, the workflow takes the same
branch / returns the same value it did the first time -- proving it read
from the log, not from a fresh call to the real source.
"""

from __future__ import annotations

from datetime import UTC, datetime

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


async def test_now_branch_is_stable_across_replay(store, monkeypatch):
    """A workflow branching on ctx.now().hour takes the same branch on replay."""

    @workflow(name="clock_branch")
    async def clock_branch(ctx):
        now = await ctx.now()
        return "morning" if now.hour < 12 else "evening"

    monkeypatch.setattr(
        "scribe.context.utcnow", lambda: datetime(2024, 1, 1, 9, 0, tzinfo=UTC)
    )
    run_id = await start_run(store, "clock_branch")
    assert await execute_run(store, run_id) == "morning"

    await _force_resume(store, run_id)
    monkeypatch.setattr(
        "scribe.context.utcnow", lambda: datetime(2024, 1, 1, 23, 0, tzinfo=UTC)
    )
    replayed = await execute_run(store, run_id)
    assert replayed == "morning", "replay must use the recorded time, not real time"


async def test_random_replays_same_value(store, monkeypatch):
    values = iter([0.1, 0.9])
    monkeypatch.setattr("scribe.context._random.random", lambda: next(values))

    @workflow(name="dice")
    async def dice(ctx):
        return await ctx.random()

    run_id = await start_run(store, "dice")
    first = await execute_run(store, run_id)
    assert first == 0.1

    await _force_resume(store, run_id)
    replayed = await execute_run(store, run_id)
    assert replayed == 0.1, "replay must not draw a fresh random value"


async def test_uuid_replays_same_value(store, monkeypatch):
    import uuid as uuid_module

    fixed = uuid_module.uuid4()
    other = uuid_module.uuid4()
    monkeypatch.setattr("scribe.context._uuid.uuid4", lambda: fixed)

    @workflow(name="idgen")
    async def idgen(ctx):
        return str(await ctx.uuid())

    run_id = await start_run(store, "idgen")
    first = await execute_run(store, run_id)
    assert first == str(fixed)

    await _force_resume(store, run_id)
    monkeypatch.setattr("scribe.context._uuid.uuid4", lambda: other)
    replayed = await execute_run(store, run_id)
    assert replayed == str(fixed), "replay must not draw a fresh uuid"


async def test_repeated_calls_get_distinct_step_ids(store, monkeypatch):
    """Two ctx.now() calls in one workflow must not collide as duplicate steps."""
    ticks = iter(
        [
            datetime(2024, 1, 1, 9, 0, tzinfo=UTC),
            datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        ]
    )
    monkeypatch.setattr("scribe.context.utcnow", lambda: next(ticks))

    @workflow(name="two_ticks")
    async def two_ticks(ctx):
        first = await ctx.now()
        second = await ctx.now()
        return [first.hour, second.hour]

    run_id = await start_run(store, "two_ticks")
    result = await execute_run(store, run_id)
    assert result == [9, 10]


async def test_now_still_participates_in_duplicate_step_detection(store):
    """A user step manually named like an auto-generated one still collides."""

    @workflow(name="collide")
    async def collide(ctx):
        await ctx.step("ctx.now:0", lambda: "manual")
        await ctx.now()

    run_id = await start_run(store, "collide")
    with pytest.raises(DuplicateStepError):
        await execute_run(store, run_id)
