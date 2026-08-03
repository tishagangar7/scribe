"""DA-131/132 -- fork inherits a prefix without copying rows, recursively."""

from __future__ import annotations

import pytest

from scribe.decorators import clear_registry, execute_run, start_run, workflow
from scribe.errors import DivergenceError
from scribe.fork import fork, fork_tree
from scribe.models import EventType
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


class Counter:
    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    def hit(self, name: str) -> str:
        self.calls[name] = self.calls.get(name, 0) + 1
        return f"result-of-{name}"

    def __getitem__(self, name: str) -> int:
        return self.calls.get(name, 0)


async def _seq_after_nth_step(store, run_id: str, n: int) -> int:
    """The seq of the nth STEP_COMPLETED event -- what you'd fork at to
    inherit exactly the first n steps."""
    log = await store.read_log(run_id)
    completed = [e for e in log if e.event_type is EventType.STEP_COMPLETED]
    return completed[n - 1].seq


async def test_fork_at_step_3_inherits_nothing_re_executed_at_or_before_it(store):
    counter = Counter()

    @workflow(name="six_steps")
    async def six_steps(ctx):
        return [
            await ctx.step(name, lambda name=name: counter.hit(name))
            for name in ["a", "b", "c", "d", "e", "f"]
        ]

    run_id = await start_run(store, "six_steps")
    parent_result = await execute_run(store, run_id)
    assert parent_result == [f"result-of-{n}" for n in "abcdef"]

    fork_at = await _seq_after_nth_step(store, run_id, 3)
    child_id = await fork(store, run_id, fork_at)

    calls_before_child_runs = dict(counter.calls)
    child_result = await execute_run(store, child_id)

    assert child_result == parent_result
    # "a", "b", "c" must not have executed again for the child.
    assert counter["a"] == calls_before_child_runs["a"]
    assert counter["b"] == calls_before_child_runs["b"]
    assert counter["c"] == calls_before_child_runs["c"]
    # "d", "e", "f" run fresh for the child (its own independent timeline).
    assert counter["d"] == 2
    assert counter["e"] == 2
    assert counter["f"] == 2

    # The child's own physical log has no rows for the inherited prefix.
    child_log = await store.read_log(child_id)
    child_step_ids = {e.step_id for e in child_log if e.step_id is not None}
    assert child_step_ids == {"d", "e", "f"}


async def test_forked_children_diverge_independently(store):
    """Two children forked from the same point run different continuations
    without interfering with each other or the parent."""
    counter = Counter()

    @workflow(name="branchy")
    async def branchy(ctx):
        a = await ctx.step("a", lambda: counter.hit("a"))
        b = await ctx.step("b", lambda: counter.hit("b"))
        return [a, b]

    run_id = await start_run(store, "branchy")
    await execute_run(store, run_id)

    fork_at = await _seq_after_nth_step(store, run_id, 1)
    child_1 = await fork(store, run_id, fork_at, child_run_id="child-1")
    child_2 = await fork(store, run_id, fork_at, child_run_id="child-2")

    result_1 = await execute_run(store, child_1)
    result_2 = await execute_run(store, child_2)

    assert result_1 == result_2 == ["result-of-a", "result-of-b"]
    # "a" ran once (parent only); "b" ran once per child, independently.
    assert counter["a"] == 1
    assert counter["b"] == 3  # parent + child_1 + child_2


async def test_recursive_fork_walks_multiple_generations(store):
    """A fork of a fork: the grandchild inherits through both hops."""
    counter = Counter()

    @workflow(name="chain")
    async def chain(ctx):
        return [
            await ctx.step(name, lambda name=name: counter.hit(name))
            for name in ["a", "b", "c", "d"]
        ]

    run_id = await start_run(store, "chain")
    await execute_run(store, run_id)

    fork_at_1 = await _seq_after_nth_step(store, run_id, 1)
    child_id = await fork(store, run_id, fork_at_1, child_run_id="child")
    await execute_run(store, child_id)  # child now has its own "b","c","d"

    # child's OWN physical log is just [b, c, d] ("a" is inherited, not
    # copied) -- its first own step is "b", so n=1 here, not 2.
    fork_at_2 = await _seq_after_nth_step(store, child_id, 1)
    grandchild_id = await fork(store, child_id, fork_at_2, child_run_id="grandchild")

    calls_before = dict(counter.calls)
    grandchild_result = await execute_run(store, grandchild_id)

    assert grandchild_result == [f"result-of-{n}" for n in "abcd"]
    # "a" and "b" inherited transitively through child -- not re-executed.
    assert counter["a"] == calls_before["a"]
    assert counter["b"] == calls_before["b"]
    # "c" and "d" are the grandchild's own fresh work.
    assert counter["c"] == calls_before["c"] + 1
    assert counter["d"] == calls_before["d"] + 1

    grandchild_log = await store.read_log(grandchild_id)
    grandchild_step_ids = {e.step_id for e in grandchild_log if e.step_id is not None}
    assert grandchild_step_ids == {"c", "d"}


async def test_forked_child_still_detects_genuine_divergence(store):
    """Divergence detection covers the inherited prefix too, not just a
    run's own physically-stored log -- forking at step 2 of 3 means "a" and
    "b" are both inherited, so a code change between them must still be
    caught, the same as it would be for a plain (non-forked) resume."""

    @workflow(name="v1")
    async def v1(ctx):
        await ctx.step("a", lambda: "a")
        await ctx.step("b", lambda: "b")
        return await ctx.step("c", lambda: "c")

    run_id = await start_run(store, "v1")
    await execute_run(store, run_id)

    fork_at = await _seq_after_nth_step(store, run_id, 2)  # inherits a, b
    child_id = await fork(store, run_id, fork_at)

    clear_registry()

    @workflow(name="v1")
    async def v1_changed(ctx):
        await ctx.step("a", lambda: "a")
        await ctx.step("NEW", lambda: "new")
        await ctx.step("b", lambda: "b")
        return await ctx.step("c", lambda: "c")

    with pytest.raises(DivergenceError) as exc:
        await execute_run(store, child_id)

    assert exc.value.expected == "b"
    assert exc.value.actual == "NEW"


async def test_fork_tree_reports_the_full_lineage(store):
    @workflow(name="tiny")
    async def tiny(ctx):
        return await ctx.step("a", lambda: "a")

    run_id = await start_run(store, "tiny")
    await execute_run(store, run_id)
    at = await _seq_after_nth_step(store, run_id, 1)

    child_a = await fork(store, run_id, at, child_run_id="child-a")
    child_b = await fork(store, run_id, at, child_run_id="child-b")
    await fork(store, child_a, at, child_run_id="grandchild")

    tree = await fork_tree(store, run_id)

    assert tree.run_id == run_id
    assert {c.run_id for c in tree.children} == {child_a, child_b}
    child_a_node = next(c for c in tree.children if c.run_id == child_a)
    assert [c.run_id for c in child_a_node.children] == ["grandchild"]
    child_b_node = next(c for c in tree.children if c.run_id == child_b)
    assert child_b_node.children == []
