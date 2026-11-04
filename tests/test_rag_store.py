#!/usr/bin/env python3
"""RAG store correctness: schema, ingestion, retrieval and isolation."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from local_memory import TFIDFIndex, format_context


class TestRagStore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "memory.db"
        self.memory = TFIDFIndex(db_path=self.db)

    def tearDown(self):
        self.memory.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_schema_version_and_status(self):
        status = self.memory.status()
        self.assertEqual(status["schema_version"], 2)
        self.assertEqual(status["documents"], 0)
        self.assertEqual(status["chunks"], 0)

    def test_in_memory_ingestion_is_idempotent(self):
        docs = [
            {"source": "a.txt", "content": "alpha philosophy ontology " * 20},
            {"source": "b.txt", "content": "beta biology membrane " * 20},
        ]
        first = self.memory.index_docs(docs)
        self.assertEqual(first["indexed"], 2)
        second = self.memory.index_docs(docs)
        self.assertEqual(second["indexed"], 0)
        self.assertEqual(second["skipped"], 2)
        self.assertEqual(self.memory.status()["documents"], 2)

    def test_changed_content_replaces_chunks(self):
        self.memory.index_docs([{"source": "a.txt", "content": "old alpha " * 20}])
        stats = self.memory.index_docs(
            [{"source": "a.txt", "content": "new beta " * 20}]
        )
        self.assertEqual(stats["updated_files"], 1)
        self.assertEqual(self.memory.search("old"), [])
        self.assertTrue(self.memory.search("new beta"))

    def test_duplicate_basenames_are_distinct(self):
        root = self.tmp / "docs"
        (root / "one").mkdir(parents=True)
        (root / "two").mkdir(parents=True)
        (root / "one" / "same.txt").write_text("alpha ontology " * 30, encoding="utf-8")
        (root / "two" / "same.txt").write_text("beta biology " * 30, encoding="utf-8")
        stats = self.memory.index_docs(root)
        self.assertEqual(stats["new_files"], 2)
        self.assertEqual([r["path"] for r in self.memory.search("alpha")], ["one/same.txt"])
        self.assertEqual([r["path"] for r in self.memory.search("beta")], ["two/same.txt"])

    def test_inactive_chunks_never_returned(self):
        self.memory.index_docs(
            [{"source": "a.txt", "content": "alpha philosophy ontology " * 20}]
        )
        conn = sqlite3.connect(str(self.db))
        conn.execute("UPDATE chunks SET is_active=0")
        conn.commit()
        conn.close()
        self.assertEqual(self.memory.search("alpha"), [])

    def test_deleted_source_stays_deleted_on_reindex(self):
        root = self.tmp / "docs"
        root.mkdir()
        (root / "a.txt").write_text("alpha ontology " * 30, encoding="utf-8")
        self.memory.index_docs(root)
        document_id = self.memory.list_indexed()[0]["id"]
        self.assertTrue(self.memory.delete_source(document_id))
        self.assertEqual(self.memory.search("alpha"), [])
        self.memory.index_docs(root)
        self.assertEqual(self.memory.search("alpha"), [])
        deleted = self.memory.list_sources(include_deleted=True)
        self.assertEqual(deleted[0]["status"], "deleted")

    def test_missing_file_is_soft_deleted(self):
        root = self.tmp / "docs"
        root.mkdir()
        target = root / "a.txt"
        target.write_text("alpha ontology " * 30, encoding="utf-8")
        self.memory.index_docs(root)
        target.unlink()
        self.memory.index_docs(root)
        self.assertEqual(self.memory.search("alpha"), [])
        self.assertEqual(
            self.memory.list_sources(include_deleted=True)[0]["status"], "deleted"
        )

    def test_fts_operators_are_not_injected(self):
        self.memory.index_docs(
            [{"source": "a.txt", "content": "alpha ontology " * 30}]
        )
        # Must not raise and must not become a match-all wildcard.
        self.assertEqual(self.memory.search('alpha" OR *'), self.memory.search("alpha"))
        self.assertEqual(self.memory.search("NEAR(alpha)"), self.memory.search("near alpha"))

    def test_path_prefix_is_literal_not_a_like_pattern(self):
        self.memory.index_docs(
            [
                {"source": "a_b.txt", "content": "alpha ontology " * 20},
                {"source": "axb.txt", "content": "alpha biology " * 20},
            ]
        )
        hits = self.memory.search("alpha", path_prefix="a_")
        self.assertEqual([r["path"] for r in hits], ["a_b.txt"])
        # "%" and "_" are literal characters, not wildcards.
        self.assertEqual(self.memory.search("alpha", path_prefix="a%"), [])
        self.assertEqual(
            {r["path"] for r in self.memory.search("alpha", path_prefix="a")},
            {"a_b.txt", "axb.txt"},
        )

    def test_empty_query_returns_nothing(self):
        self.memory.index_docs([{"source": "a.txt", "content": "alpha " * 30}])
        self.assertEqual(self.memory.search(""), [])
        self.assertEqual(self.memory.search("   the and of "), [])

    def test_bm25_relevance(self):
        self.memory.index_docs(
            [
                {"source": "relevant.txt", "content": "quantum entanglement physics " * 20},
                {"source": "noise.txt", "content": "gardening tomatoes soil " * 20},
            ]
        )
        results = self.memory.search("quantum entanglement", top_k=2)
        self.assertEqual(results[0]["path"], "relevant.txt")
        self.assertTrue(all(r["path"] == "relevant.txt" for r in results))

    def test_context_budget_and_citations(self):
        self.memory.index_docs(
            [{"source": "a.txt", "content": "alpha ontology " * 100}]
        )
        results = self.memory.search("alpha", top_k=3)
        context = format_context(results, max_chars=80)
        self.assertLessEqual(len(context), 81)
        self.assertIn("[source: a.txt#", context)

    def test_unicode_and_accents(self):
        root = self.tmp / "docs"
        root.mkdir()
        (root / "filosofía.md").write_text(
            "La ontología estudia el ser y la epistemología el conocimiento. " * 10,
            encoding="utf-8",
        )
        self.memory.index_docs(root)
        self.assertTrue(self.memory.search("ontologia"))
        self.assertTrue(self.memory.search("epistemología"))


class TestRagMigration(unittest.TestCase):
    """A shipped rc.5 (schema v1) database migrates without losing content."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "memory.db"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _legacy_db(self) -> None:
        conn = sqlite3.connect(str(self.db))
        conn.executescript(
            """
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                model_origin TEXT,
                hash TEXT NOT NULL,
                usage_count INTEGER NOT NULL DEFAULT 0,
                last_accessed INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                decay REAL NOT NULL DEFAULT 1.0,
                quality_score REAL NOT NULL DEFAULT 0.5,
                version INTEGER NOT NULL DEFAULT 1,
                is_active INTEGER NOT NULL DEFAULT 1,
                reward REAL NOT NULL DEFAULT 0.0,
                failure_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE chunk_tokens (
                chunk_id INTEGER PRIMARY KEY,
                tokens TEXT NOT NULL
            );
            CREATE TABLE doc_stats (
                file_path TEXT PRIMARY KEY,
                file_hash TEXT NOT NULL,
                chunk_count INTEGER NOT NULL,
                indexed_at TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE fts_chunks USING fts5(
                tokens, content='chunk_tokens', content_rowid='chunk_id'
            );
            INSERT INTO chunks
                (file_path, chunk_index, content, file_hash, hash,
                 created_at, last_accessed)
            VALUES
                ('dir/a.txt', 0, 'alpha beta philosophy knowledge',
                 'hash-a', 'sem-a', 1, 1),
                ('dir/b.txt', 0, 'gamma biology membrane',
                 'hash-b', 'sem-b', 2, 2);
            INSERT INTO chunk_tokens VALUES
                (1, 'alpha beta philosophy knowledge'),
                (2, 'gamma biology membrane');
            INSERT INTO fts_chunks(rowid, tokens) VALUES
                (1, 'alpha beta philosophy knowledge'),
                (2, 'gamma biology membrane');
            INSERT INTO doc_stats VALUES
                ('dir/a.txt', 'hash-a', 1, datetime('now')),
                ('dir/b.txt', 'hash-b', 1, datetime('now'));
            """
        )
        conn.commit()
        conn.close()

    def test_v1_database_migrates_to_v2_and_stays_searchable(self):
        self._legacy_db()
        memory = TFIDFIndex(db_path=self.db)
        try:
            status = memory.status()
            self.assertEqual(status["schema_version"], 2)
            self.assertEqual(status["documents"], 2)
            self.assertEqual(status["chunks"], 2)

            conn = sqlite3.connect(str(self.db))
            conn.row_factory = sqlite3.Row
            try:
                version = conn.execute(
                    "SELECT value FROM schema_meta WHERE key='version'"
                ).fetchone()[0]
                self.assertEqual(version, "2")
                columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(chunks)")
                }
                self.assertTrue(
                    {"document_id", "ordinal", "quality_base", "token_count"}
                    <= columns
                )
                # Legacy token tables are replaced by content-backed FTS.
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertNotIn("chunk_tokens", tables)
                self.assertNotIn("fts_chunks", tables)
                self.assertIn("chunks_fts", tables)
                # Every migrated chunk points at a document row.
                orphans = conn.execute(
                    "SELECT COUNT(*) FROM chunks WHERE document_id IS NULL"
                ).fetchone()[0]
                self.assertEqual(orphans, 0)
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                self.assertEqual(integrity, "ok")
            finally:
                conn.close()

            self.assertEqual(
                [r["path"] for r in memory.search("philosophy")], ["dir/a.txt"]
            )
            self.assertEqual(
                [r["path"] for r in memory.search("biology")], ["dir/b.txt"]
            )

            # Re-opening is idempotent and does not duplicate documents.
            memory.close()
            memory = TFIDFIndex(db_path=self.db)
            self.assertEqual(memory.status()["documents"], 2)
        finally:
            memory.close()


if __name__ == "__main__":
    unittest.main()
