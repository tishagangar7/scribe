"""Event log persistence.

`Store` is a Protocol so that Sprint 5 can drop in a Postgres backend and
run the identical test suite against both. Nothing above this module knows
which backend it is talking to.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

import aiosqlite

from scribe.models import Event, EventType, Run, RunStatus, utcnow

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class Store(Protocol):
    """Interface every backend implements."""

    async def create_run(self, run: Run) -> None: ...
    async def get_run(self, run_id: str) -> Run | None: ...
    async def update_run(self, run: Run) -> None: ...
    async def append_event(self, event: Event) -> int: ...
    async def next_seq(self, run_id: str) -> int: ...
    async def get_completed_step(self, run_id: str, step_id: str) -> Event | None: ...
    async def read_log(self, run_id: str) -> list[Event]: ...


def _dumps(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


def _loads(value: str | None) -> Any:
    return None if value is None else json.loads(value)


class SQLiteStore:
    """Async SQLite event store. Development and single-process use.

    Usage:
        store = await SQLiteStore.open("runs.db")
        ...
        await store.close()
    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    @classmethod
    async def open(cls, path: str | Path) -> SQLiteStore:
        conn = await aiosqlite.connect(str(path))
        conn.row_factory = aiosqlite.Row
        await conn.executescript(SCHEMA_PATH.read_text())
        await conn.commit()
        return cls(conn)

    async def close(self) -> None:
        await self._conn.close()

    # ---------------------------------------------------------------- runs

    async def create_run(self, run: Run) -> None:
        await self._conn.execute(
            """
            INSERT INTO runs (
                run_id, workflow_name, workflow_version, input, status, result,
                error, parent_run_id, forked_at_seq, token_budget, tokens_used,
                cost_budget_usd, cost_used_usd, leased_by, lease_expires_at,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run.run_id,
                run.workflow_name,
                run.workflow_version,
                _dumps(run.input),
                run.status.value,
                _dumps(run.result),
                run.error,
                run.parent_run_id,
                run.forked_at_seq,
                run.token_budget,
                run.tokens_used,
                run.cost_budget_usd,
                run.cost_used_usd,
                run.leased_by,
                run.lease_expires_at.isoformat() if run.lease_expires_at else None,
                run.created_at.isoformat(),
                run.updated_at.isoformat(),
            ),
        )
        await self._conn.commit()

    async def get_run(self, run_id: str) -> Run | None:
        cur = await self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        return Run(
            run_id=row["run_id"],
            workflow_name=row["workflow_name"],
            workflow_version=row["workflow_version"],
            input=_loads(row["input"]),
            status=RunStatus(row["status"]),
            result=_loads(row["result"]),
            error=row["error"],
            parent_run_id=row["parent_run_id"],
            forked_at_seq=row["forked_at_seq"],
            token_budget=row["token_budget"],
            tokens_used=row["tokens_used"],
            cost_budget_usd=row["cost_budget_usd"],
            cost_used_usd=row["cost_used_usd"],
            leased_by=row["leased_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def update_run(self, run: Run) -> None:
        run.updated_at = utcnow()
        await self._conn.execute(
            """
            UPDATE runs SET
                status = ?, result = ?, error = ?, tokens_used = ?,
                cost_used_usd = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (
                run.status.value,
                _dumps(run.result),
                run.error,
                run.tokens_used,
                run.cost_used_usd,
                run.updated_at.isoformat(),
                run.run_id,
            ),
        )
        await self._conn.commit()

    # -------------------------------------------------------------- events

    async def next_seq(self, run_id: str) -> int:
        cur = await self._conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 AS n FROM events WHERE run_id = ?",
            (run_id,),
        )
        row = await cur.fetchone()
        return int(row["n"])

    async def append_event(self, event: Event) -> int:
        """Append one event and commit before returning.

        The commit is not optional and not batched. `ctx.step` relies on the
        completion event being durable before the result reaches workflow
        code -- otherwise a crash in that window silently loses paid work.
        """
        await self._conn.execute(
            """
            INSERT INTO events (
                run_id, seq, step_id, event_type, payload, error_type,
                error_message, tokens, cost_usd, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event.run_id,
                event.seq,
                event.step_id,
                event.event_type.value,
                _dumps(event.payload),
                event.error_type,
                event.error_message,
                event.tokens,
                event.cost_usd,
                event.created_at.isoformat(),
            ),
        )
        await self._conn.commit()
        return event.seq

    async def get_completed_step(self, run_id: str, step_id: str) -> Event | None:
        """The hot path. Called once per ctx.step invocation."""
        cur = await self._conn.execute(
            """
            SELECT * FROM events
            WHERE run_id = ? AND step_id = ? AND event_type = ?
            """,
            (run_id, step_id, EventType.STEP_COMPLETED.value),
        )
        row = await cur.fetchone()
        return None if row is None else self._row_to_event(row)

    async def read_log(self, run_id: str) -> list[Event]:
        cur = await self._conn.execute(
            "SELECT * FROM events WHERE run_id = ? ORDER BY seq", (run_id,)
        )
        return [self._row_to_event(r) for r in await cur.fetchall()]

    @staticmethod
    def _row_to_event(row: aiosqlite.Row) -> Event:
        return Event(
            run_id=row["run_id"],
            seq=row["seq"],
            step_id=row["step_id"],
            event_type=EventType(row["event_type"]),
            payload=_loads(row["payload"]),
            error_type=row["error_type"],
            error_message=row["error_message"],
            tokens=row["tokens"],
            cost_usd=row["cost_usd"],
            created_at=row["created_at"],
        )
