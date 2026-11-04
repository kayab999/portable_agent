import shutil
import sqlite3
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from local_memory import TFIDFIndex
from maintenance import maintenance_cycle

TEST_DB_DIR = Path("tests/audit_data/vector_db")
TEST_DB_PATH = TEST_DB_DIR / "memory.db"


class TestMemoryIntegrity(unittest.TestCase):
    def setUp(self):
        if TEST_DB_DIR.exists():
            shutil.rmtree(TEST_DB_DIR)
        TEST_DB_DIR.mkdir(parents=True, exist_ok=True)
        self.memory = TFIDFIndex(db_path=TEST_DB_PATH)

        data = [
            {"source": "good.txt", "content": "good content stable", "quality_score": 0.9},
            {"source": "bad.txt", "content": "bad content prune me", "quality_score": 0.1},
        ]
        self.memory.index_docs(data)

        conn = sqlite3.connect(str(TEST_DB_PATH))
        conn.execute(
            "UPDATE chunks SET usage_count=0, reward=0.0 WHERE file_path='bad.txt'"
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.memory.close()
        if TEST_DB_DIR.exists():
            shutil.rmtree(TEST_DB_DIR)

    def test_maintenance_pruning(self):
        old_time = int(time.time()) - (30 * 24 * 60 * 60)
        conn = sqlite3.connect(str(TEST_DB_PATH))
        conn.execute(
            """UPDATE chunks SET is_active=1, last_accessed=?, quality_score=0.01,
                      usage_count=0, reward=0.0, failure_count=10, decay=0.1
               WHERE file_path='bad.txt'""",
            (old_time,),
        )
        conn.commit()
        conn.close()

        # Injectable path: operate only on TEST_DB_PATH
        summary1 = maintenance_cycle(db_path=TEST_DB_PATH, purge_documents=True)
        self.assertIsInstance(summary1, dict)

        conn = sqlite3.connect(str(TEST_DB_PATH))
        status = conn.execute(
            "SELECT is_active, quality_score FROM chunks WHERE file_path='bad.txt'"
        ).fetchone()
        remaining_after_1 = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        conn.close()

        # Soft-kill + hard-delete may complete in one cycle when last_accessed is already stale
        if status is not None:
            self.assertEqual(
                status[0],
                0,
                f"Chunk should be marked inactive (score={status[1]})",
            )
            summary2 = maintenance_cycle(db_path=TEST_DB_PATH, purge_documents=True)
            self.assertGreaterEqual(summary2.get("hard_deleted", 0), 1)
        else:
            self.assertGreaterEqual(summary1.get("hard_deleted", 0), 1)
            self.assertEqual(remaining_after_1, 1)

        conn = sqlite3.connect(str(TEST_DB_PATH))
        remaining = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        good = conn.execute(
            "SELECT count(*) FROM chunks WHERE file_path='good.txt'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(remaining, 1, f"Expected 1 chunk, found {remaining}")
        self.assertEqual(good, 1)

    def test_index_rebuild(self):
        conn = sqlite3.connect(str(TEST_DB_PATH))
        conn.execute("DELETE FROM doc_stats")
        conn.commit()
        conn.close()

        self.memory._load_index()
        self.assertEqual(self.memory._doc_count, 0)


if __name__ == "__main__":
    unittest.main()
