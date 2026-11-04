#!/usr/bin/env python3
import re
import math
import time
import sqlite3
import hashlib
import threading
from pathlib import Path
from collections import Counter

# ─── PATHS ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.resolve()
DOCS_DIR = ROOT / "docs"
DB_DIR = ROOT / "data" / "vector_db"
DB_PATH = DB_DIR / "memory.db"

# ─── LOGIC FUNCTIONS ──────────────────────────────────────────────────────────

def semantic_fingerprint(text: str) -> str:
    words = text.lower().split()
    words = [w for w in words if len(w) > 3]
    words = sorted(set(words))[:50]
    return hashlib.sha256(" ".join(words).encode()).hexdigest()[:32]

def compute_score(chunk: dict, now: int, local_density: int = 1) -> float:
    HALF_LIFE = 7 * 24 * 60 * 60
    base = float(chunk.get("quality_score") or 0.5)
    usage = math.log1p(int(chunk.get("usage_count") or 0))
    reward = float(chunk.get("reward") or 0.0)
    failures = int(chunk.get("failure_count") or 0)
    
    last = chunk.get("last_accessed")
    created = chunk.get("created_at")
    age = now - (int(last) if last else (int(created) if created else now))
    
    recency = math.exp(-age / HALF_LIFE)
    decay = float(chunk.get("decay") or 1.0)
    penalty = math.log1p(failures)
    
    reward_term = math.tanh(reward)
    quality_anchor = base + reward_term
    
    diversity_bonus = 1.0 / (1.0 + math.log1p(local_density))
    adjusted_diversity = diversity_bonus * (1.0 - math.tanh(quality_anchor))
    final_multiplier = 0.7 + (0.3 * adjusted_diversity)
    
    raw_score = (base * 0.25 + usage * 0.2 + recency * 0.15 + decay * 0.1 + reward_term * 0.3 - penalty * 0.2)
    
    return raw_score * final_multiplier

# ─── TOKENIZER ────────────────────────────────────────────────────────────────

CHUNK_SIZE = 512
CHUNK_OVERLAP = 64
TOP_K = 3
MIN_TOKEN_LEN = 2

_STOP_WORDS = frozenset({"a", "al", "algo", "ademas", "asi", "aunque", "con", "como", "cuando", "de", "del", "donde", "e", "el", "ella", "en", "es", "esta", "este", "esto", "etc", "fue", "ha", "han", "hay", "he", "la", "las", "le", "les", "lo", "los", "mas", "me", "mi", "mis", "mucho", "muy", "nada", "ni", "no", "nos", "o", "para", "pero", "poco", "por", "porque", "que", "se", "sea", "ser", "si", "sin", "sobre", "son", "su", "sus", "tambien", "tan", "te", "tiene", "todo", "tu", "un", "una", "uno", "unos", "unas", "y", "ya", "yo", "an", "and", "are", "as", "at", "be", "but", "by", "do", "for", "from", "had", "has", "have", "her", "his", "how", "if", "in", "is", "it", "its", "just", "my", "not", "of", "on", "or", "our", "out", "own", "say", "she", "so", "some", "than", "that", "the", "their", "them", "then", "there", "these", "they", "this", "to", "too", "us", "use", "was", "we", "were", "what", "when", "which", "who", "will", "with", "would", "you", "your"})

def tokenize(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^a-záéíóúñü0-9\s]", " ", text)
    return [t for t in text.split() if t not in _STOP_WORDS and len(t) >= MIN_TOKEN_LEN]

def _token_string(text: str) -> str:
    return " ".join(sorted(set(tokenize(text))))

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks = []
    step = max(1, chunk_size - overlap)
    for i in range(0, len(words), step):
        end = min(i + chunk_size, len(words))
        chunk = " ".join(words[i:end])
        if len(chunk.split()) >= 8:
            chunks.append(chunk)
        if end == len(words):
            break
    return chunks

