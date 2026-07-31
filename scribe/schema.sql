-- Event log schema (SQLite / development).
--
-- Two invariants govern everything here:
--   1. `events` is APPEND-ONLY. No UPDATE, no DELETE, ever. Corrections are
--      new events. This is what makes replay trustworthy.
--   2. A step's completion event is committed BEFORE ctx.step returns its
--      result to the workflow. If we returned first and committed after, a
--      crash in that window would lose a completed step and we would
--      re-execute (and re-pay for) it.
--
-- The Postgres version in Sprint 5 mirrors this with JSONB, TIMESTAMPTZ,
-- and FOR UPDATE SKIP LOCKED leasing.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    run_id            TEXT PRIMARY KEY,
    workflow_name     TEXT NOT NULL,
    workflow_version  INTEGER NOT NULL DEFAULT 1,
    input             TEXT,                       -- JSON
    status            TEXT NOT NULL,
    result            TEXT,                       -- JSON
    error             TEXT,

    -- Lineage. Populated by fork() in Sprint 3. Present now because adding
    -- columns that change step *resolution* semantics later is a rewrite.
    parent_run_id     TEXT REFERENCES runs(run_id),
    forked_at_seq     INTEGER,

    -- Budget. Enforced in Sprint 3.
    token_budget      INTEGER,
    tokens_used       INTEGER NOT NULL DEFAULT 0,
    cost_budget_usd   REAL,
    cost_used_usd     REAL NOT NULL DEFAULT 0.0,

    -- Leasing. Used by the distributed worker pool in Sprint 5.
    leased_by         TEXT,
    lease_expires_at  TEXT,

    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    run_id          TEXT NOT NULL REFERENCES runs(run_id),
    seq             INTEGER NOT NULL,
    step_id         TEXT,
    event_type      TEXT NOT NULL,
    payload         TEXT,                         -- JSON
    error_type      TEXT,
    error_message   TEXT,
    tokens          INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL DEFAULT 0.0,
    created_at      TEXT NOT NULL,

    PRIMARY KEY (run_id, seq)
);

-- The hot path: ctx.step asks "has this step already completed?" on every
-- single call. This index makes that O(1) instead of a log scan.
CREATE UNIQUE INDEX IF NOT EXISTS events_completed_step
    ON events (run_id, step_id)
    WHERE event_type = 'step_completed' AND step_id IS NOT NULL;

-- Worker polling (Sprint 5).
CREATE INDEX IF NOT EXISTS runs_claimable
    ON runs (status, lease_expires_at);

-- Fork tree traversal (Sprint 3).
CREATE INDEX IF NOT EXISTS runs_parent
    ON runs (parent_run_id);
