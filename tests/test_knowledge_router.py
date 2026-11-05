"""Knowledge router tests."""

from __future__ import annotations

import unittest

from knowledge_router import KnowledgeRouter, _normalize_query


class TestKnowledgeRouter(unittest.TestCase):
    def test_normalize_query_rejects_empty_and_long(self):
        with self.assertRaises(ValueError):
            _normalize_query("")
        with self.assertRaises(ValueError):
            _normalize_query("x" * 501)

    def test_router_returns_structured_results(self):
        class FakeDB:
            def search_concepts(self, query, limit=3):
                return [{"id": 1, "term": "entropy", "field": "physics", "definition": "measure of disorder"}]

            def search_formulas(self, query, limit=3):
                return []

            def search_constants(self, query, limit=3):
                return []

            def search_authors(self, query, limit=3):
                return []

            def search_history(self, query, limit=3):
                return []

        router = KnowledgeRouter(knowledge_db=FakeDB())
        result = router.query("definition of entropy", mode="structured", limit=3)
        self.assertTrue(result["ok"])
        self.assertEqual(result["source_type"], "knowledge_db")
        self.assertEqual(result["results"][0]["citation"], "knowledge_db:concepts:1")
        self.assertIn("entropy", result["results"][0]["content"])

    def test_router_falls_back_to_rag(self):
        def retriever(query, top_k=3):
            return [{"path": "doc.md", "content": "rag content", "citation": "rag:1"}]

        router = KnowledgeRouter(retriever=retriever)
        result = router.query("something", mode="rag", limit=2)
        self.assertEqual(result["source_type"], "rag")
        self.assertEqual(result["results"][0]["content"], "rag content")

    def test_router_hybrid_dedupes_citations(self):
        class FakeDB:
            def search_concepts(self, query, limit=3):
                return [{"id": 1, "term": "x", "field": "y", "definition": "z"}]

            def search_formulas(self, query, limit=3):
                return []

            def search_constants(self, query, limit=3):
                return []

            def search_authors(self, query, limit=3):
                return []

            def search_history(self, query, limit=3):
                return []

        def retriever(query, top_k=3):
            return [{"path": "doc.md", "content": "rag content", "citation": "knowledge_db:concepts:1"}]

        router = KnowledgeRouter(knowledge_db=FakeDB(), retriever=retriever)
        result = router.query("x", mode="hybrid", limit=5)
        self.assertEqual(result["count"], 1)

    def test_router_caps_content(self):
        class FakeDB:
            def search_concepts(self, query, limit=3):
                return [{"id": 1, "term": "x", "field": "y", "definition": "z" * 10000}]

            def search_formulas(self, query, limit=3):
                return []

            def search_constants(self, query, limit=3):
                return []

            def search_authors(self, query, limit=3):
                return []

            def search_history(self, query, limit=3):
                return []

        router = KnowledgeRouter(knowledge_db=FakeDB())
        result = router.query("x", mode="structured", limit=1)
        self.assertLessEqual(len(result["results"][0]["content"]), 1000)


if __name__ == "__main__":
    unittest.main()
