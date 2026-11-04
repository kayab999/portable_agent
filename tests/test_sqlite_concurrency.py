#!/usr/bin/env python3
"""Concurrent memory access must not share SQLite connections across threads."""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from local_memory import TFIDFIndex


class TestSQLiteConcurrency(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "memory.db"
        self.memory = TFIDFIndex(db_path=self.db)
        docs = [
            {
                "source": f"doc_{i}.txt",
                "content": (
                    f"philosophy knowledge document number {i} "
                    "reason ontology epistemology synthesis argument"
                ),
            }
            for i in range(20)
        ]
        self.memory.index_docs(docs)

    def tearDown(self):
        self.memory.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_64_concurrent_searches(self):
        def work(_):
            return self.memory.search("philosophy knowledge", top_k=5)

        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(work, range(64)))
        self.assertEqual(len(results), 64)
        self.assertTrue(all(isinstance(r, list) for r in results))

    def test_concurrent_reads_and_writes(self):
        def search(_):
            return len(self.memory.search("ontology", top_k=3))

        def write(i):
            return self.memory.index_docs(
                [
                    {
                        "source": f"concurrent_{i}.txt",
                        "content": f"concurrent write {i} epistemology argument structure",
                    }
                ]
            )

        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = []
            for i in range(16):
                futures.append(pool.submit(search, i))
                futures.append(pool.submit(write, i))
            for f in futures:
                f.result()

        conn = sqlite3.connect(str(self.db))
        try:
            count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
        self.assertGreaterEqual(count, 36)
        self.assertEqual(integrity, "ok")


if __name__ == "__main__":
    unittest.main()
