"""DA-126 -- `da replay --all` reports pass/fail and exits accordingly.

`main()` drives its own event loop via `asyncio.run()`, so these tests stay
plain `def`s and use `asyncio.run()` themselves for setup -- calling `main()`
from inside a pytest-asyncio-managed coroutine would nest event loops.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest

from scribe.cli import main
from scribe.decorators import clear_registry, execute_run, start_run, workflow
from scribe.store import SQLiteStore


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


def test_all_healthy_runs_exit_zero(tmp_path, capsys):
    db = tmp_path / "healthy.db"

    @workflow(name="ok")
    async def ok(ctx):
        return await ctx.step("s", lambda: "done")

    async def setup():
        store = await SQLiteStore.open(db)
        for _ in range(3):
            run_id = await start_run(store, "ok")
            await execute_run(store, run_id)
        await store.close()

    asyncio.run(setup())

    code = main(["replay", "--all", "--db", str(db)])
    out = capsys.readouterr().out

    assert code == 0
    assert "3/3 runs replayed identically." in out


def test_a_failed_run_exits_nonzero(tmp_path, capsys):
    db = tmp_path / "mixed.db"

    @workflow(name="ok")
    async def ok(ctx):
        return await ctx.step("s", lambda: "done")

    @workflow(name="broken")
    async def broken(ctx):
        await ctx.step("a", lambda: "a")

        def boom():
            raise RuntimeError("nope")

        return await ctx.step("b", boom)

    async def setup() -> str:
        store = await SQLiteStore.open(db)
        good_run = await start_run(store, "ok")
        await execute_run(store, good_run)

        bad_run = await start_run(store, "broken")
        try:
            await execute_run(store, bad_run)
        except RuntimeError:
            pass
        await store.close()
        return bad_run

    bad_run = asyncio.run(setup())

    code = main(["replay", "--all", "--db", str(db)])
    out = capsys.readouterr().out

    assert code == 1
    assert "1/2 runs replayed identically." in out
    assert f"FAILED {bad_run}" in out


def test_missing_all_flag_errors():
    with pytest.raises(SystemExit):
        main(["replay", "--db", "x.db"])


def test_import_flag_registers_workflows_in_a_fresh_registry(tmp_path, capsys):
    """The registry is in-process only -- a real `da` invocation starts fresh
    and must be told which module defines the workflows it's replaying."""
    db = tmp_path / "imported.db"
    workflow_module = tmp_path / "fixture_workflow.py"
    workflow_module.write_text(
        "from scribe.decorators import workflow\n\n"
        "@workflow(name='from_module')\n"
        "async def from_module(ctx):\n"
        "    return await ctx.step('s', lambda: 'ok')\n"
    )

    async def setup() -> None:
        import sys

        sys.path.insert(0, str(tmp_path))
        try:
            importlib.import_module("fixture_workflow")
            store = await SQLiteStore.open(db)
            run_id = await start_run(store, "from_module")
            await execute_run(store, run_id)
            await store.close()
        finally:
            sys.path.remove(str(tmp_path))

    asyncio.run(setup())

    # Simulate a genuinely fresh process: nothing registered, and the module
    # itself not yet imported (otherwise --import would just hit the
    # sys.modules cache and never re-run its @workflow registration).
    clear_registry()
    import sys

    sys.modules.pop("fixture_workflow", None)
    sys.path.insert(0, str(tmp_path))
    try:
        code = main(
            ["replay", "--all", "--db", str(db), "--import", "fixture_workflow"]
        )
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("fixture_workflow", None)

    out = capsys.readouterr().out
    assert code == 0
    assert "1/1 runs replayed identically." in out


def test_fork_creates_a_child_and_prints_its_run_id(tmp_path, capsys):
    db = tmp_path / "fork.db"

    @workflow(name="steppy")
    async def steppy(ctx):
        await ctx.step("a", lambda: "a")
        return await ctx.step("b", lambda: "b")

    async def setup() -> tuple[str, int]:
        store = await SQLiteStore.open(db)
        run_id = await start_run(store, "steppy")
        await execute_run(store, run_id)
        event = await store.get_completed_step(run_id, "a")
        await store.close()
        return run_id, event.seq

    run_id, at_seq = asyncio.run(setup())

    code = main(["fork", run_id, "--at", str(at_seq), "--db", str(db)])
    out = capsys.readouterr().out.strip()

    assert code == 0
    assert out  # the printed child run_id
    assert out != run_id
