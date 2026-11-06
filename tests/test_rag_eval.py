"""RAG evaluation harness tests (no embeddings, production BM25 index)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import evaluate_rag  # noqa: E402


class TestRagEval(unittest.TestCase):
    def test_small_corpus_is_retrieved_perfectly(self):
        report = evaluate_rag.run(
            corpus=[
                {"source": "alpha.txt", "content": "alpha ontology " * 40},
                {"source": "beta.txt", "content": "beta biology " * 40},
            ],
            queries=[
                {"query": "alpha ontology", "expected": ["alpha.txt"]},
                {"query": "beta biology", "expected": ["beta.txt"]},
            ],
        )
        self.assertEqual(report["metrics"]["recall@1"], 1.0)
        self.assertEqual(report["metrics"]["mrr"], 1.0)
        self.assertEqual(report["malformed_query_errors"], 0)

    def test_default_corpus_meets_baseline(self):
        report = evaluate_rag.run()
        self.assertGreaterEqual(report["metrics"]["recall@1"], 0.8)
        self.assertGreaterEqual(report["metrics"]["mrr"], 0.8)


if __name__ == "__main__":
    unittest.main()
