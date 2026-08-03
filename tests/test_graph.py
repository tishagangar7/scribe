"""DA-210/211 -- the agent graph's control flow, durability, and step cap.

Two kinds of tests here. Fast ones use a fake `run_test` callable so they
never touch Docker -- they prove the *graph mechanics*: the retry loop,
ctx.step integration (crash-and-resume across the whole multi-node
pipeline, not just a single step), and the step cap. One slow test
(`test_full_graph_against_real_docker_sandbox`) uses the real Sprint 1
sandbox against a real toy repo -- DA-211's literal "run one issue through
the full graph end to end," with everything real except the LLM, which is
stubbed since no API key is configured.
"""

from __future__ import annotations

import dataclasses

import pytest
from langgraph.errors import GraphRecursionError

from agent.graph import StubLLMClient, run_agent
from agent.sandbox import SandboxResult
from scribe.decorators import clear_registry, execute_run, start_run, workflow
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
    (repo / "check.py").write_text(
        "import sys\nfrom calc import add\nsys.exit(0 if add(2, 3) == 5 else 1)\n"
    )
    return repo


def _fake_test_runner(results):
    it = iter(results)

    def run(repo_path, patch, test_command):
        return next(it)

    return run


async def test_graph_retries_on_test_failure_and_succeeds_on_second_attempt(
    store, tmp_path
):
    repo = _toy_repo(tmp_path)
    llm = StubLLMClient(
        {
            "plan": "fix the subtraction bug in add()",
            "code": ["a patch that doesn't fix it", "a patch that fixes it"],
            "critic": "looks reasonable",
        }
    )
    fake_run_test = _fake_test_runner(
        [
            SandboxResult(passed=False, exit_code=1, stdout="", stderr="fail"),
            SandboxResult(passed=True, exit_code=0, stdout="ok", stderr=""),
        ]
    )

    @workflow(name="agent")
    async def agent_workflow(ctx):
        result = await run_agent(
            ctx,
            issue="add() subtracts",
            repo=str(repo),
            llm=llm,
            run_test=fake_run_test,
        )
        return dataclasses.asdict(result)

    run_id = await start_run(store, "agent")
    result = await execute_run(store, run_id)

    assert result["passed"] is True
    assert result["attempts"] == 2
    assert result["patch"] == "a patch that fixes it"


async def test_graph_resumes_after_a_crash_without_redoing_completed_nodes(
    store, tmp_path
):
    """The central claim: ctx.step inside graph nodes gives the WHOLE
    multi-node pipeline crash recovery, not just a single flat workflow."""
    repo = _toy_repo(tmp_path)
    call_counts = {"plan": 0, "code": 0, "critic": 0}
    code_attempts = {"n": 0}

    class FlakyOnFirstCodeCallLLM:
        async def complete(self, node: str, prompt: str) -> str:
            call_counts[node] += 1
            if node == "code" and code_attempts["n"] == 0:
                code_attempts["n"] += 1
                raise ConnectionError("simulated rate limit")
            if node == "plan":
                return "a plan"
            if node == "code":
                return "a patch"
            return "looks fine"

    llm = FlakyOnFirstCodeCallLLM()
    fake_run_test = _fake_test_runner(
        [SandboxResult(passed=True, exit_code=0, stdout="ok", stderr="")]
    )

    @workflow(name="agent_crash")
    async def agent_workflow(ctx):
        result = await run_agent(
            ctx, issue="fix add", repo=str(repo), llm=llm, run_test=fake_run_test
        )
        return dataclasses.asdict(result)

    run_id = await start_run(store, "agent_crash")
    with pytest.raises(ConnectionError):
        await execute_run(store, run_id)

    # "plan" already completed and recorded before "code" raised.
    assert call_counts["plan"] == 1
    assert call_counts["code"] == 1  # the failed attempt
    assert call_counts["critic"] == 0

    result = await execute_run(store, run_id)

    assert result["passed"] is True
    # Resuming re-invokes the graph from scratch, but "plan" replays from
    # the log instead of calling the LLM again.
    assert call_counts["plan"] == 1
    assert call_counts["code"] == 2  # 1 failed + 1 real retry
    assert call_counts["critic"] == 1


async def test_step_cap_halts_an_always_failing_loop(store, tmp_path):
    repo = _toy_repo(tmp_path)
    llm = StubLLMClient({"plan": "plan", "code": "broken patch", "critic": "not great"})
    fake_run_test = _fake_test_runner(
        [SandboxResult(passed=False, exit_code=1, stdout="", stderr="fail")] * 20
    )

    @workflow(name="agent_capped")
    async def agent_workflow(ctx):
        result = await run_agent(
            ctx,
            issue="unfixable",
            repo=str(repo),
            llm=llm,
            run_test=fake_run_test,
            step_cap=6,
        )
        return dataclasses.asdict(result)

    run_id = await start_run(store, "agent_capped")
    with pytest.raises(GraphRecursionError):
        await execute_run(store, run_id)


async def test_full_graph_against_real_docker_sandbox(store, tmp_path):
    """DA-211: one issue through the full graph, everything real except the LLM."""
    repo = _toy_repo(tmp_path)

    llm = StubLLMClient(
        {
            "plan": "add() should return a + b, not a - b",
            "code": [
                "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
                " def add(a, b):\n-    return a - b\n+    return a + b - 1\n",
                "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
                " def add(a, b):\n-    return a - b\n+    return a + b\n",
            ],
            "critic": "the first patch still looks off, but proceeding anyway",
        }
    )

    @workflow(name="agent_real")
    async def agent_workflow(ctx):
        result = await run_agent(
            ctx, issue="add() subtracts instead of adding", repo=str(repo), llm=llm
        )
        return dataclasses.asdict(result)

    run_id = await start_run(store, "agent_real")
    result = await execute_run(store, run_id)

    assert result["passed"] is True
    assert result["attempts"] == 2
