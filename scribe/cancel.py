"""DA-128 -- cancellation: cancelling a run marks it and all descendants cancelled.

"Descendants" means the fork lineage (`Run.parent_run_id`), present in the
schema since Sprint 1 even before `scribe/fork.py` exists to produce any --
so cancellation is written against that lineage now, ready for fork to use
it. `Context.step`/`llm_step` check for cancellation at the start of every
new step (see `_check_cancellation`), so a run already in flight in another
process notices and stops before its next paid step, not only on its next
fresh invocation.
"""

from __future__ import annotations

from scribe.errors import RunNotFoundError
from scribe.fork import children_map
from scribe.models import RunStatus
from scribe.store import Store


async def cancel_run(store: Store, run_id: str) -> list[str]:
    """Cancel `run_id` and every run forked from it, transitively.

    Only non-terminal runs are actually marked CANCELLED -- a run that
    already completed, failed, or was already cancelled is left alone.
    Returns the run_ids newly cancelled by this call.
    """
    root = await store.get_run(run_id)
    if root is None:
        raise RunNotFoundError(run_id)

    children = await children_map(store)

    cancelled: list[str] = []
    seen = {run_id}
    stack = [run_id]
    while stack:
        current_id = stack.pop()
        current = root if current_id == run_id else await store.get_run(current_id)
        assert current is not None
        if not current.status.is_terminal:
            current.status = RunStatus.CANCELLED
            await store.update_run(current)
            cancelled.append(current_id)
        for child_id in children.get(current_id, []):
            if child_id not in seen:
                seen.add(child_id)
                stack.append(child_id)

    return cancelled
