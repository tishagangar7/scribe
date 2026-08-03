"""DA-130/131/132 -- fork: branch a run's execution from a point in its history.

`fork(store, run_id, at_seq)` creates a child that inherits every step
completed at or before `at_seq`, then continues independently -- without
copying a single row. `Context` resolves the inherited prefix by walking
`parent_run_id`/`forked_at_seq` once, at construction (`_inherited_events`
in `scribe/context.py`), building a dict of step_id -> the ancestor's
Event; `Context.step`/`llm_step` fall back to that dict when a step_id
isn't in the run's own (physically much shorter) log. The child's own log
only ever grows with what the child itself executes after the fork point.

Recursive forks (a fork of a fork) work for free: `_inherited_events`
recurses up `parent_run_id` however many levels deep, concatenating each
ancestor's own contribution truncated to that link's `forked_at_seq`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from scribe.errors import RunNotFoundError
from scribe.models import Run, RunStatus
from scribe.store import Store

_NO_OVERRIDE = object()


async def fork(
    store: Store,
    run_id: str,
    at_seq: int,
    *,
    child_run_id: str | None = None,
    input: Any = _NO_OVERRIDE,
) -> str:
    """Create a child of `run_id` inheriting every step completed at seq <= at_seq.

    The child starts PENDING. Run it like any other run (`execute_run`):
    its workflow function re-invokes from line one, and every step in the
    inherited prefix resolves from the parent's log at $0 -- the same
    replay mechanism, just reading a different run's log for the prefix.

    `input` defaults to a copy of the parent's input; pass an explicit
    value to give the child different arguments (e.g. "which candidate
    patch am I testing") while still running the same registered workflow
    function and sharing the same inherited prefix.
    """
    parent = await store.get_run(run_id)
    if parent is None:
        raise RunNotFoundError(run_id)

    cid = child_run_id or f"{run_id}-fork-{uuid.uuid4().hex[:8]}"
    child = Run(
        run_id=cid,
        workflow_name=parent.workflow_name,
        workflow_version=parent.workflow_version,
        input=parent.input if input is _NO_OVERRIDE else input,
        status=RunStatus.PENDING,
        parent_run_id=run_id,
        forked_at_seq=at_seq,
        token_budget=parent.token_budget,
        cost_budget_usd=parent.cost_budget_usd,
    )
    await store.create_run(child)
    return cid


async def children_map(store: Store) -> dict[str, list[str]]:
    """run_id -> direct children run_ids, across every run in the store."""
    all_ids = await store.list_run_ids()
    children: dict[str, list[str]] = {}
    for rid in all_ids:
        r = await store.get_run(rid)
        assert r is not None
        if r.parent_run_id is not None:
            children.setdefault(r.parent_run_id, []).append(rid)
    return children


@dataclass(frozen=True)
class ForkNode:
    """One run in a lineage tree, with its children nested inside."""

    run_id: str
    forked_at_seq: int | None
    children: list[ForkNode] = field(default_factory=list)


async def fork_tree(store: Store, run_id: str) -> ForkNode:
    """The lineage tree rooted at `run_id`: every run forked from it, recursively."""
    root = await store.get_run(run_id)
    if root is None:
        raise RunNotFoundError(run_id)

    by_parent = await children_map(store)

    async def build(rid: str, forked_at_seq: int | None) -> ForkNode:
        nodes = []
        for child_id in by_parent.get(rid, []):
            child = await store.get_run(child_id)
            assert child is not None
            nodes.append(await build(child_id, child.forked_at_seq))
        return ForkNode(run_id=rid, forked_at_seq=forked_at_seq, children=nodes)

    return await build(run_id, root.forked_at_seq)