class TFIDFIndex:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._idf_cache: dict[str, float] = {}
        self._doc_count: int = 0
        self._doc_tokens: dict[int, Counter] = {}
        self._cache_lock = threading.RLock()
        self._init_db()
        self._load_index()

    def _connect(self) -> sqlite3.Connection:
        """Open a fresh connection. Never share one across gateway threads."""
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_db(self):
        c = self._connect()
        try:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS event_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    timestamp INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path       TEXT NOT NULL,
                    chunk_index     INTEGER NOT NULL,
                    content         TEXT NOT NULL,
                    file_hash       TEXT NOT NULL,
                    model_origin    TEXT,
                    hash            TEXT NOT NULL,
                    usage_count     INTEGER NOT NULL DEFAULT 0,
                    last_accessed   INTEGER NOT NULL,
                    created_at      INTEGER NOT NULL,
                    decay           REAL NOT NULL DEFAULT 1.0,
                    quality_score   REAL NOT NULL DEFAULT 0.5,
                    version         INTEGER NOT NULL DEFAULT 1,
                    is_active       INTEGER NOT NULL DEFAULT 1,
                    reward          REAL NOT NULL DEFAULT 0.0,
                    failure_count   INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS chunk_tokens (
                    chunk_id INTEGER PRIMARY KEY,
                    tokens   TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS doc_stats (
                    file_path   TEXT PRIMARY KEY,
                    file_hash   TEXT NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    indexed_at  TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks
                    USING fts5(tokens, content='chunk_tokens', content_rowid='chunk_id');
            """)
            c.commit()
        finally:
            c.close()

    def get_density_map(self) -> dict:
        c = self._connect()
        try:
            rows = c.execute(
                "SELECT hash, COUNT(*) FROM chunks WHERE is_active=1 GROUP BY hash"
            ).fetchall()
            return {r[0]: r[1] for r in rows}
        finally:
            c.close()

    def _load_index(self):
        c = self._connect()
        try:
            rows = c.execute("SELECT file_path, chunk_count FROM doc_stats").fetchall()
            doc_count = len(rows)
            doc_tokens: dict[int, Counter] = {}
            idf_cache: dict[str, float] = {}
            if doc_count:
                all_tokens = c.execute(
                    "SELECT chunk_id, tokens FROM chunk_tokens"
                ).fetchall()
                doc_freq: Counter = Counter()
                for cid, token_str in all_tokens:
                    tok_list = token_str.split()
                    doc_tokens[cid] = Counter(tok_list)
                    for t in tok_list:
                        doc_freq[t] += 1
                for term, df in doc_freq.items():
                    idf_cache[term] = math.log((1 + doc_count) / (1 + df)) + 1
        finally:
            c.close()

        with self._cache_lock:
            self._doc_count = doc_count
            self._doc_tokens = doc_tokens
            self._idf_cache = idf_cache

    def _insert_into_fts(self, c: sqlite3.Connection, cid: int, content: str):
        tokens = _token_string(content)
        c.execute(
            "INSERT INTO chunk_tokens (chunk_id, tokens) VALUES (?, ?)",
            (cid, tokens),
        )
        c.execute(
            "INSERT INTO fts_chunks(rowid, tokens) VALUES (?, ?)",
            (cid, tokens),
        )

    def index_docs(self, input_data: Path | list[dict]) -> dict:
        stats = {"indexed": 0, "skipped": 0, "new_files": 0, "updated_files": 0}
        now = int(time.time())
        if isinstance(input_data, list):
            c = self._connect()
            try:
                for doc in input_data:
                    content = doc["content"]
                    fhash = semantic_fingerprint(content)
                    file_hash = hashlib.sha256(content.encode()).hexdigest()
                    c.execute(
                        """INSERT INTO chunks
                           (file_path, chunk_index, content, file_hash, hash,
                            created_at, last_accessed, quality_score)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            doc["source"],
                            0,
                            content,
                            file_hash,
                            fhash,
                            now,
                            now,
                            doc.get("quality_score", 0.5),
                        ),
                    )
                    cid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
                    self._insert_into_fts(c, cid, content)
                    c.execute(
                        """INSERT OR REPLACE INTO doc_stats
                           (file_path, file_hash, chunk_count, indexed_at)
                           VALUES (?, ?, ?, datetime('now'))""",
                        (doc["source"], file_hash, 1),
                    )
                    stats["indexed"] += 1
                c.commit()
            except Exception:
                c.rollback()
                raise
            finally:
                c.close()
            self._load_index()
            return stats

        docs_dir = Path(input_data)
        if not docs_dir.exists():
            return {"indexed": 0, "skipped": 0, "error": "docs/ folder not found"}
        c = self._connect()
        try:
            files = list(docs_dir.glob("**/*.txt")) + list(docs_dir.glob("**/*.md"))
            for fpath in files:
                rel = fpath.name
                content = fpath.read_text(encoding="utf-8", errors="ignore")
                file_hash = hashlib.sha256(content.encode()).hexdigest()
                fhash = semantic_fingerprint(content)
                existing = c.execute(
                    "SELECT file_hash, chunk_count FROM doc_stats WHERE file_path = ?",
                    (rel,),
                ).fetchone()
                if existing and existing[0] == file_hash:
                    stats["skipped"] += 1
                    continue
                if existing:
                    old_ids = [
                        r[0]
                        for r in c.execute(
                            "SELECT id FROM chunks WHERE file_path = ?", (rel,)
                        ).fetchall()
                    ]
                    c.execute("DELETE FROM chunks WHERE file_path = ?", (rel,))
                    if old_ids:
                        id_placeholders = ",".join("?" * len(old_ids))
                        c.execute(
                            f"DELETE FROM chunk_tokens WHERE chunk_id IN ({id_placeholders})",
                            old_ids,
                        )
                        c.execute(
                            f"DELETE FROM fts_chunks WHERE rowid IN ({id_placeholders})",
                            old_ids,
                        )
                    c.execute("DELETE FROM doc_stats WHERE file_path = ?", (rel,))
                    stats["updated_files"] += 1
                else:
                    stats["new_files"] += 1
                chunks = chunk_text(content)
                for i, chunk in enumerate(chunks):
                    c.execute(
                        """INSERT INTO chunks
                           (file_path, chunk_index, content, file_hash, hash,
                            created_at, last_accessed, quality_score)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (rel, i, chunk, file_hash, fhash, now, now, 0.5),
                    )
                    cid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
                    self._insert_into_fts(c, cid, chunk)
                c.execute(
                    """INSERT OR REPLACE INTO doc_stats
                       (file_path, file_hash, chunk_count, indexed_at)
                       VALUES (?, ?, ?, datetime('now'))""",
                    (rel, file_hash, len(chunks)),
                )
                stats["indexed"] += len(chunks)
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
        self._load_index()
        return stats

    def search(self, query: str, top_k: int = TOP_K) -> list[dict]:
        with self._cache_lock:
            doc_count = self._doc_count
            doc_tokens = self._doc_tokens
            idf_cache = self._idf_cache
        if not doc_count:
            return []
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        c = self._connect()
        try:
            fts_query = " OR ".join(query_tokens)
            fts_ids = set(
                r[0]
                for r in c.execute(
                    "SELECT rowid FROM fts_chunks WHERE fts_chunks MATCH ? LIMIT 50",
                    (fts_query,),
                )
            )
            if not fts_ids:
                fts_ids = set(
                    r[0] for r in c.execute("SELECT chunk_id FROM chunk_tokens LIMIT 100")
                )
            scores: dict[int, float] = {}
            query_counter = Counter(query_tokens)
            query_norm = math.sqrt(sum(v**2 for v in query_counter.values())) or 1
            for cid in fts_ids:
                if cid not in doc_tokens:
                    continue
                doc_toks = doc_tokens[cid]
                doc_norm = math.sqrt(sum(v**2 for v in doc_toks.values())) or 1
                dot = 0.0
                for term, qtf in query_counter.items():
                    if term in doc_toks:
                        tfidf_q = (qtf / query_norm) * idf_cache.get(term, 1.0)
                        tfidf_d = (doc_toks[term] / doc_norm) * idf_cache.get(term, 1.0)
                        dot += tfidf_q * tfidf_d
                scores[cid] = dot
            top_ids = sorted(scores, key=scores.get, reverse=True)[:top_k]
            results = []
            for cid in top_ids:
                row = c.execute(
                    "SELECT file_path, chunk_index, content FROM chunks WHERE id = ?",
                    (cid,),
                ).fetchone()
                if row:
                    results.append(
                        {
                            "file": row[0],
                            "chunk": row[1],
                            "score": round(scores[cid], 4),
                            "text": row[2],
                        }
                    )
            return results
        finally:
            c.close()

    def list_indexed(self) -> list[dict]:
        c = self._connect()
        try:
            rows = c.execute(
                "SELECT file_path, chunk_count, indexed_at FROM doc_stats ORDER BY indexed_at DESC"
            ).fetchall()
            return [
                {"file": r[0], "chunks": r[1], "indexed_at": r[2]} for r in rows
            ]
        finally:
            c.close()

    def clear(self):
        c = self._connect()
        try:
            c.executescript(
                "DELETE FROM chunks; DELETE FROM chunk_tokens; "
                "DELETE FROM doc_stats; DELETE FROM fts_chunks;"
            )
            c.commit()
        finally:
            c.close()
        with self._cache_lock:
            # Rebind instead of mutating: a concurrent search may still hold a
            # reference to the previous snapshot and must not see it emptied.
            self._idf_cache = {}
            self._doc_tokens = {}
            self._doc_count = 0

    def close(self):
        """Compatibility no-op: connections are per operation now."""
        return None

_memory: TFIDFIndex | None = None
_memory_path: Path | None = None
_memory_lock = threading.Lock()


def get_memory(db_path: Path | None = None) -> TFIDFIndex:
    """Return shared TFIDFIndex. Pass db_path to force a specific DB (tests)."""
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


def retrieve(query: str, top_k: int = TOP_K, db_path: Path | None = None) -> str:
    results = get_memory(db_path).search(query, top_k)
    if not results:
        return ""
    return "\n\n---\n\n".join(
        [f"[Fuente: {r['file']}]\n{r['text']}" for r in results]
    )
