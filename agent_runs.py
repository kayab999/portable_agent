"""agent_runs.py — durable run and step storage for agent execution.

Stdlib-only. Persistence is opt-in and stores sanitized run metadata.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.resolve()
DB_DIR = ROOT / "data"
DB_PATH = DB_DIR / "agent_runs.db"

SCHEMA_VERSION = 1


def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _init_db(db_path: Path = DB_PATH) -> None:
    conn = _connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS agent_runs (
                run_id TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                status TEXT NOT NULL,
                goal TEXT,
                persona TEXT,
                model TEXT,
                config_json TEXT,
                final_answer TEXT,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS agent_steps (
                step_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                started_at INTEGER NOT NULL,
                finished_at INTEGER,
                status TEXT NOT NULL,
                messages_json TEXT,
                tool_calls_json TEXT,
                tool_results_json TEXT,
                model_output TEXT,
                error TEXT,
                FOREIGN KEY (run_id) REFERENCES agent_runs(run_id)
            );
            CREATE INDEX IF NOT EXISTS idx_agent_steps_run
                ON agent_steps(run_id, step_index);
            CREATE INDEX IF NOT EXISTS idx_agent_runs_status
                ON agent_runs(status, updated_at);
            """
        )
        conn.commit()
    finally:
        conn.close()


def new_run_id() -> str:
    return f"run-{uuid.uuid4().hex}"


def create_run(
    *,
    goal: str = "",
    persona: str = "",
    model: str = "",
    config: dict[str, Any] | None = None,
    db_path: Path = DB_PATH,
) -> str:
    _init_db(db_path)
    now = int(time.time())
    run_id = new_run_id()
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO agent_runs
                (run_id, created_at, updated_at, status, goal, persona, model, config_json)
            VALUES (?, ?, ?, 'running', ?, ?, ?, ?)
            """,
            (run_id, now, now, goal[:2000], persona, model, json.dumps(config or {})),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id


def record_step(
    run_id: str,
    step_index: int,
    *,
    status: str = "running",
    messages: list[dict] | None = None,
    tool_calls: list[dict] | None = None,
    tool_results: list[dict] | None = None,
    model_output: str = "",
    error: str = "",
    db_path: Path = DB_PATH,
) -> int:
    _init_db(db_path)
    now = int(time.time())
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO agent_steps
                (run_id, step_index, started_at, status, messages_json,
                 tool_calls_json, tool_results_json, model_output, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                step_index,
                now,
                status,
                _dump(messages),
                _dump(tool_calls),
                _dump(tool_results),
                (model_output or "")[:10000],
                (error or "")[:2000],
            ),
        )
        step_id = int(cur.lastrowid)
        conn.execute(
            "UPDATE agent_runs SET updated_at = ? WHERE run_id = ?",
            (now, run_id),
        )
        conn.commit()
        return step_id
    finally:
        conn.close()


def finish_step(
    step_id: int,
    *,
    status: str,
    tool_results: list[dict] | None = None,
    model_output: str = "",
    error: str = "",
    db_path: Path = DB_PATH,
) -> None:
    _init_db(db_path)
    now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            UPDATE agent_steps
            SET finished_at = ?, status = ?, tool_results_json = ?,
                model_output = ?, error = ?
            WHERE step_id = ?
            """,
            (
                now,
                status,
                _dump(tool_results),
                (model_output or "")[:10000],
                (error or "")[:2000],
                step_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def finish_run(
    run_id: str,
    *,
    status: str,
    final_answer: str = "",
    error: str = "",
    db_path: Path = DB_PATH,
) -> None:
    _init_db(db_path)
    now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            UPDATE agent_runs
            SET updated_at = ?, status = ?, final_answer = ?, error = ?
            WHERE run_id = ?
            """,
            (now, status, (final_answer or "")[:20000], (error or "")[:2000], run_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_run(run_id: str, db_path: Path = DB_PATH) -> dict | None:
    _init_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        run = dict(row)
        steps = conn.execute(
            "SELECT * FROM agent_steps WHERE run_id = ? ORDER BY step_index",
            (run_id,),
        ).fetchall()
        run["steps"] = [dict(s) for s in steps]
        return run
    finally:
        conn.close()


def list_runs(limit: int = 20, db_path: Path = DB_PATH) -> list[dict]:
    _init_db(db_path)
    limit = max(1, min(int(limit), 100))
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM agent_runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def cleanup_runs(retention_days: int = 30, db_path: Path = DB_PATH) -> int:
    _init_db(db_path)
    cutoff = int(time.time()) - max(1, int(retention_days)) * 86400
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "DELETE FROM agent_steps WHERE run_id IN "
            "(SELECT run_id FROM agent_runs WHERE updated_at < ?)",
            (cutoff,),
        )
        conn.execute("DELETE FROM agent_runs WHERE updated_at < ?", (cutoff,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def _dump(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)[:50000]
    except (TypeError, ValueError):
        return json.dumps(str(value), ensure_ascii=False)[:50000]