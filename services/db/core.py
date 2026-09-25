"""Connection handling + schema migrations for ``data/telecode.db``.

* stdlib ``sqlite3``, WAL journal, ``synchronous=NORMAL``, 5 s busy timeout.
* One connection per (thread, database path): sqlite3 connections must not be
  shared across threads, and the task pools, run drivers and the proxy loop all
  write. The path is re-resolved on every :func:`connect` from
  ``config._settings_dir()`` (hot-relocatable, and what the tests redirect).
* Migrations are an ordered list of SQL scripts; ``schema_migrations`` records
  which ran. Append only — never edit a shipped migration.

Every caller treats the DB as write-through bookkeeping: a failed write is
logged, never raised into a task.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("telecode.services.db")

# Tests point this at a tmp file (tests/conftest.py); production leaves it None.
_override_path: Optional[Path] = None

_local = threading.local()
_init_lock = threading.Lock()
_initialised: set = set()

MIGRATIONS = [
    # 1 — tasks, events, runs, steps, session lineage
    """
    CREATE TABLE tasks (
        task_id           TEXT PRIMARY KEY,
        task_type         TEXT NOT NULL,
        session_id        TEXT,
        session_namespace TEXT,
        status            TEXT NOT NULL,
        pool              TEXT,
        timeout_seconds   INTEGER,
        progress          REAL DEFAULT 0,
        created_at        TEXT,
        started_at        TEXT,
        completed_at      TEXT,
        error             TEXT,
        metadata          TEXT,
        result            TEXT,
        updated_at        TEXT
    );
    CREATE INDEX idx_tasks_status ON tasks(status);
    CREATE INDEX idx_tasks_created ON tasks(created_at);
    CREATE INDEX idx_tasks_session ON tasks(session_id);

    CREATE TABLE task_events (
        task_id TEXT NOT NULL,
        seq     INTEGER NOT NULL,
        kind    TEXT,
        ts      TEXT,
        data    TEXT NOT NULL,
        PRIMARY KEY (task_id, seq)
    );

    CREATE TABLE runs (
        run_id       TEXT PRIMARY KEY,
        job_id       TEXT,
        status       TEXT,
        mode         TEXT,
        source       TEXT,
        started_at   TEXT,
        completed_at TEXT,
        data         TEXT NOT NULL,
        updated_at   TEXT
    );
    CREATE INDEX idx_runs_job ON runs(job_id);
    CREATE INDEX idx_runs_started ON runs(started_at);

    CREATE TABLE run_steps (
        run_id  TEXT NOT NULL,
        idx     INTEGER NOT NULL,
        step_id TEXT NOT NULL,
        status  TEXT,
        task_id TEXT,
        data    TEXT NOT NULL,
        PRIMARY KEY (run_id, step_id)
    );
    CREATE INDEX idx_run_steps_task ON run_steps(task_id);

    CREATE TABLE sessions_index (
        id                TEXT PRIMARY KEY,
        namespace         TEXT,
        workspace_id      TEXT,
        agent_id          TEXT,
        engine            TEXT NOT NULL,
        is_local          INTEGER DEFAULT 0,
        engine_session_id TEXT,
        parent_id         TEXT,
        kind              TEXT,
        created_by        TEXT,
        cumulative_input_tokens  INTEGER DEFAULT 0,
        cumulative_output_tokens INTEGER DEFAULT 0,
        cumulative_cost_usd      REAL DEFAULT 0,
        cost_complete     INTEGER DEFAULT 1,
        runs_count        INTEGER DEFAULT 0,
        last_task_id      TEXT,
        status            TEXT DEFAULT 'active',
        created_at        TEXT,
        updated_at        TEXT
    );
    CREATE INDEX idx_sessions_scope ON sessions_index(namespace, workspace_id, agent_id, engine, is_local);

    CREATE TABLE meta (
        key   TEXT PRIMARY KEY,
        value TEXT
    );
    """,
    # 2 — P2: session policy / fork / rotation lineage, budget tokens, step handoffs
    """
    ALTER TABLE sessions_index ADD COLUMN lineage TEXT;
    ALTER TABLE sessions_index ADD COLUMN policy TEXT;
    ALTER TABLE sessions_index ADD COLUMN forked_from TEXT;
    ALTER TABLE sessions_index ADD COLUMN rotated_from TEXT;
    ALTER TABLE sessions_index ADD COLUMN cumulative_tokens INTEGER DEFAULT 0;
    UPDATE sessions_index SET lineage = CASE WHEN parent_id IS NULL THEN 'fresh' ELSE 'resume' END,
                              cumulative_tokens = cumulative_output_tokens;
    CREATE INDEX idx_sessions_engine_sid ON sessions_index(engine_session_id);
    ALTER TABLE run_steps ADD COLUMN handoff TEXT;
    """,
]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def db_path() -> Path:
    if _override_path is not None:
        return Path(_override_path)
    import config
    return Path(config._settings_dir()) / "data" / "telecode.db"


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    for version, script in enumerate(MIGRATIONS, start=1):
        if version in done:
            continue
        # executescript commits first; wrap the script + the record in one txn.
        conn.executescript("BEGIN;\n" + script + f"\nINSERT INTO schema_migrations VALUES ({version}, '{now_iso()}');\nCOMMIT;")
        logger.info("telecode.db: applied migration %d", version)


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    """This thread's connection to path (default: the current database
    path), created on demand."""
    path = Path(path) if path is not None else db_path()
    key = str(path)
    conns: Dict[str, sqlite3.Connection] = getattr(_local, "conns", None) or {}
    _local.conns = conns
    conn = conns.get(key)
    if conn is not None:
        return conn
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(key, timeout=5.0, isolation_level=None, check_same_thread=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    with _init_lock:
        if key not in _initialised:
            _migrate(conn)
            _initialised.add(key)
    conns[key] = conn
    return conn


def close_thread_connections() -> None:
    """Close this thread's connections (tests; long-lived threads never need it)."""
    for conn in (getattr(_local, "conns", None) or {}).values():
        try:
            conn.close()
        except Exception:
            pass
    _local.conns = {}


def get_meta(key: str) -> Optional[str]:
    row = connect().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(key: str, value: str) -> None:
    connect().execute("INSERT INTO meta(key, value) VALUES(?, ?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
