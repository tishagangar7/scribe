"""DA-210/211 -- the multi-agent graph: retrieve -> plan -> code -> critic -> test.

LangGraph owns *control flow* (which node runs next, the retry edge from
`test` back to `plan`, the step cap via `recursion_limit`). `scribe`'s
`Context` owns *durability* -- every node body's real work happens inside a
`ctx.step`, so a crash mid-graph resumes by simply re-invoking
`graph.ainvoke` again with the same `ctx`: already-completed node work
replays from the log at $0 instead of re-running, and LangGraph naturally
re-derives the same path because every value its conditional edge reads
(`test_passed`) itself came from a `ctx.step`. The two systems compose
without either needing to know about the other's internals.

The LLM is behind `LLMClient` so this graph runs for real -- real retrieval,
real Docker sandbox test execution -- without spending money or needing an
API key: `StubLLMClient` serves scripted, deterministic responses. Swapping
in a real client later is a one-line change at the call site.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, StateGraph

from agent.retrieval import Chunk
from agent.sandbox import SandboxResult, run_in_sandbox
from agent.search import retrieve as hybrid_retrieve
from scribe.cancel import cancel_run
from scribe.context import Context
from scribe.decorators import execute_run
from scribe.fork import fork
from scribe.store import Store

TestRunner = Callable[[Path, str | None, str], SandboxResult]


class LLMClient(Protocol):
    async def complete(self, node: str, prompt: str) -> str: ...
    async def complete_many(self, node: str, prompt: str, n: int) -> list[str]: ...


class StubLLMClient:
    """Scripted, deterministic responses -- no network call, no cost.

    `responses[node]` is either a fixed string (every attempt gets the same
    answer) or a list consumed one element per call to that node, so a test
    can script "the first patch is broken, the second one fixes it."
    `many_responses[node]` backs `complete_many` -- a fixed list of N
    candidates returned by one call, for the fork-based multi-candidate path.
    """

    def __init__(
        self,
        responses: dict[str, str | list[str]],
        many_responses: dict[str, list[str]] | None = None,
    ) -> None:
        self._responses = responses
        self._many_responses = many_responses or {}
        self._calls: dict[str, int] = {}

    async def complete(self, node: str, prompt: str) -> str:
        response = self._responses[node]
        if isinstance(response, str):
            return response
        i = self._calls.get(node, 0)
        self._calls[node] = i + 1
        return response[min(i, len(response) - 1)]

    async def complete_many(self, node: str, prompt: str, n: int) -> list[str]:
        return self._many_responses[node][:n]


class AgentState(TypedDict, total=False):
    issue: str
    repo: str
    test_command: str
    attempt: int
    chunks: list[dict[str, Any]]
    plan: str
    patch: str
    critic_notes: str
    test_passed: bool
    test_output: str


@dataclasses.dataclass(frozen=True)
class AgentResult:
    passed: bool
    attempts: int
    patch: str
    test_output: str


def _plan_prompt(issue: str, chunks: list[dict[str, Any]]) -> str:
    context = "\n\n".join(
        f"# {c['file_path']}:{c['name']}\n{c['text']}" for c in chunks
    )
    return f"Issue:\n{issue}\n\nRelevant code:\n{context}\n\nWrite a plan to fix this."


def _code_prompt(issue: str, plan: str, chunks: list[dict[str, Any]]) -> str:
    return f"Issue:\n{issue}\n\nPlan:\n{plan}\n\nWrite a unified diff patch."


def _critic_prompt(patch: str) -> str:
    return f"Review this patch for obvious problems:\n{patch}"


def build_graph(
    ctx: Context,
    llm: LLMClient,
    *,
    run_test: TestRunner = run_in_sandbox,
) -> Any:
    """Compile the retrieve->plan->code->critic->test graph against `ctx`.

    Every node's real work is wrapped in `ctx.step` with an id that includes
    the current retry `attempt`, so repeated trips around the retry loop get
    distinct, stable step_ids instead of colliding as duplicates.
    """

    async def retrieve_node(state: AgentState) -> dict[str, Any]:
        def do_retrieve() -> list[dict[str, Any]]:
            chunks: list[Chunk] = hybrid_retrieve(
                state["issue"], Path(state["repo"]), k=5
            )
            return [dataclasses.asdict(c) for c in chunks]

        chunks = await ctx.step("retrieve", do_retrieve)
        return {"chunks": chunks, "attempt": 0}

    async def plan_node(state: AgentState) -> dict[str, Any]:
        attempt = state.get("attempt", 0)
        prompt = _plan_prompt(state["issue"], state["chunks"])
        plan = await ctx.step(f"plan:{attempt}", lambda: llm.complete("plan", prompt))
        return {"plan": plan}

    async def code_node(state: AgentState) -> dict[str, Any]:
        attempt = state.get("attempt", 0)
        prompt = _code_prompt(state["issue"], state["plan"], state["chunks"])
        patch = await ctx.step(f"code:{attempt}", lambda: llm.complete("code", prompt))
        return {"patch": patch}

    async def critic_node(state: AgentState) -> dict[str, Any]:
        attempt = state.get("attempt", 0)
        prompt = _critic_prompt(state["patch"])
        notes = await ctx.step(
            f"critic:{attempt}", lambda: llm.complete("critic", prompt)
        )
        return {"critic_notes": notes}

    async def test_node(state: AgentState) -> dict[str, Any]:
        attempt = state.get("attempt", 0)

        def do_test() -> dict[str, Any]:
            result = run_test(
                Path(state["repo"]),
                state["patch"],
                state.get("test_command", "python check.py"),
            )
            return dataclasses.asdict(result)

        result = await ctx.step(f"test:{attempt}", do_test)
        return {
            "test_passed": result["passed"],
            "test_output": result["stdout"] + result["stderr"],
            "attempt": attempt + 1,
        }

    def route_after_test(state: AgentState) -> str:
        return "done" if state["test_passed"] else "retry"

    graph = StateGraph(AgentState)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("plan", plan_node)
    graph.add_node("code", code_node)
    graph.add_node("critic", critic_node)
    graph.add_node("test", test_node)

    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "plan")
    graph.add_edge("plan", "code")
    graph.add_edge("code", "critic")
    graph.add_edge("critic", "test")
    graph.add_conditional_edges(
        "test", route_after_test, {"done": END, "retry": "plan"}
    )

    return graph.compile()


async def run_agent(
    ctx: Context,
    issue: str,
    repo: str,
    llm: LLMClient,
    *,
    test_command: str = "python check.py",
    step_cap: int = 12,
    run_test: TestRunner = run_in_sandbox,
) -> AgentResult:
    """Run one issue through the full graph, durably, to a pass or step cap."""
    graph = build_graph(ctx, llm, run_test=run_test)
    final_state: AgentState = await graph.ainvoke(
        {"issue": issue, "repo": repo, "test_command": test_command},
        config={"recursion_limit": step_cap},
    )
    return AgentResult(
        passed=final_state["test_passed"],
        attempts=final_state["attempt"],
        patch=final_state["patch"],
        test_output=final_state["test_output"],
    )


@dataclasses.dataclass(frozen=True)
class ForkAgentResult:
    passed: bool
    winning_patch: str | None
    winning_child_run_id: str | None
    candidates_tried: int


def _never(step_id: str) -> Callable[[], Any]:
    def _raise() -> Any:
        raise AssertionError(
            f"step {step_id!r} executed for real -- this workflow should only "
            f"run as a forked child of a run that already completed it"
        )

    return _raise


async def run_child_candidate(
    ctx: Context,
    candidate_index: int,
    issue: str,
    repo: str,
    llm: LLMClient,
    *,
    test_command: str = "python check.py",
    run_test: TestRunner = run_in_sandbox,
) -> dict[str, Any]:
    """A forked child's continuation: critic + test on its one assigned candidate.

    Re-requests the exact step_ids `run_agent_with_fork` used for the shared
    prefix (retrieve, plan, code:candidates). For a properly-forked child
    these always resolve from the inherited prefix; the bodies passed here
    only run if this is somehow invoked without having actually been forked
    from a run that completed them, which is a caller error, not a normal
    path -- hence `_never` rather than real implementations.
    """
    await ctx.step("retrieve", _never("retrieve"))
    await ctx.step("plan", _never("plan"))
    candidates: list[str] = await ctx.step("code:candidates", _never("code:candidates"))

    patch = candidates[candidate_index]
    critic_prompt = _critic_prompt(patch)
    await ctx.step(
        f"critic:{candidate_index}", lambda: llm.complete("critic", critic_prompt)
    )

    async def do_test() -> dict[str, Any]:
        outcome = run_test(Path(repo), patch, test_command)
        if inspect.isawaitable(outcome):
            outcome = await outcome
        return dataclasses.asdict(outcome)

    result = await ctx.step(f"test:{candidate_index}", do_test)
    return {
        "passed": result["passed"],
        "patch": patch,
        "test_output": result["stdout"] + result["stderr"],
    }


async def run_agent_with_fork(
    store: Store,
    ctx: Context,
    issue: str,
    repo: str,
    llm: LLMClient,
    *,
    n_candidates: int = 3,
    test_command: str = "python check.py",
    run_test: TestRunner = run_in_sandbox,
) -> ForkAgentResult:
    """DA-212: generate N candidate patches, fork a child per candidate, run
    their tests concurrently, and let the first passing candidate (by
    index) win -- the rest are cancelled.

    retrieve/plan/code:candidates run once, here, on the parent -- forked
    children inherit them at $0 rather than re-deriving anything. The
    fork-and-race itself is one atomic ctx.step, the same trade-off
    `ctx.gather` makes: a crash mid-race re-runs the whole race on resume
    (fresh child run_ids) rather than resuming individual children, which
    keeps this consistent with how every other `ctx.step` already behaves.
    """

    def do_retrieve() -> list[dict[str, Any]]:
        chunks: list[Chunk] = hybrid_retrieve(issue, Path(repo), k=5)
        return [dataclasses.asdict(c) for c in chunks]

    chunks = await ctx.step("retrieve", do_retrieve)
    plan_prompt = _plan_prompt(issue, chunks)
    plan = await ctx.step("plan", lambda: llm.complete("plan", plan_prompt))
    code_prompt = _code_prompt(issue, plan, chunks)
    candidates: list[str] = await ctx.step(
        "code:candidates",
        lambda: llm.complete_many("code", code_prompt, n_candidates),
    )

    async def do_race() -> dict[str, Any]:
        candidates_event = await store.get_completed_step(ctx.run_id, "code:candidates")
        assert candidates_event is not None
        fork_at = candidates_event.seq

        child_ids = [
            f"{ctx.run_id}-candidate-{i}-{uuid.uuid4().hex[:6]}"
            for i in range(len(candidates))
        ]
        for i, cid in enumerate(child_ids):
            await fork(
                store,
                ctx.run_id,
                fork_at,
                child_run_id=cid,
                input={"candidate_index": i},
            )

        tasks = {
            asyncio.create_task(execute_run(store, cid)): i
            for i, cid in enumerate(child_ids)
        }
        pending = set(tasks)
        winner_index: int | None = None
        winner: dict[str, Any] | None = None

        # Stop as soon as one candidate passes -- don't wait for the rest.
        while pending and winner_index is None:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                try:
                    result = task.result()
                except (Exception, asyncio.CancelledError):
                    continue
                if isinstance(result, dict) and result.get("passed"):
                    winner_index = tasks[task]
                    winner = result
                    break

        # Whatever's still genuinely in flight gets cancelled for real, both
        # the asyncio task and the run's own status.
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for i, cid in enumerate(child_ids):
            if i != winner_index:
                await cancel_run(store, cid)

        return {
            "winner_index": winner_index,
            "winning_child_run_id": child_ids[winner_index]
            if winner_index is not None
            else None,
            "winning_patch": winner["patch"] if winner else None,
        }

    race_result = await ctx.step("race", do_race)

    return ForkAgentResult(
        passed=race_result["winner_index"] is not None,
        winning_patch=race_result["winning_patch"],
        winning_child_run_id=race_result["winning_child_run_id"],
        candidates_tried=len(candidates),
    )
