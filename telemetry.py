import sqlite3
import time
import json
import hashlib
from typing import List, Dict

DB_PATH = "coderag_telemetry.db"

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS search_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT,
                repo_filter TEXT,
                confidence INTEGER,
                latency_ms REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT,
                query TEXT,
                answer TEXT,
                context_json TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS query_cache (
                query_hash TEXT PRIMARY KEY,
                answer TEXT,
                sources_json TEXT,
                confidence INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

def get_cache_key(query: str, repo_filter: str) -> str:
    raw = f"{query}_{repo_filter or 'ALL'}"
    return hashlib.md5(raw.encode()).hexdigest()

def get_cached_query(query: str, repo_filter: str):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # Cache expires after 24 hours
            cursor.execute('''
                SELECT answer, sources_json, confidence 
                FROM query_cache 
                WHERE query_hash = ? AND timestamp >= datetime('now', '-1 day')
            ''', (get_cache_key(query, repo_filter),))
            row = cursor.fetchone()
            if row:
                return {
                    "answer": row["answer"],
                    "sources": json.loads(row["sources_json"]),
                    "confidence": row["confidence"]
                }
    except Exception as e:
        print(f"Cache Read Error: {e}")
    return None

def set_cached_query(query: str, repo_filter: str, answer: str, sources: list, confidence: int):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('''
                INSERT OR REPLACE INTO query_cache (query_hash, answer, sources_json, confidence)
                VALUES (?, ?, ?, ?)
            ''', (get_cache_key(query, repo_filter), answer, json.dumps(sources), confidence))
    except Exception as e:
        print(f"Cache Write Error: {e}")

def log_search(query: str, repo_filter: str, confidence: int, latency_ms: float):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('''
                INSERT INTO search_logs (query, repo_filter, confidence, latency_ms)
                VALUES (?, ?, ?, ?)
            ''', (query, str(repo_filter) if repo_filter else "ALL", confidence, latency_ms))
    except Exception as e:
        print(f"Telemetry Error: {e}")

def save_chat(thread_id: str, query: str, answer: str, sources: List[dict]):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('''
                INSERT INTO chat_history (thread_id, query, answer, context_json)
                VALUES (?, ?, ?, ?)
            ''', (thread_id, query, answer, json.dumps(sources)))
    except Exception as e:
        print(f"Chat Save Error: {e}")

def get_analytics() -> Dict:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Basic stats
            cursor.execute("SELECT COUNT(*) as total_searches, AVG(latency_ms) as avg_latency, AVG(confidence) as avg_conf FROM search_logs")
            stats = dict(cursor.fetchone())
            
            # Recent queries
            cursor.execute("SELECT query, confidence, latency_ms, timestamp FROM search_logs ORDER BY id DESC LIMIT 10")
            recent = [dict(row) for row in cursor.fetchall()]
            
            return {
                "total_searches": stats["total_searches"] or 0,
                "avg_latency_ms": round(stats["avg_latency"] or 0, 2),
                "avg_confidence": round(stats["avg_conf"] or 0, 2),
                "recent_queries": recent
            }
    except Exception as e:
        print(f"Analytics Error: {e}")
        return {}

def get_chat_history() -> List[Dict]:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT id, query, answer, timestamp FROM chat_history ORDER BY id DESC LIMIT 50")
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        print(f"History Error: {e}")
        return []
