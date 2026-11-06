#!/usr/bin/env python3
"""evaluate_rag.py — deterministic Recall@k / MRR harness for the local BM25 RAG.

The harness uses the production ``TFIDFIndex`` (SQLite FTS5/BM25). It does not
introduce embeddings or a second retrieval implementation. Results are written
as JSON so a release can compare runs.

Usage:
    python scripts/evaluate_rag.py --output dist/rag_eval.json
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from local_memory import TFIDFIndex  # noqa: E402

DEFAULT_K = (1, 3, 5)
MAX_K = 5

CORPUS = [
    {
        "source": "fotosintesis.txt",
        "content": (
            "La fotosíntesis convierte luz solar, agua y dióxido de carbono en "
            "glucosa y oxígeno. La clorofila de los cloroplastos absorbe la luz. "
        )
        * 8,
    },
    {
        "source": "mcp.txt",
        "content": (
            "El protocolo MCP usa JSON-RPC sobre stdio. El cliente moderno envía "
            "server/discover y metadatos por petición; los servidores legacy usan "
            "el handshake initialize. Las herramientas se aíslan con namespaces. "
        )
        * 8,
    },
    {
        "source": "presupuestos.txt",
        "content": (
            "El runtime limita max_tool_calls y max_runtime_seconds. Un presupuesto "
            "agotado no puede declararse como éxito verificado. Los resultados de "
            "herramientas son la evidencia objetiva de finalización. "
        )
        * 8,
    },
    {
        "source": "vault.txt",
        "content": (
            "El vault deriva claves con PBKDF2-HMAC-SHA256 y 600000 iteraciones. "
            "El cifrado usa Encrypt-then-MAC con nonce aleatorio y etiqueta HMAC. "
            "La comparación de secretos es en tiempo constante. "
        )
        * 8,
    },
    {
        "source": "ssrf.txt",
        "content": (
            "fetch_url rechaza loopback, rangos privados, link-local, CGNAT y "
            "destinos no globales. La conexión se fija a la IP validada para evitar "
            "DNS rebinding; cada redirección se valida antes de conectar. "
        )
        * 8,
    },
]

QUERIES = [
    {"query": "fotosíntesis clorofila", "expected": ["fotosintesis.txt"]},
    {"query": "MCP JSON-RPC stdio legacy initialize", "expected": ["mcp.txt"]},
    {"query": "presupuesto max_tool_calls max_runtime_seconds", "expected": ["presupuestos.txt"]},
    {"query": "PBKDF2 iteraciones vault", "expected": ["vault.txt"]},
    {"query": "DNS rebinding redirecciones SSRF", "expected": ["ssrf.txt"]},
]

MALFORMED_QUERIES = ["", "   ", "*", '" OR *', "--", "ñ" * 10_000]


def _recall_at_k(paths: set[str], expected: set[str], k: int) -> float:
    return 1.0 if paths & expected else 0.0


def evaluate(index: TFIDFIndex, queries: list[dict]) -> dict:
    metrics: dict[str, float] = {f"recall@{k}": 0.0 for k in DEFAULT_K}
    reciprocal_rank = 0.0
    details: list[dict] = []
    for item in queries:
        expected = set(item["expected"])
        results = index.search(item["query"], top_k=MAX_K)
        paths = [str(result.get("path") or "") for result in results]
        for k in DEFAULT_K:
            metrics[f"recall@{k}"] += _recall_at_k(set(paths[:k]), expected, k)
        rr = 0.0
        for rank, path in enumerate(paths, start=1):
            if path in expected:
                rr = 1.0 / rank
                break
        reciprocal_rank += rr
        details.append(
            {
                "query": item["query"],
                "expected": sorted(expected),
                "returned": paths,
                "reciprocal_rank": rr,
            }
        )
    count = max(1, len(queries))
    for key in metrics:
        metrics[key] = round(metrics[key] / count, 4)
    metrics["mrr"] = round(reciprocal_rank / count, 4)
    return {"metrics": metrics, "queries": details}


def run(corpus: list[dict] | None = None, queries: list[dict] | None = None) -> dict:
    corpus = corpus if corpus is not None else CORPUS
    queries = queries if queries is not None else QUERIES
    with tempfile.TemporaryDirectory() as td:
        index = TFIDFIndex(db_path=Path(td) / "rag_eval.db")
        try:
            index.index_docs(corpus)
            report = evaluate(index, queries)
            malformed_errors = 0
            for query in MALFORMED_QUERIES:
                try:
                    index.search(query)
                except Exception:
                    malformed_errors += 1
            report["malformed_query_errors"] = malformed_errors
            report["documents"] = len(corpus)
            return report
        finally:
            index.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="", help="write JSON report to this path")
    args = parser.parse_args(argv)
    report = run()
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    print(rendered)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
        print(f"wrote {output}")
    return 0 if report["malformed_query_errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
