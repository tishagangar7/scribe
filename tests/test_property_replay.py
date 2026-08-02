"""DA-123 -- property test: replay matches recording over generated workflow shapes.

Hypothesis generates workflow "shapes" -- sequences of plain steps, gathers,
and branches, nested up to a couple of levels deep -- interpreted by one
generic workflow, `shape_runner`. For each generated shape: record it, then
replay it through a Context that can only serve steps from the log (see
scribe/replay.py), and assert the replayed result and step sequence are
byte-for-byte identical to what was recorded. Across >=100 shapes, this is
much stronger evidence than any hand-written example that replay is
correct for arbitrary control flow, not just the couple of shapes a human
thought to write down.
"""

from __future__ import annotations

import asyncio
import itertools
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from scribe.decorators import clear_registry, execute_run, start_run, workflow
from scribe.models import EventType
from scribe.replay import replay
from scribe.store import SQLiteStore

_ATOM = st.one_of(
    st.booleans(),
    st.integers(min_value=-1000, max_value=1000),
    st.text(max_size=5),
)


def _ops_strategy(max_depth: int) -> st.SearchStrategy[list[dict[str, Any]]]:
    leaf = st.builds(lambda v: {"type": "step", "value": v}, _ATOM)
    gather = st.builds(
        lambda vs: {"type": "gather", "members": vs},
        st.lists(_ATOM, min_size=1, max_size=3),
    )
    choices = [leaf, gather]
    if max_depth > 0:
        branch = st.builds(
            lambda cond, then_ops, else_ops: {
                "type": "branch",
                "cond": cond,
                "then": then_ops,
                "else": else_ops,
            },
            st.booleans(),
            _ops_strategy(max_depth - 1),
            _ops_strategy(max_depth - 1),
        )
        choices.append(branch)
    return st.lists(st.one_of(*choices), min_size=1, max_size=4)


async def _run_ops(
    ctx: Any, ops: list[dict[str, Any]], counter: itertools.count[int]
) -> list[Any]:
    results: list[Any] = []
    for op in ops:
        if op["type"] == "step":
            i = next(counter)
            value = op["value"]
            results.append(await ctx.step(f"step:{i}", lambda v=value: v))
        elif op["type"] == "gather":
            specs = []
            for value in op["members"]:
                i = next(counter)
                specs.append((f"gather-step:{i}", lambda v=value: v))
            results.append(await ctx.gather(*specs))
        elif op["type"] == "branch":
            i = next(counter)
            cond = op["cond"]
            branch_result = await ctx.step(f"cond:{i}", lambda c=cond: c)
            branch_ops = op["then"] if branch_result else op["else"]
            results.append(await _run_ops(ctx, branch_ops, counter))
    return results


async def _record_and_replay(ops: list[dict[str, Any]]) -> None:
    clear_registry()

    @workflow(name="shape_runner")
    async def shape_runner(ctx: Any, ops: list[dict[str, Any]]) -> list[Any]:
        return await _run_ops(ctx, ops, itertools.count())

    store = await SQLiteStore.open(":memory:")
    try:
        run_id = await start_run(store, "shape_runner", input={"ops": ops})
        original_result = await execute_run(store, run_id)

        log = await store.read_log(run_id)
        original_sequence = [
            e.step_id for e in log if e.event_type is EventType.STEP_COMPLETED
        ]

        run = await store.get_run(run_id)
        assert run is not None
        from scribe.models import RunStatus

        run.status = RunStatus.RUNNING
        await store.update_run(run)

        result = await replay(store, run_id)

        assert result.passed, f"replay diverged from recording: {result.error}"
        assert result.result == original_result
        assert result.step_sequence == original_sequence
    finally:
        await store.close()


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(ops=_ops_strategy(max_depth=2))
def test_replay_matches_recording_across_generated_shapes(ops):
    asyncio.run(_record_and_replay(ops))
