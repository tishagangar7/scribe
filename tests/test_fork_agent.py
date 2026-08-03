"""DA-212 -- N candidate patches via fork: first passing candidate wins,
siblings cancelled. Uses a fake run_test (not real Docker) to keep this
fast; agent/graph.py's `_never` guards prove the shared prefix (retrieve,
plan, code:candidates) is never re-executed by a forked child -- if it
were, the test would fail with an AssertionError, not a wrong assertion.
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from agent.graph import StubLLMClient, run_agent_with_fork, run_child_candidate
from agent.sandbox import SandboxResult
from scribe.decorators import clear_registry, execute_run, start_run, workflow
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


def _toy_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    return repo


def _fake_run_test_matching(good_patch: str):
    def run(repo_path, patch, test_command):
        passed = patch == good_patch
        return SandboxResult(
            passed=passed,
            exit_code=0 if passed else 1,
            stdout="ok" if passed else "",
            stderr="" if passed else "fail",
        )

    return run


def _fake_run_test_winner_fast_others_slow(good_patch: str):
    """The winner returns instantly; every other candidate blocks on a real
    (cancellable) asyncio.sleep -- long enough that the race's early-exit
    genuinely finds them still in flight and cancels them for real, rather
    than racing against candidates that would have finished anyway."""

    async def run(repo_path, patch, test_command):
        if patch == good_patch:
            return SandboxResult(passed=True, exit_code=0, stdout="ok", stderr="")
        await asyncio.sleep(5)
        return SandboxResult(passed=False, exit_code=1, stdout="", stderr="fail")

    return run


def _register(store, repo, llm, run_test):
    @workflow(name="agent_fork")
    async def agent_fork_workflow(ctx, candidate_index=None):
        if candidate_index is None:
            result = await run_agent_with_fork(
                store,
                ctx,
                issue="add() subtracts",
                repo=str(repo),
                llm=llm,
                n_candidates=3,
                run_test=run_test,
            )
            return dataclasses.asdict(result)
        return await run_child_candidate(
            ctx,
            candidate_index,
            issue="add() subtracts",
            repo=str(repo),
            llm=llm,
            run_test=run_test,
        )


async def test_first_passing_candidate_wins(store, tmp_path):
    repo = _toy_repo(tmp_path)
    candidates = ["bad-patch-0", "bad-patch-1", "good-patch-2"]
    llm = StubLLMClient(
        responses={"plan": "a plan", "critic": "fine"},
        many_responses={"code": candidates},
    )
    run_test = _fake_run_test_matching("good-patch-2")
    _register(store, repo, llm, run_test)

    run_id = await start_run(store, "agent_fork")
    result = await execute_run(store, run_id)

    assert result["passed"] is True
    assert result["winning_patch"] == "good-patch-2"
    assert result["candidates_tried"] == 3


async def test_losing_siblings_still_in_flight_are_genuinely_cancelled(store, tmp_path):
    """The winner finishes instantly; the losers are still asleep (a real,
    cancellable await) when the race ends -- proving cancellation actually
    stops in-flight work, not just bookkeeping runs that finished anyway."""
    repo = _toy_repo(tmp_path)
    candidates = ["bad-patch-0", "bad-patch-1", "good-patch-2"]
    llm = StubLLMClient(
        responses={"plan": "a plan", "critic": "fine"},
        many_responses={"code": candidates},
    )
    run_test = _fake_run_test_winner_fast_others_slow("good-patch-2")
    _register(store, repo, llm, run_test)

    run_id = await start_run(store, "agent_fork")
    result = await execute_run(store, run_id)

    winner_id = result["winning_child_run_id"]
    all_ids = await store.list_run_ids()
    child_ids = [rid for rid in all_ids if rid != run_id]
    assert len(child_ids) == 3

    for cid in child_ids:
        run = await store.get_run(cid)
        if cid == winner_id:
            assert run.status is RunStatus.COMPLETED
        else:
            assert run.status is RunStatus.CANCELLED, (
                f"{cid} should have been stopped mid-flight, not left to finish"
            )


async def test_no_candidate_passes(store, tmp_path):
    repo = _toy_repo(tmp_path)
    candidates = ["bad-patch-0", "bad-patch-1", "bad-patch-2"]
    llm = StubLLMClient(
        responses={"plan": "a plan", "critic": "fine"},
        many_responses={"code": candidates},
    )
    run_test = _fake_run_test_matching("no-patch-matches-this")
    _register(store, repo, llm, run_test)

    run_id = await start_run(store, "agent_fork")
    result = await execute_run(store, run_id)

    assert result["passed"] is False
    assert result["winning_patch"] is None
    assert result["winning_child_run_id"] is None
