#!/usr/bin/env python3
"""local_memory.py — versioned local RAG store (SQLite FTS5/BM25).

Design goals
------------
* Zero runtime dependencies: SQLite FTS5 only.
* Idempotent ingestion keyed by ``(source_type, root, path)``.
* Documents and chunks are separate; chunk text is the FTS source of truth.
* Retrieval never falls back to arbitrary chunks and never returns inactive
  rows.
* Ranking is BM25. Quality/recency signals are only used by maintenance and
  are stored separately from the immutable ``quality_base`` so repeated
  maintenance cycles cannot drift.

The public surface (``TFIDFIndex``, ``get_memory``, ``index_docs``,
``retrieve``, ``chunk_text``, ``compute_score``, ``semantic_fingerprint``)
is intentionally preserved for compatibility with the rest of the runtime.
"""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path

# ─── PATHS ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.resolve()
DOCS_DIR = ROOT / "docs"
DB_DIR = ROOT / "data" / "vector_db"
DB_PATH = DB_DIR / "memory.db"

SCHEMA_VERSION = 2

# ─── TUNABLES ─────────────────────────────────────────────────────────────────
CHUNK_SIZE = 512
CHUNK_OVERLAP = 64
TOP_K = 3
MIN_TOKEN_LEN = 2
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_CHUNKS_PER_FILE = 2000
CONTEXT_MAX_CHARS = 6000
MAX_CHUNKS_PER_DOCUMENT = 2
INDEX_EXTENSIONS = frozenset({".txt", ".md", ".markdown", ".rst", ".csv", ".json"})

_STOP_WORDS = frozenset({"a", "al", "algo", "ademas", "asi", "aunque", "con", "como", "cuando", "de", "del", "donde", "e", "el", "ella", "en", "es", "esta", "este", "esto", "etc", "fue", "ha", "han", "hay", "he", "la", "las", "le", "les", "lo", "los", "mas", "me", "mi", "mis", "mucho", "muy", "nada", "ni", "no", "nos", "o", "para", "pero", "poco", "por", "porque", "que", "se", "sea", "ser", "si", "sin", "sobre", "son", "su", "sus", "tambien", "tan", "te", "tiene", "todo", "tu", "un", "una", "uno", "unos", "unas", "y", "ya", "yo", "an", "and", "are", "as", "at", "be", "but", "by", "do", "for", "from", "had", "has", "have", "her", "his", "how", "if", "in", "is", "it", "its", "just", "my", "not", "of", "on", "or", "our", "out", "own", "say", "she", "so", "some", "than", "that", "the", "their", "them", "then", "there", "these", "they", "this", "to", "too", "us", "use", "was", "we", "were", "what", "when", "which", "who", "will", "with", "would", "you", "your"})


# ─── PURE HELPERS ─────────────────────────────────────────────────────────────


def semantic_fingerprint(text: str) -> str:
    words = text.lower().split()
    words = [w for w in words if len(w) > 3]
    words = sorted(set(words))[:50]
    return hashlib.sha256(" ".join(words).encode()).hexdigest()[:32]


def compute_score(chunk: dict, now: int, local_density: int = 1) -> float:
    """Compute a retention score without mutating its own input.

    ``quality_base`` is the immutable owner-supplied quality. ``quality_score``
    is a derived snapshot and is deliberately *not* read here, otherwise every
    maintenance cycle would feed on its own output.
    """
    half_life = 7 * 24 * 60 * 60
    base = float(chunk.get("quality_base", chunk.get("quality_score")) or 0.5)
    usage = math.log1p(int(chunk.get("usage_count") or 0))
    reward = float(chunk.get("reward") or 0.0)
    failures = int(chunk.get("failure_count") or 0)

    last = chunk.get("last_accessed")
    created = chunk.get("created_at")
    age = now - (int(last) if last else (int(created) if created else now))

    recency = math.exp(-age / half_life)
    decay = float(chunk.get("decay") or 1.0)
    penalty = math.log1p(failures)

    reward_term = math.tanh(reward)
    quality_anchor = base + reward_term

    diversity_bonus = 1.0 / (1.0 + math.log1p(local_density))
    adjusted_diversity = diversity_bonus * (1.0 - math.tanh(quality_anchor))
    final_multiplier = 0.7 + (0.3 * adjusted_diversity)

    raw_score = (
        base * 0.25
        + usage * 0.2
        + recency * 0.15
        + decay * 0.1
        + reward_term * 0.3
        - penalty * 0.2
    )
    return raw_score * final_multiplier


