"""DA-128 -- cancelling a run marks it and all descendants cancelled.

Fork (scribe/fork.py) doesn't exist yet, so "descendants" are simulated by
hand-constructing Run rows with parent_run_id set -- cancel_run only needs
that column, not the fork mechanism itself.
"""

from __future__ import annotations

import pytest

from scribe.cancel import cancel_run
from scribe.decorators import clear_registry, execute_run, start_run, workflow
from scribe.errors import RunCancelledError, RunNotFoundError
from scribe.models import Run, RunStatus
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


async def _child_run(store, parent_id: str, run_id: str) -> None:
    await store.create_run(
        Run(
            run_id=run_id,
            workflow_name="child",
            status=RunStatus.RUNNING,
            parent_run_id=parent_id,
        )
    )


async def test_cancel_marks_the_run_and_all_descendants(store):
    await store.create_run(
        Run(run_id="root", workflow_name="w", status=RunStatus.RUNNING)
    )
    await _child_run(store, "root", "child-a")
    await _child_run(store, "root", "child-b")
    await _child_run(store, "child-a", "grandchild")

    cancelled = await cancel_run(store, "root")

    assert set(cancelled) == {"root", "child-a", "child-b", "grandchild"}
    for run_id in cancelled:
        run = await store.get_run(run_id)
        assert run.status is RunStatus.CANCELLED


async def test_cancel_leaves_already_terminal_runs_alone(store):
    await store.create_run(
        Run(run_id="root", workflow_name="w", status=RunStatus.RUNNING)
    )
    await store.create_run(
        Run(
            run_id="finished-child",
            workflow_name="w",
            status=RunStatus.COMPLETED,
            result="done",
            parent_run_id="root",
        )
    )

    cancelled = await cancel_run(store, "root")

    assert cancelled == ["root"]
    finished = await store.get_run("finished-child")
    assert finished.status is RunStatus.COMPLETED
    assert finished.result == "done"


async def test_cancel_unrelated_run_is_not_affected(store):
    await store.create_run(
        Run(run_id="root", workflow_name="w", status=RunStatus.RUNNING)
    )
    await store.create_run(
        Run(run_id="unrelated", workflow_name="w", status=RunStatus.RUNNING)
    )

    await cancel_run(store, "root")

    unrelated = await store.get_run("unrelated")
    assert unrelated.status is RunStatus.RUNNING


async def test_cancel_unknown_run_raises(store):
    with pytest.raises(RunNotFoundError):
        await cancel_run(store, "nonexistent")


async def test_a_cancelled_run_cannot_be_resumed(store):
    @workflow(name="cancel_target")
    async def cancel_target(ctx):
        return await ctx.step("a", lambda: "a")

    run_id = await start_run(store, "cancel_target")
    await cancel_run(store, run_id)

    with pytest.raises(RunCancelledError):
        await execute_run(store, run_id)


async def test_in_flight_run_notices_cancellation_at_the_next_step_boundary(store):
    """A run cancelled mid-flight (by "another process") stops before its
    next real step, not only on its next fresh invocation."""
    should_cancel_after_first_step = {"v": False}

    @workflow(name="long_running")
    async def long_running(ctx):
        a = await ctx.step("a", lambda: "a")
        if should_cancel_after_first_step["v"]:
            await cancel_run(store, ctx.run_id)
        b = await ctx.step("b", lambda: "b")
        return [a, b]

    run_id = await start_run(store, "long_running")
    should_cancel_after_first_step["v"] = True

    with pytest.raises(RunCancelledError):
        await execute_run(store, run_id)

    run = await store.get_run(run_id)
    assert run.status is RunStatus.CANCELLED

    log = await store.read_log(run_id)
    step_ids = {e.step_id for e in log}
    assert "a" in step_ids
    assert "b" not in step_ids
