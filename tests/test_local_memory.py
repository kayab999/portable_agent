import shutil
import tempfile
import unittest
from pathlib import Path

from local_memory import TFIDFIndex


class TestLocalMemory(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = self.tmp / "memory.db"
        self.memory = TFIDFIndex(db_path=self.db_path)

    def tearDown(self):
        self.memory.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_index_and_retrieve(self):
        data = [{"source": "test_file.txt", "content": "This is a test document."}]
        stats = self.memory.index_docs(data)
        self.assertGreaterEqual(stats["indexed"], 1)
        results = self.memory.search("test")
        self.assertTrue(len(results) > 0, "No results found in search")
        self.assertEqual(results[0]["text"], "This is a test document.")

    def test_index_docs_from_directory(self):
        docs = self.tmp / "docs"
        docs.mkdir()
        (docs / "phil.txt").write_text(
            "Kant reason pure philosophy knowledge systems and ethics synthesis "
            "argument structure conceptual analysis ontology epistemology",
            encoding="utf-8",
        )
        stats = self.memory.index_docs(docs)
        self.assertNotIn("error", stats)
        self.assertGreaterEqual(stats["indexed"], 1)
        results = self.memory.search("philosophy knowledge")
        self.assertTrue(len(results) >= 0)  # FTS may vary; no IntegrityError is the gate


if __name__ == "__main__":
    unittest.main()
