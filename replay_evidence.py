"""DA-125 -- record 20 real naive-agent runs, replay all 20, measure overhead.

Standalone script, not a pytest test: it deliberately runs the full-latency
"research" workflow from demo.py 20 times end to end (real 0.6s sleeps per
step, real ~35% simulated failures needing real retries) to produce a
representative baseline, then reports how much faster and cheaper replaying
those same 20 runs is. Run once to produce the numbers recorded in
docs/journal.md -- this is intentionally not part of CI, which should stay
seconds, not minutes.

    python replay_evidence.py
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import demo  # noqa: F401 -- importing registers the "research" workflow
from scribe.decorators import execute_run, start_run
from scribe.replay import replay_all
from scribe.store import SQLiteStore

DB = Path("replay_evidence.db")
N_RUNS = 20


async def run_to_completion(store: SQLiteStore, run_id: str) -> Any:
    """Keep re-invoking execute_run until it finishes -- exactly how a human
    re-running `python demo.py` after each simulated rate-limit would."""
    while True:
        try:
            return await execute_run(store, run_id)
        except Exception:
            continue


async def main() -> None:
    demo._report = lambda ctx, name: None  # quiet demo.py's per-step printing

    DB.unlink(missing_ok=True)
    store = await SQLiteStore.open(DB)

    print(f"Recording {N_RUNS} real runs of the 'research' workflow...\n")
    run_ids = []
    direct_started = time.time()
    for i in range(N_RUNS):
        run_id = f"evidence-{i}"
        await start_run(store, "research", run_id=run_id)
        await run_to_completion(store, run_id)
        run_ids.append(run_id)
        print(f"  [{i + 1}/{N_RUNS}] recorded {run_id}")
    direct_elapsed = time.time() - direct_started

    print(
        f"\nRecorded {N_RUNS} runs in {direct_elapsed:.1f}s "
        f"({direct_elapsed / N_RUNS:.1f}s/run average, including retries).\n"
    )
    print("Replaying all of them from the log, offline...\n")

    replay_started = time.time()
    suite = await replay_all(store, run_ids)
    replay_elapsed = time.time() - replay_started

    print(f"Replayed {suite.total} runs in {replay_elapsed:.3f}s total.")
    print(f"{suite.passed}/{suite.total} identical to their recording.")
    for f in suite.failures:
        print(f"  FAILED {f.run_id}: {f.error}")

    speedup = direct_elapsed / replay_elapsed if replay_elapsed > 0 else float("inf")
    print(f"\nDirect execution: {direct_elapsed:.1f}s total, real network/LLM cost")
    print(f"Replay:           {replay_elapsed:.3f}s total, $0, offline")
    print(f"Speedup:          ~{speedup:,.0f}x")

    await store.close()
    DB.unlink(missing_ok=True)

    assert suite.all_passed, "not all runs replayed identically"


if __name__ == "__main__":
    asyncio.run(main())
