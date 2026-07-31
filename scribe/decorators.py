"""Workflow registration and the top-level run/resume entry point."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from scribe.context import Context
from scribe.errors import WorkflowNotFoundError
from scribe.models import Event, EventType, Run, RunStatus, utcnow
from scribe.store import Store

WorkflowFn = Callable[..., Coroutine[Any, Any, Any]]


@dataclass(frozen=True)
class RegisteredWorkflow:
    name: str
    version: int
    fn: WorkflowFn


_REGISTRY: dict[str, RegisteredWorkflow] = {}


def workflow(name: str, version: int = 1) -> Callable[[WorkflowFn], WorkflowFn]:
    """Register an async function as a durable workflow.

    The decorated function must take `ctx: Context` as its first argument and
    perform every side effect through `ctx.step`. Anything it does outside a
    step will be re-executed on every replay.

    Version is recorded on each run. Sprint 2 pins in-flight runs to the
    version they started under, so deploying new code does not corrupt them.
    """

    def decorator(fn: WorkflowFn) -> WorkflowFn:
        _REGISTRY[name] = RegisteredWorkflow(name=name, version=version, fn=fn)
        fn.__workflow_name__ = name  # type: ignore[attr-defined]
        fn.__workflow_version__ = version  # type: ignore[attr-defined]
        return fn

    return decorator


def get_workflow(name: str) -> RegisteredWorkflow:
    if name not in _REGISTRY:
        raise WorkflowNotFoundError(name)
    return _REGISTRY[name]


def clear_registry() -> None:
    """Test helper. Not for production use."""
    _REGISTRY.clear()


async def start_run(
    store: Store,
    workflow_name: str,
    input: Any = None,
    run_id: str | None = None,
    token_budget: int | None = None,
    cost_budget_usd: float | None = None,
) -> str:
    """Create a run row. Does not execute it."""
    wf = get_workflow(workflow_name)
    rid = run_id or f"run_{uuid.uuid4().hex[:12]}"
    run = Run(
        run_id=rid,
        workflow_name=wf.name,
        workflow_version=wf.version,
        input=input,
        status=RunStatus.PENDING,
        token_budget=token_budget,
        cost_budget_usd=cost_budget_usd,
    )
    await store.create_run(run)
    await store.append_event(
        Event(
            run_id=rid,
            seq=await store.next_seq(rid),
            event_type=EventType.RUN_STARTED,
            payload=input,
        )
    )
    return rid


async def execute_run(store: Store, run_id: str) -> Any:
    """Execute or resume a run to completion.

    This is the same call whether the run is brand new or resuming after a
    crash. There is no separate "resume" code path -- the workflow function
    is invoked from line one either way, and `ctx.step` decides per step
    whether to replay from the log or actually execute.

    Raises:
        Whatever the workflow raises. The log survives, so calling this again
        with the same run_id will resume from the failed step.
    """
    run = await store.get_run(run_id)
    if run is None:
        from scribe.errors import RunNotFoundError

        raise RunNotFoundError(run_id)

    if run.status is RunStatus.COMPLETED:
        return run.result

    wf = get_workflow(run.workflow_name)

    ctx = Context(store, run)
    await ctx.load_expected_order()

    run.status = RunStatus.RUNNING
    await store.update_run(run)

    try:
        if isinstance(run.input, dict):
            result = await wf.fn(ctx, **run.input)
        elif run.input is None:
            result = await wf.fn(ctx)
        else:
            result = await wf.fn(ctx, run.input)
    except Exception as exc:
        run.status = RunStatus.FAILED
        run.error = f"{type(exc).__name__}: {exc}"
        run.updated_at = utcnow()
        await store.update_run(run)
        await store.append_event(
            Event(
                run_id=run_id,
                seq=await store.next_seq(run_id),
                event_type=EventType.RUN_FAILED,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        )
        raise

    run.status = RunStatus.COMPLETED
    run.result = result
    run.updated_at = utcnow()
    await store.update_run(run)
    await store.append_event(
        Event(
            run_id=run_id,
            seq=await store.next_seq(run_id),
            event_type=EventType.RUN_COMPLETED,
            payload=result,
        )
    )
    return result


async def run_workflow(
    store: Store,
    workflow_name: str,
    input: Any = None,
    run_id: str | None = None,
) -> tuple[str, Any]:
    """Convenience: start (if new) and execute. Returns (run_id, result)."""
    if run_id is not None and await store.get_run(run_id) is not None:
        rid = run_id  # resuming an existing run
    else:
        rid = await start_run(store, workflow_name, input, run_id)
    return rid, await execute_run(store, rid)
