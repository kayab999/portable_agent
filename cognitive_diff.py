import sqlite3
from pathlib import Path

# Fallback rápido si no hay numpy/hnswlib
def jaccard_similarity(s1: str, s2: str) -> float:
    t1 = set(s1.lower().split())
    t2 = set(s2.lower().split())
    if not t1 or not t2:
        return 0.0
    return len(t1 & t2) / len(t1 | t2)

def _safe_stack(data):
    if not data:
        return None
    # Estructura como lista de objetos para no depender de numpy en el stack
    return data

class CognitiveDiffEngineV3:
    def __init__(self, db1_path: Path, db2_path: Path):
        self.db1 = db1_path
        self.db2 = db2_path

    def _load_data(self, db_path):
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT id, content, quality_score FROM chunks").fetchall()
        conn.close()
        return [{"id": r[0], "text": r[1], "complexity": r[2] or 0.0} for r in rows]

    def compare(self):
        data1 = self._load_data(self.db1)
        data2 = self._load_data(self.db2)

        if not data1 or not data2:
            return {"error": "Missing snapshots"}

        summary = {"stable": 0, "modified": 0, "new": 0, "deleted": 0}
        cognitive_delta = {"knowledge_gain": 0.0, "knowledge_loss": 0.0}
        
        # Umbrales adaptativos
        stable_th = 0.85
        modified_th = 0.4
        
        matched_ids1 = set()
        
        for d2 in data2:
            best_sim = -1.0
            best_id1 = None
            
            for d1 in data1:
                sim = jaccard_similarity(d1['text'], d2['text'])
                if sim > best_sim:
                    best_sim = sim
                    best_id1 = d1['id']
            
            if best_sim >= stable_th:
                summary["stable"] += 1
                matched_ids1.add(best_id1)
            elif best_sim >= modified_th:
                summary["modified"] += 1
                matched_ids1.add(best_id1)
            else:
                summary["new"] += 1
                cognitive_delta["knowledge_gain"] += 1.0

        summary["deleted"] = len(set(d['id'] for d in data1) - matched_ids1)
        cognitive_delta["knowledge_loss"] = float(summary["deleted"])
        
        return {"summary": summary, "cognitive_delta": cognitive_delta}
