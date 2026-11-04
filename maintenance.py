#!/usr/bin/env python3
"""CRS maintenance: retention scoring, soft-kill and bounded garbage collection.

The maintenance loop must never feed on its own output. Ranking quality is
derived from the immutable ``quality_base`` column; ``quality_score`` is only
a snapshot used for retention decisions. Experience chunks may be garbage
collected automatically, but user documents are only purged when the operator
explicitly asks for it.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from local_memory import DB_PATH, compute_score, get_memory

RETENTION_DAYS = 7
SOFT_KILL_THRESHOLD = 0.15


def _log_path(db_path: Path) -> Path:
    return db_path.parent / "maintenance_log.jsonl"


def get_metrics(db_path: Path | None = None, *, log: bool = True) -> dict:
    path = Path(db_path) if db_path is not None else DB_PATH
    conn = sqlite3.connect(str(path), timeout=30)
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
            "active": active,
            "active_ratio": active / total if total > 0 else 0,
            "avg_score": avg_score,
            "usage_rate": used / total if total > 0 else 0,
            "reward_stats": {
                "avg": reward_dist[0],
                "min": reward_dist[1],
                "max": reward_dist[2],
            },
        }
        if log:
            log_file = _log_path(path)
            log_file.parent.mkdir(parents=True, exist_ok=True)
            if log_file.exists() and log_file.stat().st_size > 1024 * 1024:
                log_file.replace(log_file.with_suffix(".old.jsonl"))
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(metrics) + "\n")
        return metrics
    finally:
        conn.close()


def maintenance_cycle(
    db_path: Path | None = None,
    *,
    purge_documents: bool = False,
    dry_run: bool = False,
) -> dict:
    """Run one maintenance cycle.

    Parameters
    ----------
    purge_documents:
        When false (default), hard GC is limited to experience chunks. User
        documents are only soft-killed and can be restored or purged later.
    dry_run:
        Compute the summary without writing any row.
    """
    path = Path(db_path) if db_path is not None else DB_PATH
    mem = get_memory(path)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")

    summary = {
        "rescored": 0,
        "soft_killed": 0,
        "hard_deleted": 0,
        "dry_run": dry_run,
        "purge_documents": purge_documents,
    }

    try:
        now = int(time.time())
        conn.execute(
            """
            UPDATE chunks SET
                created_at = COALESCE(created_at, ?),
                last_accessed = COALESCE(last_accessed, created_at),
                quality_base = COALESCE(quality_base, quality_score, 0.5),
                quality_score = COALESCE(quality_score, quality_base, 0.5),
                decay = COALESCE(decay, 1.0),
                reward = COALESCE(reward, 0.0),
                failure_count = COALESCE(failure_count, 0),
                usage_count = COALESCE(usage_count, 0)
            WHERE created_at IS NULL OR quality_score IS NULL
               OR quality_base IS NULL OR last_accessed IS NULL;
            """,
            (now,),
        )

        density_map = mem.get_density_map()
        chunks = conn.execute(
            """SELECT id, quality_base, quality_score, usage_count, last_accessed,
                      created_at, decay, reward, failure_count, hash
               FROM chunks"""
        ).fetchall()

        for chunk in chunks:
            chunk_dict = {
                "quality_base": chunk["quality_base"],
                "quality_score": chunk["quality_score"],
                "usage_count": chunk["usage_count"],
                "last_accessed": chunk["last_accessed"],
                "created_at": chunk["created_at"],
                "decay": chunk["decay"],
                "reward": chunk["reward"],
                "failure_count": chunk["failure_count"],
            }
            local_density = density_map.get(chunk["hash"], 1)
            score = compute_score(chunk_dict, now, local_density=local_density)
            if not dry_run:
                conn.execute(
                    "UPDATE chunks SET quality_score = ? WHERE id = ?",
                    (score, chunk["id"]),
                )
            summary["rescored"] += 1

        if dry_run:
            summary["soft_killed"] = conn.execute(
                """SELECT COUNT(*) FROM chunks
                   WHERE quality_score < ? AND is_active = 1
                     AND usage_count < 3 AND reward < 0.05""",
                (SOFT_KILL_THRESHOLD,),
            ).fetchone()[0]
            return summary

        cur = conn.execute(
            """UPDATE chunks SET is_active = 0
               WHERE quality_score < ? AND is_active = 1
                 AND usage_count < 3 AND reward < 0.05""",
            (SOFT_KILL_THRESHOLD,),
        )
        summary["soft_killed"] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

        cutoff = now - RETENTION_DAYS * 24 * 60 * 60
        if purge_documents:
            doomed = conn.execute(
                """SELECT id FROM chunks
                   WHERE is_active = 0 AND last_accessed < ?""",
                (cutoff,),
            ).fetchall()
        else:
            doomed = conn.execute(
                """SELECT c.id FROM chunks c
                   JOIN documents d ON d.id = c.document_id
                   WHERE c.is_active = 0 AND c.last_accessed < ?
                     AND d.source_type = 'experience'""",
                (cutoff,),
            ).fetchall()
        doomed_ids = [row[0] for row in doomed]
        if doomed_ids:
            placeholders = ",".join("?" * len(doomed_ids))
            # Deleting chunks fires the FTS delete trigger; never touch
            # chunks_fts manually here.
            conn.execute(
                f"DELETE FROM chunks WHERE id IN ({placeholders})",
                doomed_ids,
            )
            summary["hard_deleted"] = len(doomed_ids)

        conn.commit()
    finally:
        conn.close()

    try:
        mem._load_index()
    except Exception:
        pass

    return summary


if __name__ == "__main__":
    print("Iniciando ciclo de validación de mantenimiento...")
    print(maintenance_cycle())
    print(get_metrics())
    print("Ciclo completado.")
