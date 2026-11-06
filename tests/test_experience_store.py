#!/usr/bin/env python3
"""Experience queue: idempotency, leasing, retry and dead-letter semantics."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import experience_store


class TestExperienceStore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "memory.db"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_schema_migration_adds_category_to_legacy_events(self):
        conn = sqlite3.connect(self.db)
        conn.executescript(
            """
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                available_at INTEGER NOT NULL,
                locked_by TEXT,
                locked_at INTEGER,
                last_error TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                processed_at INTEGER
            );
            INSERT INTO events
                (event_type, payload, idempotency_key, available_at,
                 created_at, updated_at)
            VALUES ('PERSIST', '{}', 'legacy-key', 1, 1, 1);
            """
        )
        conn.commit()
        experience_store.ensure_schema(conn)
        row = conn.execute(
            "SELECT category FROM events WHERE idempotency_key = 'legacy-key'"
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "unverified_experience")

    def test_experience_category_is_derived_and_persisted(self):
        cases = {
            "verified_result": {
                "provenance": {"category": "verified_result"},
                "tool_results": [{"ok": True}],
            },
            "tool_observation": {"tool_results": [{"ok": True}]},
            "model_assertion": {"success": True},
            "unverified_experience": {"success": False},
        }
        for expected, payload in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(
                    experience_store.classify_experience(payload), expected
                )
                experience_store.enqueue("PERSIST", payload, db_path=self.db)
                conn = experience_store._connect(self.db)
                try:
                    row = conn.execute(
                        "SELECT category FROM events ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                finally:
                    conn.close()
                self.assertEqual(row["category"], expected)

    def test_enqueue_is_idempotent(self):
        first = experience_store.enqueue("PERSIST", {"query": "a"}, db_path=self.db)
        second = experience_store.enqueue("PERSIST", {"query": "a"}, db_path=self.db)
        self.assertGreater(first, 0)
        self.assertEqual(second, 0)
        self.assertEqual(experience_store.status(self.db)["pending"], 1)

    def test_drain_completes_once(self):
        seen = []
        experience_store.enqueue("PERSIST", {"query": "a"}, db_path=self.db)
        summary = experience_store.drain(
            lambda event, payload: seen.append((event["id"], payload["query"])),
            db_path=self.db,
        )
        self.assertEqual(summary, {"processed": 1, "failed": 0, "dead": 0})
        self.assertEqual(seen, [(1, "a")])
        again = experience_store.drain(lambda event, payload: seen.append(1), db_path=self.db)
        self.assertEqual(again["processed"], 0)
        self.assertEqual(experience_store.status(self.db)["done"], 1)

    def test_failure_backs_off_and_dead_letters(self):
        experience_store.enqueue("PERSIST", {"query": "a"}, db_path=self.db)

        def boom(event, payload):
            raise RuntimeError("downstream unavailable")

        first = experience_store.drain(boom, db_path=self.db, max_attempts=2)
        self.assertEqual(first["failed"], 1)
        self.assertEqual(experience_store.status(self.db)["pending"], 1)
        # Backoff means it is not claimable immediately.
        self.assertEqual(
            experience_store.drain(boom, db_path=self.db, max_attempts=2)["failed"], 0
        )
        conn = experience_store._connect(self.db)
        conn.execute("UPDATE events SET available_at=0")
        conn.close()
        second = experience_store.drain(boom, db_path=self.db, max_attempts=2)
        self.assertEqual(second["dead"], 1)
        self.assertEqual(experience_store.status(self.db)["dead"], 1)

    def test_stale_lease_is_reclaimed(self):
        experience_store.enqueue("PERSIST", {"query": "a"}, db_path=self.db)
        conn = experience_store._connect(self.db)
        conn.execute(
            "UPDATE events SET status='processing', locked_by='crashed', locked_at=0"
        )
        conn.close()
        summary = experience_store.drain(
            lambda event, payload: None, db_path=self.db
        )
        self.assertEqual(summary["processed"], 1)

    def test_concurrent_drains_do_not_duplicate(self):
        for i in range(60):
            experience_store.enqueue("PERSIST", {"i": i}, db_path=self.db)
        seen = []
        lock = threading.Lock()

        def worker():
            def handler(event, payload):
                with lock:
                    seen.append(event["id"])

            experience_store.drain(handler, db_path=self.db, max_events=10)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(seen), 60)
        self.assertEqual(len(set(seen)), 60)

    def test_purge_only_old_terminal_events(self):
        experience_store.enqueue("PERSIST", {"query": "a"}, db_path=self.db)
        experience_store.drain(lambda event, payload: None, db_path=self.db)
        self.assertEqual(
            experience_store.purge(db_path=self.db, older_than_seconds=3600), 0
        )
        conn = experience_store._connect(self.db)
        conn.execute("UPDATE events SET updated_at=0")
        conn.close()
        self.assertEqual(
            experience_store.purge(db_path=self.db, older_than_seconds=0), 1
        )


if __name__ == "__main__":
    unittest.main()
