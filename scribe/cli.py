"""DA-126 -- the `da` command line tool.

One subcommand today: `da replay --all --db PATH` runs every recorded run in
a store as an offline regression suite and reports pass/fail.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib

from scribe.replay import ReplaySuiteResult, replay_all
from scribe.store import SQLiteStore


async def _replay_all(db_path: str) -> ReplaySuiteResult:
    store = await SQLiteStore.open(db_path)
    try:
        return await replay_all(store)
    finally:
        await store.close()


def _print_report(suite: ReplaySuiteResult) -> None:
    print(f"{suite.passed}/{suite.total} runs replayed identically.")
    for failure in suite.failures:
        print(f"  FAILED {failure.run_id}: {failure.error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="da")
    subparsers = parser.add_subparsers(dest="command", required=True)

    replay_parser = subparsers.add_parser(
        "replay", help="replay recorded runs from a store as a regression suite"
    )
    replay_parser.add_argument(
        "--all",
        action="store_true",
        required=True,
        help="replay every run in the store (currently the only supported mode)",
    )
    replay_parser.add_argument(
        "--db", required=True, help="path to the SQLite store to replay"
    )
    replay_parser.add_argument(
        "--import",
        dest="import_modules",
        action="append",
        default=[],
        metavar="MODULE",
        help=(
            "Python module to import before replaying, to register its "
            "@workflow-decorated functions (repeatable). The registry is "
            "in-process only, so a run's workflow module must be imported "
            "here or replay fails with WorkflowNotFoundError."
        ),
    )

    args = parser.parse_args(argv)

    if args.command == "replay":
        for module_name in args.import_modules:
            importlib.import_module(module_name)
        suite = asyncio.run(_replay_all(args.db))
        _print_report(suite)
        return 0 if suite.all_passed else 1

    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
