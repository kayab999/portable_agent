#!/usr/bin/env python3
"""
snapshot_from_db.py

Crea un snapshot versionado e inmutable de una base SQLite de conocimiento.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

def count_rows(conn: sqlite3.Connection, table: str) -> Optional[int]:
    try:
        cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
        row = cur.fetchone()
        return int(row[0]) if row else None
    except sqlite3.Error:
        return None

def build_manifest(source_db: Path, snapshot_db: Path) -> Dict[str, Any]:
    manifest: Dict[str, Any] = {
        "created_at": utc_now_iso(),
        "source_db": str(source_db),
        "snapshot_db": str(snapshot_db),
        "source_sha256": sha256_file(source_db),
        "snapshot_sha256": sha256_file(snapshot_db),
        "tables": {},
    }
    with sqlite3.connect(str(snapshot_db)) as conn:
        conn.row_factory = sqlite3.Row
        tables = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        for t in tables:
            manifest["tables"][t] = {"row_count": count_rows(conn, t)}
    return manifest

def snapshot_sqlite(source_db: Path, snapshots_root: Path, version: str, force: bool = False) -> Path:
    if not source_db.exists():
        raise FileNotFoundError(f"No existe la DB origen: {source_db}")
    snapshot_dir = snapshots_root / version
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_db = snapshot_dir / "memory.db"
    manifest_path = snapshot_dir / "manifest.json"
    if snapshot_db.exists() and not force:
        raise FileExistsError(f"El snapshot ya existe: {snapshot_db}. Usa --force.")
    with sqlite3.connect(str(source_db)) as src_conn:
        with sqlite3.connect(str(snapshot_db)) as dst_conn:
            src_conn.backup(dst_conn)
    manifest = build_manifest(source_db, snapshot_db)
    manifest["version"] = version
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return snapshot_db

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-db", required=True)
    parser.add_argument("--snapshots-root", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    snapshot_sqlite(Path(args.source_db), Path(args.snapshots_root), args.version, args.force)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
