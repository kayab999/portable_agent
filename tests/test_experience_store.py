#!/usr/bin/env python3
"""Experience queue: idempotency, leasing, retry and dead-letter semantics."""

from __future__ import annotations

import shutil
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
