"""Core data models for the durable execution runtime.

Two entities matter: a `Run` (one execution of a workflow) and an `Event`
(one immutable fact about what happened during that run). Run state is
*derived* from the event log, never stored authoritatively on the run row --
the run row is a materialized convenience.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    """Timezone-aware UTC now.

    Note: runtime-internal use only. Workflow code must never call this --
    it must use `ctx.now()` so the value is recorded and replay-stable.
    """
    return datetime.now(UTC)


class RunStatus(StrEnum):
    """Lifecycle states for a run.

    Terminal states: COMPLETED, FAILED, CANCELLED.
    BUDGET_EXCEEDED is deliberately *non-terminal*: the log stays resumable,
    so raising the cap and re-invoking continues from where it stopped.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BUDGET_EXCEEDED = "budget_exceeded"

    @property
    def is_terminal(self) -> bool:
        return self in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)


class EventType(StrEnum):
    """Every fact we record. The log is append-only; corrections are new events."""

    RUN_STARTED = "run_started"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"


class Event(BaseModel):
    """One immutable entry in a run's log.

    `seq` is monotonic within a run and assigned by the store at append time.
    `step_id` is assigned by the workflow author and must be stable across
    replays -- it is the key that makes resumption work.
    """

    run_id: str
    seq: int
    step_id: str | None = None
    event_type: EventType
    payload: Any = None
    error_type: str | None = None
    error_message: str | None = None
    tokens: int = 0
    cost_usd: float = 0.0
    created_at: datetime = Field(default_factory=utcnow)


class Run(BaseModel):
    """One execution of a workflow."""

    run_id: str
    workflow_name: str
    workflow_version: int = 1
    input: Any = None
    status: RunStatus = RunStatus.PENDING
    result: Any = None
    error: str | None = None

    # Lineage (fork). Present from day one -- retrofitting these columns
    # after events exist is painful, so they ship in the first schema.
    parent_run_id: str | None = None
    forked_at_seq: int | None = None

    # Budget enforcement (wired up in Sprint 3, stored from Sprint 1).
    token_budget: int | None = None
    tokens_used: int = 0
    cost_budget_usd: float | None = None
    cost_used_usd: float = 0.0

    # Leasing (wired up in Sprint 5).
    leased_by: str | None = None
    lease_expires_at: datetime | None = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
