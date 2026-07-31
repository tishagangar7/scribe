"""Standalone demo: crash a run, resume it, watch nothing re-execute.

    python demo.py          # run (crashes ~40% of the time per step)
    python demo.py --reset  # start over with a fresh log

Run it repeatedly with the same database and watch the [replay] lines grow
while the [execute] lines shrink. Every [replay] is an API call you did not
have to pay for a second time.
"""

from __future__ import annotations

import asyncio
import random
import sys
import time
from pathlib import Path

from scribe.decorators import execute_run, start_run, workflow
from scribe.models import EventType
from scribe.store import SQLiteStore

DB = Path("demo.db")
RUN_ID = "demo-run-1"

CYAN, GREEN, YELLOW, RED, DIM, RESET = (
    "\033[36m",
    "\033[32m",
    "\033[33m",
    "\033[31m",
    "\033[2m",
    "\033[0m",
)

TOKENS_PER_STEP = 1_240
COST_PER_STEP = 0.031


def fake_llm_call(name: str, fail_rate: float = 0.35) -> dict:
    """Stands in for an LLM call: slow, costly, and sometimes rate-limited."""
    time.sleep(0.6)
    if random.random() < fail_rate:
        raise ConnectionError(f"429 rate limited during {name}")
    return {"step": name, "tokens": TOKENS_PER_STEP, "cost": COST_PER_STEP}


@workflow(name="research")
async def research(ctx):
    plan = await ctx.step("plan", lambda: fake_llm_call("plan"))
    _report(ctx, "plan")

    findings = []
    for i in range(6):
        await ctx.step(f"search:{i}", lambda i=i: fake_llm_call(f"search:{i}"))
        _report(ctx, f"search:{i}")
        s = await ctx.step(
            f"summarize:{i}", lambda i=i: fake_llm_call(f"summarize:{i}")
        )
        _report(ctx, f"summarize:{i}")
        findings.append(s)

    out = await ctx.step("synthesize", lambda: fake_llm_call("synthesize"))
    _report(ctx, "synthesize")
    return {"plan": plan, "findings": len(findings), "synthesis": out}


STATS = {"replayed": 0, "executed": 0}


def _report(ctx, name: str) -> None:
    if ctx.is_replaying:
        STATS["replayed"] += 1
        print(f"  {GREEN}⏩ replay {RESET}{DIM}{name:<16} 0.00s      cached{RESET}")
    else:
        STATS["executed"] += 1
        print(
            f"  {CYAN}▸  execute{RESET} {name:<16} 0.60s   "
            f"{TOKENS_PER_STEP:,} tok  ${COST_PER_STEP:.3f}"
        )


async def main() -> int:
    if "--reset" in sys.argv:
        DB.unlink(missing_ok=True)
        print(f"{DIM}fresh log{RESET}\n")

    store = await SQLiteStore.open(DB)

    existing = await store.get_run(RUN_ID)
    if existing is None:
        await start_run(store, "research", run_id=RUN_ID)
        print(f"{YELLOW}▶ starting new run {RUN_ID}{RESET}\n")
    else:
        done = sum(
            1
            for e in await store.read_log(RUN_ID)
            if e.event_type is EventType.STEP_COMPLETED
        )
        print(f"{YELLOW}▶ resuming {RUN_ID} — {done} steps already in the log{RESET}\n")

    started = time.time()
    try:
        result = await execute_run(store, RUN_ID)
    except Exception as exc:
        log = await store.read_log(RUN_ID)
        done = sum(1 for e in log if e.event_type is EventType.STEP_COMPLETED)
        print(f"\n  {RED}✗ crashed: {exc}{RESET}")
        print(
            f"\n{DIM}  {done} completed steps are safe in the log.\n"
            f"  Run `python demo.py` again — they will not re-execute.{RESET}\n"
        )
        await store.close()
        return 1

    elapsed = time.time() - started
    log = await store.read_log(RUN_ID)
    total = sum(1 for e in log if e.event_type is EventType.STEP_COMPLETED)

    if STATS["executed"] == 0 and STATS["replayed"] == 0:
        print(f"  {DIM}run already completed — result served from the log{RESET}\n")
        await store.close()
        return 0

    print(f"\n  {GREEN}✓ completed in {elapsed:.1f}s{RESET}")
    print(f"    {total} steps total, {result['findings']} findings")
    print(
        f"    {STATS['executed']} executed, {GREEN}{STATS['replayed']} replayed{RESET}"
    )
    if STATS["replayed"]:
        print(
            f"    {GREEN}saved {STATS['replayed'] * TOKENS_PER_STEP:,} tokens "
            f"(${STATS['replayed'] * COST_PER_STEP:.2f}) and "
            f"{STATS['replayed'] * 0.6:.1f}s by resuming{RESET}"
        )
    print()
    await store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
