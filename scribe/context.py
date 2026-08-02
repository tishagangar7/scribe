"""The execution context -- where durability actually happens.

Everything in this project rests on one function, `Context.step`, and one
three-line decision:

    1. Is this step_id already in the log as completed?
    2. Yes -> return the recorded result. Do not execute.
    3. No  -> execute, record the result, then return it.

That is the whole mechanism. Resumption is not a special code path: after a
crash you re-invoke the *same workflow function from line one*, and the
already-completed steps return instantly from disk instead of hitting the
network. Execution "fast-forwards" to the point of failure and continues.
"""

from __future__ import annotations

import inspect
import json
import random as _random
import uuid as _uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from scribe.errors import (
    DivergenceError,
    DuplicateStepError,
    NonSerializableResultError,
)
from scribe.models import Event, EventType, Run, utcnow
from scribe.store import Store

# A step body: zero-arg, sync or async, returning a JSON-serializable value.
type StepFn[T] = Callable[[], T | Awaitable[T]]


class Context:
    """Passed to every workflow. The only sanctioned way to do side effects.

    Attributes:
        run_id: stable identifier; re-invoking with the same run_id resumes.
        is_replaying: True while the current step is being served from the log.
            Useful for suppressing duplicate logging or notifications.
    """

    def __init__(self, store: Store, run: Run) -> None:
        self._store = store
        self._run = run
        self._seen_step_ids: set[str] = set()
        self._expected_order: list[str] = []
        self._position = 0
        self.is_replaying = False

        # Counters for observability -- how much did resumption actually save?
        self.steps_replayed = 0
        self.steps_executed = 0

        # Per-call counters so repeated ctx.now()/random()/uuid() calls each
        # get a distinct, stable step_id without the workflow author naming one.
        self._now_calls = 0
        self._random_calls = 0
        self._uuid_calls = 0

    @property
    def run_id(self) -> str:
        return self._run.run_id

    @property
    def workflow_version(self) -> int:
        return self._run.workflow_version

    async def load_expected_order(self) -> None:
        """Read the recorded step order so divergence can be detected.

        Called once before the workflow body runs. The list is the sequence
        of step_ids that completed during the original recording; replay must
        request them in exactly this order.
        """
        log = await self._store.read_log(self.run_id)
        self._expected_order = [
            e.step_id
            for e in log
            if e.event_type is EventType.STEP_COMPLETED and e.step_id is not None
        ]
        self._position = 0

    async def step(self, step_id: str, fn: StepFn[Any]) -> Any:
        """Execute `fn` exactly once across all attempts of this run.

        Args:
            step_id: stable, unique-within-run identifier. Inside a loop,
                include the index: f"search:{i}".
            fn: zero-arg callable, sync or async. Its return value must be
                JSON-serializable, because it is persisted to the log.

        Returns:
            The result of `fn` -- freshly computed on first execution, or
            read back from the event log on every subsequent replay.

        Raises:
            DuplicateStepError: step_id already used in this run.
            DivergenceError: replay requested a step out of recorded order.
            NonSerializableResultError: fn returned something unpersistable.
        """
        if step_id in self._seen_step_ids:
            raise DuplicateStepError(self.run_id, step_id)
        self._seen_step_ids.add(step_id)

        recorded = await self._store.get_completed_step(self.run_id, step_id)

        if recorded is not None:
            # --- REPLAY PATH: no execution, no network, no cost. ---
            self._check_divergence(step_id)
            self._position += 1
            self.steps_replayed += 1
            self.is_replaying = True
            return recorded.payload

        # --- EXECUTE PATH: we are past the end of the log. ---
        # Any step_id absent from the log means recording, not replay. If the
        # log still has unconsumed entries at this point, the workflow has
        # skipped a recorded step, which is divergence.
        if self._position < len(self._expected_order):
            raise DivergenceError(
                self.run_id,
                self._position,
                self._expected_order[self._position],
                step_id,
            )

        self.is_replaying = False
        seq = await self._store.next_seq(self.run_id)
        await self._store.append_event(
            Event(
                run_id=self.run_id,
                seq=seq,
                step_id=step_id,
                event_type=EventType.STEP_STARTED,
            )
        )

        try:
            result = fn()
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            await self._store.append_event(
                Event(
                    run_id=self.run_id,
                    seq=await self._store.next_seq(self.run_id),
                    step_id=step_id,
                    event_type=EventType.STEP_FAILED,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
            )
            raise

        _assert_serializable(step_id, result)

        # Durable BEFORE the caller sees the value. A crash between the
        # workflow receiving the result and the commit landing would lose
        # work we already paid for.
        await self._store.append_event(
            Event(
                run_id=self.run_id,
                seq=await self._store.next_seq(self.run_id),
                step_id=step_id,
                event_type=EventType.STEP_COMPLETED,
                payload=result,
            )
        )

        self._position += 1
        self._expected_order.append(step_id)
        self.steps_executed += 1
        return result

    async def now(self) -> datetime:
        """Replay-stable wall clock. Never call `datetime.now()` in a workflow.

        The value is recorded as an ordinary step on first execution; every
        replay returns that same recorded instant regardless of when the
        replay actually runs, so branching on it (e.g. `ctx.now().hour`)
        takes the same path every time.
        """
        step_id = f"ctx.now:{self._now_calls}"
        self._now_calls += 1
        iso = await self.step(step_id, lambda: utcnow().isoformat())
        return datetime.fromisoformat(iso)

    async def random(self) -> float:
        """Replay-stable `random.random()`. Never call `random.*` directly."""
        step_id = f"ctx.random:{self._random_calls}"
        self._random_calls += 1
        result: float = await self.step(step_id, _random.random)
        return result

    async def uuid(self) -> _uuid.UUID:
        """Replay-stable `uuid.uuid4()`. Never call `uuid.uuid4()` directly."""
        step_id = f"ctx.uuid:{self._uuid_calls}"
        self._uuid_calls += 1
        raw = await self.step(step_id, lambda: str(_uuid.uuid4()))
        return _uuid.UUID(raw)

    def _check_divergence(self, step_id: str) -> None:
        if self._position >= len(self._expected_order):
            return
        expected = self._expected_order[self._position]
        if expected != step_id:
            raise DivergenceError(self.run_id, self._position, expected, step_id)


def _assert_serializable(step_id: str, value: Any) -> None:
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise NonSerializableResultError(
            step_id, type(value).__name__, str(exc)
        ) from exc
