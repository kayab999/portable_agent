
import sys
import re
import sqlite3
import hashlib
from pathlib import Path
from local_memory import get_memory

def normalize_trace(t: str) -> str:
    t = re.sub(r"\s+", " ", t)
    t = t.replace("FINAL ANSWER:", "FINAL:").replace("SOLUTION:", "FINAL:")
    return t.strip()

def reasoning_density(trace: str) -> float:
    keywords = ["if", "then", "therefore", "because", "strategy", "step"]
    score = sum(trace.lower().count(k) for k in keywords)
    return score / max(len(trace.split()), 1)

def is_high_value(trace: str) -> bool:
    return ("FINAL" in trace.upper()) and 150 < len(trace) < 5000 and reasoning_density(trace) > 0.02

def extract_reasoning_traces(text: str) -> list[str]:
    # Regex optimizado: límite de 2000 chars en bloque, evitando backtracking masivo
    patterns = [r"(THOUGHT:[^F]{0,2000}?FINAL[^F]{0,500}?)", r"(STRATEGY:[^F]{0,2000}?FINAL[^F]{0,500}?)"]
    traces = []
    for p in patterns:
        matches = re.findall(p, text, re.IGNORECASE)
        traces.extend([normalize_trace(m) for m in matches if is_high_value(m)])
    return traces

def detect_model(source: str):
    s = source.lower()
    for m in ["opus", "deepseek", "chatgpt", "glm", "bonsai"]:
        if m in s:
            return m
    return "unknown"

def process_file(path: Path):
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    
    docs = []
    for t in extract_reasoning_traces(text):
        # Deduplicación por hash del contenido
        content_hash = hashlib.sha256(t.encode()).hexdigest()
        docs.append({
            "content": t,
            "source": str(path),
            "model": detect_model(str(path)),
            "type": "reasoning_trace",
            "hash": content_hash,
            "quality": reasoning_density(t)
        })
    return docs

def index_batch(docs: list[dict]):
    if not docs:
        return
    mem = get_memory()
    conn = sqlite3.connect(mem.db_path)
    try:
        # Asegurar esquema actualizado
        conn.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS model_origin TEXT")
        conn.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS quality_score REAL")
        conn.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS hash TEXT")
        
        cursor = conn.cursor()
        for doc in docs:
            # Check deduplicación
            if cursor.execute("SELECT 1 FROM chunks WHERE hash = ?", (doc['hash'],)).fetchone():
                continue
                
            cursor.execute("""
                INSERT INTO chunks (file_path, chunk_index, content, file_hash, model_origin, quality_score, hash) 
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (doc["source"], 0, doc["content"], doc["hash"], doc["model"], doc["quality"], doc["hash"]))
        conn.commit()
    finally:
        conn.close()

def main():
    if len(sys.argv) < 2:
        print("Uso: python3 distill.py <ruta_dataset>")
        sys.exit(1)
    
    source = Path(sys.argv[1])
    batch_size = 128
    buffer = []
    total_indexed = 0

    print(f"--- Iniciando Ingestión Industrial: {source} ---")
    
    for file in source.rglob("*"):
        if file.suffix in [".json", ".txt", ".md", ".py"]:
            for trace in process_file(file):
                buffer.append(trace)
                if len(buffer) >= batch_size:
                    index_batch(buffer)
                    total_indexed += len(buffer)
                    print(f"[{total_indexed} trazas indexadas]")
                    buffer.clear()

    if buffer:
        index_batch(buffer)
        total_indexed += len(buffer)
    
    print(f"--- Destilación Finalizada. Total: {total_indexed} ---")

if __name__ == "__main__":
    main()
