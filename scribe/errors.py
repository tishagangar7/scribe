"""Exception hierarchy for the runtime.

Design note: these are all *loud*. There is no error condition in this
runtime that is handled by silently continuing, because every one of them
indicates the log and the code have gotten out of sync -- and a replay that
proceeds while out of sync produces plausible, wrong results.
"""

from __future__ import annotations


class RuntimeError_(Exception):
    """Base class for all runtime errors."""


class DuplicateStepError(RuntimeError_):
    """The same step_id was requested twice within one run.

    Step IDs are the primary key for resumption. If two steps share an ID,
    resumption becomes ambiguous -- the second would silently receive the
    first's cached result.
    """

    def __init__(self, run_id: str, step_id: str) -> None:
        super().__init__(
            f"step_id {step_id!r} requested twice in run {run_id!r}. "
            f"Step IDs must be unique within a run. If this is inside a loop, "
            f"include the loop index, e.g. f'search:{{i}}'."
        )
        self.run_id = run_id
        self.step_id = step_id


class DivergenceError(RuntimeError_):
    """Replay requested a step that does not match the recorded log.

    This means the workflow code took a different path than it did during
    recording. Causes, in rough order of likelihood:

      1. Nondeterminism in workflow code (datetime.now, random, uuid4,
         dict/set iteration order, reading env vars).
      2. The workflow source changed while this run was in flight.
      3. A conditional branching on data fetched outside a ctx.step.

    We halt rather than continue. A divergent replay would write new events
    into a log whose prefix describes a different execution -- the resulting
    run is coherent-looking and wrong, which is the worst possible outcome.
    """

    def __init__(self, run_id: str, seq: int, expected: str, actual: str) -> None:
        super().__init__(
            f"Divergence in run {run_id!r} at seq {seq}: "
            f"log expected step {expected!r} but workflow requested {actual!r}. "
            f"The workflow took a different path than when it was recorded. "
            f"Check for nondeterminism (use ctx.now/ctx.random/ctx.uuid) or "
            f"a code change since this run started."
        )
        self.run_id = run_id
        self.seq = seq
        self.expected = expected
        self.actual = actual


class NonSerializableResultError(RuntimeError_):
    """A step returned something that cannot be written to the log.

    Caught at write time rather than at read time, because a failure here
    at read time would surface hours later during a replay.
    """

    def __init__(self, step_id: str, type_name: str, detail: str) -> None:
        super().__init__(
            f"Step {step_id!r} returned a non-JSON-serializable value of type "
            f"{type_name}: {detail}. Step results are persisted to the event log, "
            f"so they must be JSON-serializable. Return a dict or a Pydantic "
            f"model dump instead."
        )
        self.step_id = step_id


class RunNotFoundError(RuntimeError_):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"No run found with run_id {run_id!r}")
        self.run_id = run_id


class WorkflowNotFoundError(RuntimeError_):
    def __init__(self, name: str) -> None:
        super().__init__(
            f"No workflow registered under the name {name!r}. "
            f"Decorate it with @workflow(name=...) and ensure the module is imported."
        )
        self.name = name
