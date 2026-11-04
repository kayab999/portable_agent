#!/usr/bin/env python3
"""CRS maintenance: score refresh, soft-kill, hard-delete with FTS cleanup."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from local_memory import DB_PATH, compute_score, get_memory


def get_metrics(db_path: Path | None = None) -> dict:
    path = Path(db_path) if db_path is not None else DB_PATH
    conn = sqlite3.connect(str(path))
    try:
        cursor = conn.cursor()
        total = cursor.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        active = cursor.execute(
            "SELECT COUNT(*) FROM chunks WHERE is_active = 1"
        ).fetchone()[0]
        avg_score = (
            cursor.execute("SELECT AVG(quality_score) FROM chunks").fetchone()[0]
            or 0.0
        )
        used = cursor.execute(
            "SELECT COUNT(*) FROM chunks WHERE usage_count > 0"
        ).fetchone()[0]
        reward_dist = cursor.execute(
            "SELECT AVG(reward), MIN(reward), MAX(reward) FROM chunks"
        ).fetchone()

        metrics = {
            "timestamp": time.time(),
            "total": total,
            "active_ratio": active / total if total > 0 else 0,
            "avg_score": avg_score,
            "usage_rate": used / total if total > 0 else 0,
            "reward_stats": {
                "avg": reward_dist[0],
                "min": reward_dist[1],
                "max": reward_dist[2],
            },
        }
        log_file = Path("maintenance_log.jsonl")
        if log_file.exists() and log_file.stat().st_size > 1 * 1024 * 1024:
            log_file.rename("maintenance_log.old.jsonl")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(metrics) + "\n")
        print(f"--- Métricas de Salud Cognitiva ---\n{json.dumps(metrics, indent=2)}")
        return metrics
    finally:
        conn.close()


def maintenance_cycle(db_path: Path | None = None) -> dict:
    """
    Run maintenance on the given DB (default: production DB_PATH).
    Returns summary stats for tests and operators.
    """
    path = Path(db_path) if db_path is not None else DB_PATH
    mem = get_memory(path)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")

    summary = {"soft_killed": 0, "hard_deleted": 0, "rescored": 0}

    try:
        now = int(time.time())
        conn.execute(
            """
            UPDATE chunks SET
                created_at = COALESCE(created_at, ?),
                last_accessed = COALESCE(last_accessed, created_at),
                quality_score = COALESCE(quality_score, 0.5),
                decay = COALESCE(decay, 1.0),
                reward = COALESCE(reward, 0.0),
                failure_count = COALESCE(failure_count, 0),
                usage_count = COALESCE(usage_count, 0)
            WHERE created_at IS NULL OR quality_score IS NULL OR last_accessed IS NULL;
            """,
            (now,),
        )

        density_map = mem.get_density_map()
        chunks = conn.execute(
            """SELECT id, quality_score, usage_count, last_accessed, created_at,
                      decay, reward, failure_count, hash
               FROM chunks"""
        ).fetchall()

        for c in chunks:
            chunk_dict = {
                "quality_score": c[1],
                "usage_count": c[2],
                "last_accessed": c[3],
                "created_at": c[4],
                "decay": c[5],
                "reward": c[6],
                "failure_count": c[7],
            }
            local_density = density_map.get(c[8], 1)
            score = compute_score(chunk_dict, now, local_density=local_density)
            conn.execute(
                "UPDATE chunks SET quality_score = ? WHERE id = ?", (score, c[0])
            )
            summary["rescored"] += 1

        cur = conn.execute(
            """UPDATE chunks SET is_active = 0
               WHERE quality_score < 0.15 AND is_active = 1
                 AND usage_count < 3 AND reward < 0.05"""
        )
        summary["soft_killed"] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

        # Hard delete inactive chunks older than 7 days + clean FTS/tokens
        doomed = conn.execute(
            """SELECT id FROM chunks
               WHERE is_active = 0 AND last_accessed < ?""",
            (now - 604800,),
        ).fetchall()
        doomed_ids = [r[0] for r in doomed]
        if doomed_ids:
            placeholders = ",".join("?" * len(doomed_ids))
            conn.execute(
                f"DELETE FROM chunk_tokens WHERE chunk_id IN ({placeholders})",
                doomed_ids,
            )
            # FTS5 external content: delete by rowid
            conn.execute(
                f"DELETE FROM fts_chunks WHERE rowid IN ({placeholders})",
                doomed_ids,
            )
            conn.execute(
                f"DELETE FROM chunks WHERE id IN ({placeholders})",
                doomed_ids,
            )
            summary["hard_deleted"] = len(doomed_ids)

        conn.commit()
    finally:
        conn.close()

    # Refresh in-memory index after GC
    try:
        mem._load_index()
    except Exception:
        pass

    return summary


if __name__ == "__main__":
    print("Iniciando ciclo de validación de mantenimiento...")
    print(maintenance_cycle())
    get_metrics()
    print("Ciclo completado.")
