"""DA-105 -- event store unit tests.

These are parametrised over the Store protocol so Sprint 5's PostgresStore
drops into the same suite unchanged.
"""

from __future__ import annotations

import pytest

from scribe.models import Event, EventType, Run, RunStatus
from scribe.store import SQLiteStore


@pytest.fixture
async def store(tmp_path):
    s = await SQLiteStore.open(tmp_path / "t.db")
    yield s
    await s.close()


async def _mk_run(store, run_id="r1") -> Run:
    run = Run(run_id=run_id, workflow_name="wf", input={"x": 1})
    await store.create_run(run)
    return run


async def test_create_and_get_run(store):
    await _mk_run(store)
    got = await store.get_run("r1")
    assert got is not None
    assert got.workflow_name == "wf"
    assert got.input == {"x": 1}
    assert got.status is RunStatus.PENDING


async def test_get_missing_run_returns_none(store):
    assert await store.get_run("nope") is None


async def test_events_read_back_in_seq_order(store):
    await _mk_run(store)
    for i in range(5):
        await store.append_event(
            Event(
                run_id="r1",
                seq=i,
                step_id=f"s{i}",
                event_type=EventType.STEP_COMPLETED,
                payload={"i": i},
            )
        )
    log = await store.read_log("r1")
    assert [e.seq for e in log] == [0, 1, 2, 3, 4]
    assert [e.payload["i"] for e in log] == [0, 1, 2, 3, 4]


async def test_next_seq_is_monotonic(store):
    await _mk_run(store)
    assert await store.next_seq("r1") == 0
    await store.append_event(
        Event(run_id="r1", seq=0, event_type=EventType.RUN_STARTED)
    )
    assert await store.next_seq("r1") == 1


async def test_get_completed_step_finds_only_completed(store):
    await _mk_run(store)
    await store.append_event(
        Event(run_id="r1", seq=0, step_id="a", event_type=EventType.STEP_STARTED)
    )
    # started but not completed -> lookup misses
    assert await store.get_completed_step("r1", "a") is None

    await store.append_event(
        Event(
            run_id="r1",
            seq=1,
            step_id="a",
            event_type=EventType.STEP_COMPLETED,
            payload="done",
        )
    )
    found = await store.get_completed_step("r1", "a")
    assert found is not None and found.payload == "done"


async def test_completed_step_uniqueness_enforced(store):
    """The DB itself refuses a second completion for the same step."""
    import aiosqlite

    await _mk_run(store)
    await store.append_event(
        Event(run_id="r1", seq=0, step_id="a", event_type=EventType.STEP_COMPLETED)
    )
    with pytest.raises(aiosqlite.IntegrityError):
        await store.append_event(
            Event(run_id="r1", seq=1, step_id="a", event_type=EventType.STEP_COMPLETED)
        )


async def test_runs_are_isolated(store):
    await _mk_run(store, "r1")
    await _mk_run(store, "r2")
    await store.append_event(
        Event(run_id="r1", seq=0, step_id="s", event_type=EventType.STEP_COMPLETED)
    )
    assert len(await store.read_log("r1")) == 1
    assert len(await store.read_log("r2")) == 0
    assert await store.get_completed_step("r2", "s") is None


async def test_update_run_persists_status_and_result(store):
    run = await _mk_run(store)
    run.status = RunStatus.COMPLETED
    run.result = {"answer": 42}
    await store.update_run(run)

    got = await store.get_run("r1")
    assert got.status is RunStatus.COMPLETED
    assert got.result == {"answer": 42}


async def test_lineage_columns_roundtrip(store):
    """Fork columns exist from day one even though fork() lands in Sprint 3."""
    parent = await _mk_run(store, "parent")
    child = Run(
        run_id="child",
        workflow_name="wf",
        parent_run_id=parent.run_id,
        forked_at_seq=7,
    )
    await store.create_run(child)

    got = await store.get_run("child")
    assert got.parent_run_id == "parent"
    assert got.forked_at_seq == 7
