"""DA-121/122 -- replay a recorded run for verification, offline and free.

`execute_run` already serves a run's steps from the log instead of
re-executing them whenever the log already has the answer (that's the whole
mechanism `Context.step` implements). What `execute_run` does *not* do is
refuse to fall back to a live call when the log runs out -- and for a
COMPLETED run it doesn't even re-invoke the workflow body at all, short-
circuiting straight to the stored result.

`replay` re-invokes the workflow body through a `Context` that raises
instead of executing anything the log doesn't already have -- "network
disabled" is enforced structurally, not by convention. That makes replay
a genuine regression check: if it produces the original result, every step
of the original recorded execution really is reproducible from the log
alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scribe.context import Context, StepFn
from scribe.decorators import get_workflow
from scribe.errors import RunNotFoundError
from scribe.models import Run
from scribe.store import Store


class ReplayIncompleteError(Exception):
    """The workflow requested a step_id the recorded log doesn't have.

    Either the original run never finished (the log is a true prefix, not a
    complete recording) or the workflow has diverged. Either way, replay
    must not paper over the gap with a live call -- that would defeat the
    entire point of replaying at $0 and offline.
    """

    def __init__(self, run_id: str, step_id: str) -> None:
        super().__init__(
            f"replay of run {run_id!r} requested step {step_id!r}, which is not "
            f"in the recorded log. Replay never executes a step for real."
        )
        self.run_id = run_id
        self.step_id = step_id


class _ReplayContext(Context):
    """A Context that can only ever serve steps from the log.

    Overrides `step` to check the log itself before doing anything else:
    an injected fault raises in place of the recorded result (DA-124), and
    a step absent from the log raises `ReplayIncompleteError` rather than
    falling through to `Context.step`'s execute path.
    """

    def __init__(
        self, store: Store, run: Run, inject: dict[str, type[BaseException]]
    ) -> None:
        super().__init__(store, run)
        self._inject = inject

    async def step(self, step_id: str, fn: StepFn[Any]) -> Any:
        if step_id in self._inject:
            raise self._inject[step_id](f"injected failure at step {step_id!r}")

        recorded = await self._store.get_completed_step(self.run_id, step_id)
        if recorded is None:
            raise ReplayIncompleteError(self.run_id, step_id)

        return await super().step(step_id, fn)


@dataclass(frozen=True)
class ReplayResult:
    """Outcome of replaying one run."""

    run_id: str
    passed: bool
    result: Any = None
    error: str | None = None
    step_sequence: list[str] = field(default_factory=list)


async def replay(
    store: Store,
    run_id: str,
    *,
    inject: dict[str, type[BaseException]] | None = None,
) -> ReplayResult:
    """Reconstruct `run_id` purely from its recorded log.

    Intended for COMPLETED runs: `passed` means the replayed result matches
    the originally recorded `run.result` exactly, with every step served
    from the log. `inject` maps a step_id to an exception type to raise in
    place of that step's recorded result -- for asserting how surrounding
    workflow code reacts to a failure at a chosen point (DA-124), without
    needing a real historical failure to test against.

    Never raises: any exception during replay (including an injected one)
    is captured in the returned ReplayResult so `replay_all` can keep going
    across many runs.
    """
    run = await store.get_run(run_id)
    if run is None:
        raise RunNotFoundError(run_id)

    wf = get_workflow(run.workflow_name)
    ctx = _ReplayContext(store, run, inject or {})
    await ctx.load_expected_order()

    try:
        if isinstance(run.input, dict):
            result = await wf.fn(ctx, **run.input)
        elif run.input is None:
            result = await wf.fn(ctx)
        else:
            result = await wf.fn(ctx, run.input)
    except Exception as exc:
        return ReplayResult(
            run_id=run_id,
            passed=False,
            error=f"{type(exc).__name__}: {exc}",
            step_sequence=ctx.step_sequence,
        )

    return ReplayResult(
        run_id=run_id,
        passed=(result == run.result),
        result=result,
        step_sequence=ctx.step_sequence,
    )


@dataclass(frozen=True)
class ReplaySuiteResult:
    """Outcome of replaying every run in a store as a regression suite."""

    total: int
    results: list[ReplayResult]

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failures(self) -> list[ReplayResult]:
        return [r for r in self.results if not r.passed]

    @property
    def all_passed(self) -> bool:
        return self.total > 0 and not self.failures


async def replay_all(
    store: Store, run_ids: list[str] | None = None
) -> ReplaySuiteResult:
    """Replay every run in `store` (or just `run_ids`) as a regression suite.

    "A directory of recorded logs" in this project is one SQLite store file
    holding many run rows, rather than one file per run -- so this iterates
    the store's run_ids instead of a filesystem directory.
    """
    ids = run_ids if run_ids is not None else await store.list_run_ids()
    results = [await replay(store, rid) for rid in ids]
    return ReplaySuiteResult(total=len(results), results=results)
