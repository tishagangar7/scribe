"""DA-127/129 -- ctx.llm_step enforces the run's token/cost budget.

DA-129: a run with a tiny budget halts cleanly, then completes after the
cap is raised.
"""

from __future__ import annotations

import pytest

from scribe.decorators import clear_registry, execute_run, start_run, workflow
from scribe.errors import BudgetExceededError, DuplicateStepError
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


async def test_tiny_token_budget_halts_cleanly_then_completes_after_raise(store):
    @workflow(name="budgeted")
    async def budgeted(ctx):
        a = await ctx.llm_step("a", lambda: ("result-a", 10, 0.0))
        b = await ctx.llm_step("b", lambda: ("result-b", 5, 0.0))
        return [a, b]

    run_id = await start_run(store, "budgeted", token_budget=10)

    with pytest.raises(BudgetExceededError):
        await execute_run(store, run_id)

    run = await store.get_run(run_id)
    assert run.status is RunStatus.BUDGET_EXCEEDED
    assert run.tokens_used == 10

    # Raise the cap and resume with the same run_id -- "a" must not re-spend.
    run.token_budget = 100
    await store.update_run(run)

    result = await execute_run(store, run_id)
    assert result == ["result-a", "result-b"]

    run = await store.get_run(run_id)
    assert run.status is RunStatus.COMPLETED
    assert run.tokens_used == 15


async def test_cost_budget_is_enforced_independently_of_token_budget(store):
    @workflow(name="cost_budgeted")
    async def cost_budgeted(ctx):
        await ctx.llm_step("a", lambda: ("a", 1, 0.50))
        return await ctx.llm_step("b", lambda: ("b", 1, 0.50))

    run_id = await start_run(store, "cost_budgeted", cost_budget_usd=0.5)

    with pytest.raises(BudgetExceededError) as exc:
        await execute_run(store, run_id)
    assert exc.value.resource == "cost_usd"

    run = await store.get_run(run_id)
    assert run.status is RunStatus.BUDGET_EXCEEDED
    assert run.cost_used_usd == 0.5


async def test_a_generous_budget_never_trips(store):
    @workflow(name="cheap")
    async def cheap(ctx):
        return await ctx.llm_step("a", lambda: ("ok", 1, 0.001))

    run_id = await start_run(store, "cheap", token_budget=1_000_000)
    result = await execute_run(store, run_id)

    assert result == "ok"
    run = await store.get_run(run_id)
    assert run.status is RunStatus.COMPLETED


async def test_no_budget_set_means_unlimited(store):
    @workflow(name="unbudgeted")
    async def unbudgeted(ctx):
        return await ctx.llm_step("a", lambda: ("ok", 10**9, 10**6))

    run_id = await start_run(store, "unbudgeted")
    result = await execute_run(store, run_id)
    assert result == "ok"


async def test_llm_step_replay_never_respends(store):
    calls = {"n": 0}

    @workflow(name="replayed_llm")
    async def replayed_llm(ctx):
        def fn():
            calls["n"] += 1
            return ("ok", 5, 0.0)

        return await ctx.llm_step("a", fn)

    run_id = await start_run(store, "replayed_llm", token_budget=1000)
    await execute_run(store, run_id)
    assert calls["n"] == 1

    from scribe.models import RunStatus as RS

    run = await store.get_run(run_id)
    run.status = RS.RUNNING
    await store.update_run(run)

    await execute_run(store, run_id)
    assert calls["n"] == 1, "replay must not re-invoke the paid call"

    run = await store.get_run(run_id)
    assert run.tokens_used == 5, "usage must not double-count across replay"


async def test_duplicate_step_id_across_step_and_llm_step_rejected(store):
    @workflow(name="mixed_dupe")
    async def mixed_dupe(ctx):
        await ctx.step("a", lambda: 1)
        await ctx.llm_step("a", lambda: (2, 1, 0.0))

    run_id = await start_run(store, "mixed_dupe")
    with pytest.raises(DuplicateStepError):
        await execute_run(store, run_id)
