"""knowledge_router.py — deterministic read-only router for the knowledge base.

The router is the only production entry point for structured knowledge. It
combines exact-ish FTS lookups with the existing RAG store and returns a
normalized, citation-bearing result set.
"""

from __future__ import annotations

from typing import Any

MAX_QUERY_CHARS = 500
MAX_RESULTS = 10
MAX_CONTENT_CHARS = 8000

_TABLES = ("formulas", "history", "constants", "authors", "concepts")

_STOP_WORDS = frozenset({"de", "la", "el", "y", "en", "a", "the", "of", "to"})


def _normalize_query(query: str) -> str:
    if not isinstance(query, str):
        raise ValueError("query must be a string")
    query = query.strip()
    if not query:
        raise ValueError("query must not be empty")
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError(f"query exceeds {MAX_QUERY_CHARS} characters")
    # Drop control characters; keep printable text.
    query = "".join(ch for ch in query if ch.isprintable())
    if not query.strip():
        raise ValueError("query must not be empty")
    return query.strip()


def _classify(query: str) -> str:
    lowered = query.lower()
    if any(token in lowered for token in ("formula", "ecuacion", "equation")):
        return "formulas"
    if any(token in lowered for token in ("constante", "constant", "pi", "speed of light")):
        return "constants"
    if any(token in lowered for token in ("autor", "author", "writer", "philosopher")):
        return "authors"
    if any(token in lowered for token in ("concepto", "concept", "definition", "definicion")):
        return "concepts"
    if any(token in lowered for token in ("historia", "history", "year", "siglo", "century")):
        return "history"
    return "all"


def _citation(table: str, row: dict[str, Any]) -> str:
    if table == "formulas":
        return f"knowledge_db:formulas:{row.get('id')}"
    if table == "constants":
        return f"knowledge_db:constants:{row.get('id')}"
    if table == "authors":
        return f"knowledge_db:authors:{row.get('id')}"
    if table == "concepts":
        return f"knowledge_db:concepts:{row.get('id')}"
    if table == "history":
        return f"knowledge_db:history:{row.get('id')}"
    return f"knowledge_db:{table}:{row.get('id')}"


def _row_to_result(table: str, row: dict[str, Any]) -> dict[str, Any]:
    if table == "formulas":
        content = f"{row.get('name')}: {row.get('formula')}"
        if row.get("variables"):
            content += f" (variables: {row.get('variables')})"
    elif table == "constants":
        content = f"{row.get('name')} ({row.get('symbol')}): {row.get('value')} {row.get('unit') or ''}".strip()
    elif table == "authors":
        content = f"{row.get('name')} ({row.get('birth')}-{row.get('death')}): {row.get('key_works')}"
    elif table == "concepts":
        content = f"{row.get('term')}: {row.get('definition')}"
    elif table == "history":
        content = f"[{row.get('year')}] {row.get('event')}"
    else:
        content = str(row)
    return {
        "source_type": "knowledge_db",
        "table": table,
        "id": row.get("id"),
        "title": row.get("name") or row.get("term") or row.get("event") or str(row.get("id")),
        "content": content[:1000],
        "citation": _citation(table, row),
    }


class KnowledgeRouter:
    """Read-only router over structured knowledge and optional RAG."""

    def __init__(self, knowledge_db=None, retriever=None):
        self._knowledge_db = knowledge_db
        self._retriever = retriever

    def _knowledge_db_instance(self):
        if self._knowledge_db is not None:
            return self._knowledge_db
        try:
            from knowledge_db import get_knowledge

            self._knowledge_db = get_knowledge()
        except Exception:
            self._knowledge_db = None
        return self._knowledge_db

    def _rag_retrieve(self, query: str, limit: int) -> list[dict[str, Any]]:
        if self._retriever is None:
            try:
                from local_memory import retrieve

                self._retriever = retrieve
            except Exception:
                self._retriever = None
        if self._retriever is None:
            return []
        try:
            rows = self._retriever(query, top_k=limit)
        except Exception:
            return []
        results = []
        for row in rows or []:
            results.append(
                {
                    "source_type": "rag",
                    "table": None,
                    "id": row.get("id") if isinstance(row, dict) else None,
                    "title": (row.get("path") if isinstance(row, dict) else None) or "rag",
                    "content": (row.get("content") if isinstance(row, dict) else str(row))[:1000],
                    "citation": (row.get("citation") if isinstance(row, dict) else None) or "rag",
                }
            )
        return results

    def query(self, query: str, *, mode: str = "auto", limit: int = 5) -> dict[str, Any]:
        query = _normalize_query(query)
        if mode not in {"auto", "structured", "rag", "hybrid"}:
            raise ValueError("mode must be auto, structured, rag, or hybrid")
        limit = max(1, min(int(limit), MAX_RESULTS))

        route = _classify(query) if mode == "auto" else mode
        structured_results: list[dict[str, Any]] = []
        rag_results: list[dict[str, Any]] = []

        if route in {"formulas", "constants", "authors", "concepts", "history", "structured", "hybrid", "all"}:
            db = self._knowledge_db_instance()
            if db is not None:
                try:
                    if route in {"formulas", "all", "structured", "hybrid"}:
                        for row in db.search_formulas(query, limit):
                            structured_results.append(_row_to_result("formulas", row))
                    if route in {"constants", "all", "structured", "hybrid"}:
                        for row in db.search_constants(query, limit):
                            structured_results.append(_row_to_result("constants", row))
                    if route in {"authors", "all", "structured", "hybrid"}:
                        for row in db.search_authors(query, limit):
                            structured_results.append(_row_to_result("authors", row))
                    if route in {"concepts", "all", "structured", "hybrid"}:
                        for row in db.search_concepts(query, limit):
                            structured_results.append(_row_to_result("concepts", row))
                    if route in {"history", "all", "structured", "hybrid"}:
                        for row in db.search_history(query, limit):
                            structured_results.append(_row_to_result("history", row))
                except Exception:
                    structured_results = []

        if route in {"rag", "hybrid"} or (route == "auto" and not structured_results):
            rag_results = self._rag_retrieve(query, limit)

        # Deduplicate by citation while preserving order.
        seen: set[str] = set()
        merged: list[dict[str, Any]] = []
        for result in structured_results + rag_results:
            citation = result.get("citation") or ""
            if citation and citation in seen:
                continue
            if citation:
                seen.add(citation)
            merged.append(result)
            if len(merged) >= limit:
                break

        # Truncate total content.
        total = 0
        final: list[dict[str, Any]] = []
        for result in merged:
            if total + len(result.get("content", "")) > MAX_CONTENT_CHARS:
                result = dict(result)
                result["truncated"] = True
                remaining = MAX_CONTENT_CHARS - total
                if remaining <= 0:
                    break
                result["content"] = result["content"][:remaining]
            final.append(result)
            total += len(result.get("content", ""))

        source_type = "hybrid" if structured_results and rag_results else (
            "knowledge_db" if structured_results else "rag" if rag_results else "none"
        )
        return {
            "ok": True,
            "query": query,
            "route": route,
            "source_type": source_type,
            "count": len(final),
            "results": final,
        }