def tokenize(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^a-záéíóúñü0-9\s]", " ", text)
    return [t for t in text.split() if t not in _STOP_WORDS and len(t) >= MIN_TOKEN_LEN]


def _token_string(text: str) -> str:
    return " ".join(sorted(set(tokenize(text))))


def chunk_text(
    text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP
) -> list[str]:
    """Split text into overlapping, paragraph-friendly word windows."""
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    step = max(1, chunk_size - overlap)
    for i in range(0, len(words), step):
        end = min(i + chunk_size, len(words))
        chunk = " ".join(words[i:end])
        if len(chunk.split()) >= 8:
            chunks.append(chunk)
        if end == len(words):
            break
        if len(chunks) >= MAX_CHUNKS_PER_FILE:
            break
    # Very short documents are still searchable.
    if not chunks:
        joined = " ".join(words).strip()
        if joined:
            chunks.append(joined)
    return chunks


def _fts_query(query: str) -> str:
    tokens = tokenize(query)
    # Quote every token: user/model text must never be able to inject FTS
    # operators (NEAR, *, :, ^, parentheses) into the MATCH expression.
    quoted = ['"' + t.replace('"', '""') + '"' for t in tokens]
    return " OR ".join(quoted)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


# ─── STORE ────────────────────────────────────────────────────────────────────


