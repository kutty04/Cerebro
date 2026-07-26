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
                user_id TEXT,
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
                user_id TEXT,
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
        # Idempotent migration for existing SQLite databases
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(search_logs)")
        cols = [col[1] for col in cursor.fetchall()]
        if "user_id" not in cols:
            conn.execute("ALTER TABLE search_logs ADD COLUMN user_id TEXT")

        cursor.execute("PRAGMA table_info(chat_history)")
        cols = [col[1] for col in cursor.fetchall()]
        if "user_id" not in cols:
            conn.execute("ALTER TABLE chat_history ADD COLUMN user_id TEXT")

def get_cache_key(query: str, user_id: str, repo_scope: str = "ALL", index_version: str = "v1") -> str:
    """
    Generates a cryptographically isolated cache key for tenant + repo + index_version + normalized query.
    Prevents cross-user cache leakage or cross-version cache stale hits.
    """
    norm_query = (query or "").strip().lower()
    norm_user = (user_id or "anonymous").strip()
    norm_repo = (repo_scope or "ALL").strip()
    norm_ver = (index_version or "v1").strip()

    raw = f"u:{norm_user}|r:{norm_repo}|v:{norm_ver}|q:{norm_query}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def get_cached_query(query: str, user_id: str, repo_scope: str = "ALL", index_version: str = "v1"):
    if not user_id:
        return None
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            key = get_cache_key(query, user_id=user_id, repo_scope=repo_scope, index_version=index_version)
            # Cache expires after 24 hours
            cursor.execute('''
                SELECT answer, sources_json, confidence 
                FROM query_cache 
                WHERE query_hash = ? AND timestamp >= datetime('now', '-1 day')
            ''', (key,))
            row = cursor.fetchone()
            if row:
                return {
                    "answer": row["answer"],
                    "sources": json.loads(row["sources_json"]),
                    "confidence": row["confidence"]
                }
    except Exception as e:
        print(f"Cache Read Error: {type(e).__name__}")
    return None

def set_cached_query(query: str, user_id: str, repo_scope: str, answer: str, sources: list, confidence: int, index_version: str = "v1"):
    if not user_id:
        return
    try:
        with sqlite3.connect(DB_PATH) as conn:
            key = get_cache_key(query, user_id=user_id, repo_scope=repo_scope, index_version=index_version)
            conn.execute('''
                INSERT OR REPLACE INTO query_cache (query_hash, answer, sources_json, confidence)
                VALUES (?, ?, ?, ?)
            ''', (key, answer, json.dumps(sources), confidence))
    except Exception as e:
        print(f"Cache Write Error: {type(e).__name__}")

def log_search(query: str, repo_filter: str, confidence: int, latency_ms: float, user_id: str = None):
    if not user_id:
        return
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('''
                INSERT INTO search_logs (user_id, query, repo_filter, confidence, latency_ms)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, query, str(repo_filter) if repo_filter else "ALL", confidence, latency_ms))
    except Exception as e:
        print(f"Telemetry Error: {type(e).__name__}")

def save_chat(thread_id: str, query: str, answer: str, sources: List[dict], user_id: str = None):
    if not user_id:
        return
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('''
                INSERT INTO chat_history (user_id, thread_id, query, answer, context_json)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, thread_id, query, answer, json.dumps(sources)))
    except Exception as e:
        print(f"Chat Save Error: {type(e).__name__}")

def get_analytics(user_id: str) -> Dict:
    if not user_id:
        return {"total_searches": 0, "avg_latency_ms": 0, "avg_confidence": 0, "recent_queries": []}
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # User-scoped stats (quarantines legacy rows with user_id IS NULL)
            cursor.execute(
                "SELECT COUNT(*) as total_searches, AVG(latency_ms) as avg_latency, AVG(confidence) as avg_conf "
                "FROM search_logs WHERE user_id = ? AND user_id IS NOT NULL", (user_id,)
            )
            row = cursor.fetchone()
            stats = dict(row) if row else {}
            
            # User-scoped recent queries
            cursor.execute(
                "SELECT query, confidence, latency_ms, timestamp "
                "FROM search_logs WHERE user_id = ? AND user_id IS NOT NULL ORDER BY id DESC LIMIT 10", (user_id,)
            )
            recent = [dict(r) for r in cursor.fetchall()]
            
            return {
                "total_searches": stats.get("total_searches") or 0,
                "avg_latency_ms": round(stats.get("avg_latency") or 0, 2),
                "avg_confidence": round(stats.get("avg_conf") or 0, 2),
                "recent_queries": recent
            }
    except Exception as e:
        print(f"Analytics Error: {type(e).__name__}")
        return {"total_searches": 0, "avg_latency_ms": 0, "avg_confidence": 0, "recent_queries": []}

def get_chat_history(user_id: str) -> List[Dict]:
    if not user_id:
        return []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # User-scoped chat history (quarantines legacy rows with user_id IS NULL)
            cursor.execute(
                "SELECT id, query, answer, timestamp FROM chat_history "
                "WHERE user_id = ? AND user_id IS NOT NULL ORDER BY id DESC LIMIT 50", (user_id,)
            )
            return [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        print(f"History Error: {type(e).__name__}")
        return []
