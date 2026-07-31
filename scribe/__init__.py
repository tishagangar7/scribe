"""Scribe -- durable execution for LLM agents.

It writes down what happened, so nothing has to happen twice.

    from scribe import workflow, SQLiteStore, start_run, execute_run

    @workflow(name="research")
    async def research(ctx, topic: str):
        plan = await ctx.step("plan", lambda: llm(f"plan {topic}"))
        return await ctx.step("write", lambda: llm(f"write up {plan}"))
"""

from scribe.context import Context
from scribe.decorators import (
    execute_run,
    get_workflow,
    run_workflow,
    start_run,
    workflow,
)
from scribe.errors import (
    DivergenceError,
    DuplicateStepError,
    NonSerializableResultError,
    RunNotFoundError,
    WorkflowNotFoundError,
)
from scribe.models import Event, EventType, Run, RunStatus
from scribe.store import SQLiteStore, Store

__version__ = "0.1.0"

__all__ = [
    "Context",
    "DivergenceError",
    "DuplicateStepError",
    "Event",
    "EventType",
    "NonSerializableResultError",
    "Run",
    "RunNotFoundError",
    "RunStatus",
    "SQLiteStore",
    "Store",
    "WorkflowNotFoundError",
    "execute_run",
    "get_workflow",
    "run_workflow",
    "start_run",
    "workflow",
]