class TFIDFIndex:
    """Local RAG store. The historical name is kept for API compatibility."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._idf_cache: dict[str, float] = {}
        self._doc_count: int = 0
        self._doc_tokens: dict[int, Counter] = {}
        self._cache_lock = threading.RLock()
        self._init_db()
        self._load_index()

    # -- connections ---------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open a fresh connection. Never share one across gateway threads."""
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # -- schema --------------------------------------------------------------

    @staticmethod
    def _table_exists(c: sqlite3.Connection, name: str) -> bool:
        row = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
            (name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _columns(c: sqlite3.Connection, name: str) -> set[str]:
        return {row[1] for row in c.execute(f"PRAGMA table_info({name})")}

    def _init_db(self) -> None:
        c = self._connect()
        try:
            if not self._table_exists(c, "chunks"):
                self._create_v2_schema(c)
                c.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version', ?)",
                    (str(SCHEMA_VERSION),),
                )
                c.commit()
                return

            self._migrate_to_v2(c)
            c.commit()
        finally:
            c.close()

    def _create_v2_schema(self, c: sqlite3.Connection) -> None:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT NOT NULL DEFAULT 'document',
                path TEXT NOT NULL,
                root TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL,
                mtime INTEGER,
                size INTEGER,
                status TEXT NOT NULL DEFAULT 'active',
                indexed_at TEXT NOT NULL,
                error TEXT,
                UNIQUE(source_type, root, path)
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id     INTEGER REFERENCES documents(id) ON DELETE CASCADE,
                file_path       TEXT NOT NULL,
                chunk_index     INTEGER NOT NULL,
                ordinal         INTEGER NOT NULL DEFAULT 0,
                content         TEXT NOT NULL,
                file_hash       TEXT NOT NULL,
                model_origin    TEXT,
                hash            TEXT NOT NULL,
                usage_count     INTEGER NOT NULL DEFAULT 0,
                last_accessed   INTEGER NOT NULL,
                created_at      INTEGER NOT NULL,
                decay           REAL NOT NULL DEFAULT 1.0,
                quality_score   REAL NOT NULL DEFAULT 0.5,
                quality_base    REAL NOT NULL DEFAULT 0.5,
                version         INTEGER NOT NULL DEFAULT 1,
                is_active       INTEGER NOT NULL DEFAULT 1,
                reward          REAL NOT NULL DEFAULT 0.0,
                failure_count   INTEGER NOT NULL DEFAULT 0,
                token_count     INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS doc_stats (
                file_path   TEXT PRIMARY KEY,
                file_hash   TEXT NOT NULL,
                chunk_count INTEGER NOT NULL,
                indexed_at  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
            CREATE INDEX IF NOT EXISTS idx_chunks_active ON chunks(is_active);
            CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(file_path);
            CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                content,
                content='chunks',
                content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            );

            CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, content)
                    VALUES ('delete', old.id, old.content);
            END;
            CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE OF content ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, content)
                    VALUES ('delete', old.id, old.content);
                INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
            END;
            """
        )

    def _migrate_to_v2(self, c: sqlite3.Connection) -> None:
        c.execute(
            "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        if not self._table_exists(c, "documents"):
            c.execute(
                """CREATE TABLE documents (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       source_type TEXT NOT NULL DEFAULT 'document',
                       path TEXT NOT NULL,
                       root TEXT NOT NULL DEFAULT '',
                       content_hash TEXT NOT NULL,
                       mtime INTEGER,
                       size INTEGER,
                       status TEXT NOT NULL DEFAULT 'active',
                       indexed_at TEXT NOT NULL,
                       error TEXT,
                       UNIQUE(source_type, root, path)
                   )"""
            )
        cols = self._columns(c, "chunks")
        additions = {
            "document_id": "INTEGER REFERENCES documents(id) ON DELETE CASCADE",
            "ordinal": "INTEGER NOT NULL DEFAULT 0",
            "quality_base": "REAL NOT NULL DEFAULT 0.5",
            "token_count": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, decl in additions.items():
            if column not in cols:
                c.execute(f"ALTER TABLE chunks ADD COLUMN {column} {decl}")

        # Keep doc_stats as a compatibility summary while documents becomes
        # authoritative. Some legacy tooling/tests still query it.
        if not self._table_exists(c, "doc_stats"):
            c.execute(
                """CREATE TABLE doc_stats (
                       file_path TEXT PRIMARY KEY,
                       file_hash TEXT NOT NULL,
                       chunk_count INTEGER NOT NULL,
                       indexed_at TEXT NOT NULL
                   )"""
            )

        # Backfill one document per legacy file path.
        rows = c.execute(
            "SELECT file_path, MAX(file_hash) FROM chunks GROUP BY file_path"
        ).fetchall()
        for file_path, file_hash in rows:
            c.execute(
                """INSERT OR IGNORE INTO documents
                   (source_type, path, root, content_hash, status, indexed_at)
                   VALUES ('document', ?, '', ?, 'active', datetime('now'))""",
                (file_path, file_hash or _hash_text(file_path)),
            )
            doc = c.execute(
                """SELECT id FROM documents
                   WHERE source_type='document' AND root='' AND path=?""",
                (file_path,),
            ).fetchone()
            if doc is not None:
                c.execute(
                    "UPDATE chunks SET document_id=? WHERE file_path=? AND document_id IS NULL",
                    (doc[0], file_path),
                )

        c.execute(
            """INSERT OR REPLACE INTO doc_stats(file_path, file_hash, chunk_count, indexed_at)
               SELECT file_path, MAX(file_hash), COUNT(*), datetime('now')
               FROM chunks GROUP BY file_path"""
        )

        # Replace the fragile v1 token table/FTF pair with content-backed FTS.
        if self._table_exists(c, "fts_chunks"):
            c.execute("DROP TABLE IF EXISTS fts_chunks")
        if self._table_exists(c, "chunk_tokens"):
            c.execute("DROP TABLE IF EXISTS chunk_tokens")

        c.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
            CREATE INDEX IF NOT EXISTS idx_chunks_active ON chunks(is_active);
            CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(file_path);
            CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                content,
                content='chunks',
                content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            );

            CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, content)
                    VALUES ('delete', old.id, old.content);
            END;
            CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE OF content ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, content)
                    VALUES ('delete', old.id, old.content);
                INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
            END;

            INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version', '2');
            """
        )
        # Rebuild from chunks so migrated rows are searchable exactly once.
        c.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")

    # -- compatibility cache -------------------------------------------------

    def _load_index(self) -> None:
        """Refresh the legacy counters used by diagnostics/tests.

        Search reads SQLite directly; these fields are retained so older
        operator tooling and the audit suite keep working.
        """
        c = self._connect()
        try:
            row = c.execute("SELECT COUNT(*) FROM doc_stats").fetchone()
            doc_count = int(row[0]) if row else 0
        except sqlite3.Error:
            doc_count = 0
        finally:
            c.close()
        with self._cache_lock:
            self._doc_count = doc_count
            self._doc_tokens = {}
            self._idf_cache = {}

    def get_density_map(self) -> dict:
        c = self._connect()
        try:
            rows = c.execute(
                "SELECT hash, COUNT(*) FROM chunks WHERE is_active=1 GROUP BY hash"
            ).fetchall()
            return {r[0]: r[1] for r in rows}
        finally:
            c.close()

    # -- ingestion -----------------------------------------------------------

    def _delete_document_chunks(self, c: sqlite3.Connection, document_id: int) -> None:
        c.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))

    def _upsert_document(
        self,
        c: sqlite3.Connection,
        *,
        source_type: str,
        path: str,
        root: str,
        content: str,
        mtime: int | None,
        size: int | None,
        quality_base: float,
    ) -> tuple[str, int, int]:
        """Insert or replace one document atomically.

        Returns ``(status, document_id, chunk_count)`` where status is one of
        ``new``, ``updated`` or ``skipped``.
        """
        file_hash = _hash_text(content)
        row = c.execute(
            """SELECT id, content_hash, error FROM documents
               WHERE source_type=? AND root=? AND path=?""",
            (source_type, root, path),
        ).fetchone()

        if row is not None and row["content_hash"] == file_hash:
            # A source deleted by the operator stays deleted; a source merely
            # missing from disk is reactivated when it reappears unchanged.
            if row["error"] == "deleted_by_user":
                return "skipped", int(row["id"]), 0
            c.execute(
                """UPDATE documents
                   SET status='active', error=NULL, mtime=?, size=?
                   WHERE id=?""",
                (mtime, size, row["id"]),
            )
            c.execute(
                "UPDATE chunks SET is_active=1 WHERE document_id=?",
                (row["id"],),
            )
            return "skipped", int(row["id"]), 0

        if row is not None:
            document_id = int(row["id"])
            self._delete_document_chunks(c, document_id)
            c.execute(
                """UPDATE documents
                   SET content_hash=?, mtime=?, size=?, status='active',
                       indexed_at=datetime('now'), error=NULL
                   WHERE id=?""",
                (file_hash, mtime, size, document_id),
            )
            status = "updated"
        else:
            cursor = c.execute(
                """INSERT INTO documents
                   (source_type, path, root, content_hash, mtime, size,
                    status, indexed_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', datetime('now'))""",
                (source_type, path, root, file_hash, mtime, size),
            )
            document_id = int(cursor.lastrowid)
            status = "new"

        chunks = chunk_text(content)
        if not chunks:
            c.execute(
                "UPDATE documents SET error='empty document' WHERE id=?",
                (document_id,),
            )
            return "skipped", document_id, 0

        now = int(time.time())
        chunk_hash = semantic_fingerprint(content)
        for i, chunk in enumerate(chunks[:MAX_CHUNKS_PER_FILE]):
            c.execute(
                """INSERT INTO chunks
                   (document_id, file_path, chunk_index, ordinal, content,
                    file_hash, hash, created_at, last_accessed, quality_score,
                    quality_base, token_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    document_id,
                    path,
                    i,
                    i,
                    chunk,
                    file_hash,
                    chunk_hash,
                    now,
                    now,
                    quality_base,
                    quality_base,
                    len(tokenize(chunk)),
                ),
            )

        c.execute(
            """INSERT OR REPLACE INTO doc_stats
               (file_path, file_hash, chunk_count, indexed_at)
               VALUES (?, ?, ?, datetime('now'))""",
            (path, file_hash, len(chunks)),
        )
        return status, document_id, len(chunks)

    def index_docs(self, input_data: Path | list[dict]) -> dict:
        """Index a directory or a list of in-memory documents.

        List entries accept ``source`` (required), ``content`` (required),
        ``source_type`` (default ``document``) and ``quality_score``.
        Repeated calls are idempotent: unchanged content is skipped.
        """
        stats = {
            "indexed": 0,
            "skipped": 0,
            "new_files": 0,
            "updated_files": 0,
            "errors": [],
        }
        c = self._connect()
        try:
            if isinstance(input_data, list):
                for doc in input_data:
                    if "content" not in doc or "source" not in doc:
                        stats["errors"].append(
                            {"source": doc.get("source"), "error": "missing source/content"}
                        )
                        continue
                    source = str(doc["source"])
                    source_type = str(doc.get("source_type") or "document")
                    quality = float(doc.get("quality_score", 0.5))
                    status, _, count = self._upsert_document(
                        c,
                        source_type=source_type,
                        path=source,
                        root="",
                        content=str(doc["content"]),
                        mtime=None,
                        size=len(str(doc["content"]).encode("utf-8")),
                        quality_base=quality,
                    )
                    if status == "skipped":
                        stats["skipped"] += 1
                    else:
                        stats["indexed"] += count
                        stats[f"{status}_files"] += 1
                c.commit()
            else:
                docs_dir = Path(input_data)
                if not docs_dir.exists() or not docs_dir.is_dir():
                    return {**stats, "error": "docs/ folder not found"}
                root = docs_dir.resolve()
                seen: set[str] = set()
                for fpath in sorted(root.rglob("*")):
                    try:
                        if fpath.is_symlink():
                            resolved = fpath.resolve()
                            if not resolved.is_relative_to(root):
                                stats["errors"].append(
                                    {"source": str(fpath), "error": "symlink escapes root"}
                                )
                                continue
                        if not fpath.is_file():
                            continue
                        if fpath.suffix.lower() not in INDEX_EXTENSIONS:
                            continue
                        if fpath.stat().st_size > MAX_FILE_BYTES:
                            stats["errors"].append(
                                {"source": str(fpath), "error": "file too large"}
                            )
                            continue
                        raw = fpath.read_bytes()
                        try:
                            content = raw.decode("utf-8")
                        except UnicodeDecodeError:
                            stats["errors"].append(
                                {"source": str(fpath), "error": "not valid UTF-8"}
                            )
                            continue
                        rel = fpath.relative_to(root).as_posix()
                        seen.add(rel)
                        st = fpath.stat()
                        status, _, count = self._upsert_document(
                            c,
                            source_type="document",
                            path=rel,
                            root=str(root),
                            content=content,
                            mtime=int(st.st_mtime),
                            size=int(st.st_size),
                            quality_base=0.5,
                        )
                        if status == "skipped":
                            stats["skipped"] += 1
                        else:
                            stats["indexed"] += count
                            stats[f"{status}_files"] += 1
                    except OSError as exc:
                        stats["errors"].append(
                            {"source": str(fpath), "error": str(exc)}
                        )
                # Files removed from disk are soft-deleted, never silently
                # purged, so a re-index cannot destroy user data by accident.
                stale = c.execute(
                    """SELECT id, path FROM documents
                       WHERE source_type='document' AND root=? AND status='active'""",
                    (str(root),),
                ).fetchall()
                for doc_id, path in stale:
                    if path not in seen:
                        c.execute(
                            "UPDATE documents SET status='deleted', error='missing_file' WHERE id=?",
                            (doc_id,),
                        )
                        c.execute(
                            "UPDATE chunks SET is_active=0 WHERE document_id=?",
                            (doc_id,),
                        )
                c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
        self._load_index()
        return stats

    # -- retrieval -----------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = TOP_K,
        *,
        source_type: str | None = None,
        path_prefix: str | None = None,
    ) -> list[dict]:
        if top_k <= 0:
            return []
        fts_query = _fts_query(query)
        if not fts_query:
            return []

        c = self._connect()
        try:
            params: list[object] = [fts_query]
            filters = ["c.is_active = 1", "(d.id IS NULL OR d.status = 'active')"]
            if source_type:
                filters.append("d.source_type = ?")
                params.append(source_type)
            if path_prefix:
                # LIKE metacharacters are data here, not pattern syntax: a
                # prefix filter must not silently broaden its scope.
                escaped = (
                    str(path_prefix)
                    .replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
                filters.append("c.file_path LIKE ? ESCAPE '\\'")
                params.append(f"{escaped}%")
            params.append(max(top_k * 4, top_k))
            sql = f"""
                SELECT c.id, c.document_id, c.file_path, c.chunk_index,
                       c.content, c.hash, d.source_type,
                       bm25(chunks_fts) AS rank
                FROM chunks_fts
                JOIN chunks c ON c.id = chunks_fts.rowid
                LEFT JOIN documents d ON d.id = c.document_id
                WHERE chunks_fts MATCH ? AND {' AND '.join(filters)}
                ORDER BY rank ASC
                LIMIT ?
            """
            try:
                rows = c.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                return []

            results: list[dict] = []
            seen_hashes: set[str] = set()
            per_document: Counter = Counter()
            for row in rows:
                if row["hash"] in seen_hashes:
                    continue
                doc_key = row["document_id"] or row["file_path"]
                if per_document[doc_key] >= MAX_CHUNKS_PER_DOCUMENT:
                    continue
                seen_hashes.add(row["hash"])
                per_document[doc_key] += 1
                results.append(
                    {
                        "id": row["id"],
                        "document_id": row["document_id"],
                        "file": row["file_path"],
                        "path": row["file_path"],
                        "chunk": row["chunk_index"],
                        "score": round(-float(row["rank"]), 4),
                        "text": row["content"],
                        "source_type": row["source_type"] or "document",
                        "citation": f"{row['file_path']}#{row['chunk_index']}",
                    }
                )
                if len(results) >= top_k:
                    break
            return results
        finally:
            c.close()

    # -- browsing ------------------------------------------------------------

    def list_indexed(self) -> list[dict]:
        c = self._connect()
        try:
            rows = c.execute(
                """SELECT d.id, d.path, d.source_type, d.status, d.indexed_at,
                          COUNT(c.id) AS chunks
                   FROM documents d
                   LEFT JOIN chunks c ON c.document_id=d.id AND c.is_active=1
                   WHERE d.status='active'
                   GROUP BY d.id
                   ORDER BY d.indexed_at DESC, d.path ASC"""
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "file": r["path"],
                    "path": r["path"],
                    "source_type": r["source_type"],
                    "chunks": r["chunks"],
                    "indexed_at": r["indexed_at"],
                    "status": r["status"],
                }
                for r in rows
            ]
        finally:
            c.close()

    def list_sources(self, *, include_deleted: bool = False) -> list[dict]:
        c = self._connect()
        try:
            where = "" if include_deleted else "WHERE status='active'"
            rows = c.execute(
                f"""SELECT d.id, d.path, d.source_type, d.status, d.indexed_at,
                           d.error, COUNT(c.id) AS chunks
                    FROM documents d
                    LEFT JOIN chunks c ON c.document_id=d.id AND c.is_active=1
                    {where}
                    GROUP BY d.id
                    ORDER BY d.indexed_at DESC, d.path ASC"""
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            c.close()

    def delete_source(self, document_id: int, *, purge: bool = False) -> bool:
        c = self._connect()
        try:
            row = c.execute(
                "SELECT id FROM documents WHERE id=?", (document_id,)
            ).fetchone()
            if row is None:
                return False
            if purge:
                c.execute("DELETE FROM documents WHERE id=?", (document_id,))
                c.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
            else:
                c.execute(
                    "UPDATE documents SET status='deleted', error='deleted_by_user' WHERE id=?",
                    (document_id,),
                )
                c.execute(
                    "UPDATE chunks SET is_active=0 WHERE document_id=?",
                    (document_id,),
                )
            c.commit()
            return True
        finally:
            c.close()

    def status(self) -> dict:
        c = self._connect()
        try:
            documents = c.execute(
                "SELECT COUNT(*) FROM documents WHERE status='active'"
            ).fetchone()[0]
            chunks = c.execute(
                "SELECT COUNT(*) FROM chunks WHERE is_active=1"
            ).fetchone()[0]
            version_row = c.execute(
                "SELECT value FROM schema_meta WHERE key='version'"
            ).fetchone()
            last = c.execute(
                "SELECT MAX(indexed_at) FROM documents WHERE status='active'"
            ).fetchone()[0]
            try:
                size = self.db_path.stat().st_size
            except OSError:
                size = 0
            return {
                "schema_version": int(version_row[0]) if version_row else SCHEMA_VERSION,
                "documents": int(documents),
                "chunks": int(chunks),
                "db_bytes": size,
                "last_indexed": last,
            }
        finally:
            c.close()

    def clear(self) -> None:
        c = self._connect()
        try:
            c.executescript(
                "DELETE FROM chunks; DELETE FROM documents; DELETE FROM doc_stats;"
            )
            c.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
            c.commit()
        finally:
            c.close()
        with self._cache_lock:
            self._idf_cache = {}
            self._doc_tokens = {}
            self._doc_count = 0

    def close(self) -> None:
        """Compatibility no-op: connections are per operation now."""
        return None


# ─── CONTEXT FORMATTING ──────────────────────────────────────────────────────


def format_context(results: list[dict], max_chars: int = CONTEXT_MAX_CHARS) -> str:
    """Render retrieval hits as a bounded, citation-labelled context block."""
    if not results or max_chars <= 0:
        return ""
    parts: list[str] = []
    used = 0
    for result in results:
        header = f"[source: {result.get('path') or result.get('file')}#{result.get('chunk', 0)}]"
        body = str(result.get("text", ""))
        remaining = max_chars - used - len(header) - 2
        if remaining <= 0:
            break
        if len(body) > remaining:
            body = body[: max(0, remaining - 1)].rstrip() + "…"
        block = f"{header}\n{body}"
        parts.append(block)
        used += len(block) + 2
    return "\n\n".join(parts)


_memory: TFIDFIndex | None = None
_memory_path: Path | None = None
_memory_lock = threading.Lock()


def get_memory(db_path: Path | None = None) -> TFIDFIndex:
    """Return the shared index. Pass db_path to force a specific DB (tests)."""
    global _memory, _memory_path
    target = Path(db_path) if db_path is not None else DB_PATH
    with _memory_lock:
        if _memory is None or _memory_path != target:
            if _memory is not None:
                try:
                    _memory.close()
                except Exception:
                    pass
            _memory = TFIDFIndex(db_path=target)
            _memory_path = target
        return _memory


def index_docs(docs_dir: Path = DOCS_DIR, db_path: Path | None = None) -> dict:
    return get_memory(db_path).index_docs(docs_dir)


def retrieve(
    query: str,
    top_k: int = TOP_K,
    db_path: Path | None = None,
    *,
    max_chars: int = CONTEXT_MAX_CHARS,
) -> str:
    return format_context(
        get_memory(db_path).search(query, top_k), max_chars=max_chars
    )
