import sqlite3
import time
import json
import hashlib
import logging
from typing import List, Dict, Optional

DB_PATH = "coderag_telemetry.db"
logger = logging.getLogger(__name__)


def init_db():
    """
    Initializes SQLite telemetry DB and safely performs idempotent schema migrations
    to ensure search_logs, chat_history, and query_cache support user-scoped isolation.
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS search_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT,
                    repo_filter TEXT,
                    confidence INTEGER,
                    latency_ms REAL,
                    user_id TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT,
                    user_id TEXT,
                    query TEXT,
                    answer TEXT,
                    context_json TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS query_cache (
                    query_hash TEXT PRIMARY KEY,
                    user_id TEXT,
                    repo_filter TEXT,
                    answer TEXT,
                    sources_json TEXT,
                    confidence INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Migration: Ensure user_id column exists in search_logs
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(search_logs)")
            cols = [row[1] for row in cursor.fetchall()]
            if "user_id" not in cols:
                conn.execute("ALTER TABLE search_logs ADD COLUMN user_id TEXT")
                logger.info("Migrated search_logs: added user_id column")

            # Migration: Ensure user_id column exists in chat_history
            cursor.execute("PRAGMA table_info(chat_history)")
            cols = [row[1] for row in cursor.fetchall()]
            if "user_id" not in cols:
                conn.execute("ALTER TABLE chat_history ADD COLUMN user_id TEXT")
                logger.info("Migrated chat_history: added user_id column")

            # Migration: Ensure user_id and repo_filter exist in query_cache
            cursor.execute("PRAGMA table_info(query_cache)")
            cols = [row[1] for row in cursor.fetchall()]
            if "user_id" not in cols:
                conn.execute("ALTER TABLE query_cache ADD COLUMN user_id TEXT")
            if "repo_filter" not in cols:
                conn.execute("ALTER TABLE query_cache ADD COLUMN repo_filter TEXT")

    except Exception as e:
        logger.error("Telemetry database init failed [op=init_db, exc_type=%s]", type(e).__name__)


# Auto-initialize database on module import
init_db()


def get_cache_key(query: str, repo_filter: Optional[str], user_id: str) -> str:
    """
    Computes a SHA-256 cache key strictly scoped by query, repo_filter, and authenticated user_id.
    """
    raw = f"{query}_{repo_filter or 'ALL'}_{user_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cached_query(query: str, repo_filter: Optional[str], user_id: str):
    """
    Reads query cache strictly filtered by SHA-256 hash and user_id.
    """
    if not user_id:
        return None

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            key = get_cache_key(query, repo_filter, user_id)
            cursor.execute('''
                SELECT answer, sources_json, confidence 
                FROM query_cache 
                WHERE query_hash = ? AND user_id = ? AND timestamp >= datetime('now', '-1 day')
            ''', (key, user_id))
            row = cursor.fetchone()
            if row:
                return {
                    "answer": row["answer"],
                    "sources": json.loads(row["sources_json"]),
                    "confidence": row["confidence"]
                }
    except Exception as e:
        logger.error("Telemetry cache read failed [op=cache_read, exc_type=%s]", type(e).__name__)
    return None


def set_cached_query(query: str, repo_filter: Optional[str], answer: str, sources: list, confidence: int, user_id: str):
    """
    Writes query cache strictly scoped by authenticated user_id.
    """
    if not user_id:
        return

    try:
        key = get_cache_key(query, repo_filter, user_id)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('''
                INSERT OR REPLACE INTO query_cache (query_hash, user_id, repo_filter, answer, sources_json, confidence)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (key, user_id, repo_filter or "ALL", answer, json.dumps(sources), confidence))
    except Exception as e:
        logger.error("Telemetry cache write failed [op=cache_write, exc_type=%s]", type(e).__name__)


def invalidate_user_repo_cache(user_id: str, repo_filter: Optional[str] = None):
    """
    Purges cache entries owned by user_id (and optionally filtered by repo_name)
    upon repository re-indexing or repository deletion.
    """
    if not user_id:
        return

    try:
        with sqlite3.connect(DB_PATH) as conn:
            if repo_filter:
                conn.execute("DELETE FROM query_cache WHERE user_id = ? AND repo_filter = ?", (user_id, repo_filter))
            else:
                conn.execute("DELETE FROM query_cache WHERE user_id = ?", (user_id,))
            logger.info("Invalidated cache entries for user [op=cache_invalidate]")
    except Exception as e:
        logger.error("Telemetry cache invalidation failed [op=cache_invalidate, exc_type=%s]", type(e).__name__)


def log_search(query: str, repo_filter: Optional[str], confidence: int, latency_ms: float, user_id: Optional[str] = None):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('''
                INSERT INTO search_logs (query, repo_filter, confidence, latency_ms, user_id)
                VALUES (?, ?, ?, ?, ?)
            ''', (query, str(repo_filter) if repo_filter else "ALL", confidence, latency_ms, user_id))
    except Exception as e:
        logger.error("Telemetry search logging failed [op=log_search, exc_type=%s]", type(e).__name__)


def save_chat(thread_id: str, query: str, answer: str, sources: List[dict], user_id: Optional[str] = None):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('''
                INSERT INTO chat_history (thread_id, user_id, query, answer, context_json)
                VALUES (?, ?, ?, ?, ?)
            ''', (thread_id, user_id, query, answer, json.dumps(sources)))
    except Exception as e:
        logger.error("Telemetry chat save failed [op=save_chat, exc_type=%s]", type(e).__name__)


def get_analytics(user_id: str) -> Dict:
    """
    Fetches search statistics strictly scoped by authenticated user_id.
    Quarantines/ignores legacy unscoped rows.
    """
    if not user_id:
        return {"total_searches": 0, "avg_latency_ms": 0.0, "avg_confidence": 0.0, "recent_queries": []}

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute(
                "SELECT COUNT(*) as total_searches, AVG(latency_ms) as avg_latency, AVG(confidence) as avg_conf "
                "FROM search_logs WHERE user_id = ?",
                (user_id,)
            )
            stats = dict(cursor.fetchone() or {})

            cursor.execute(
                "SELECT query, confidence, latency_ms, timestamp FROM search_logs "
                "WHERE user_id = ? ORDER BY id DESC LIMIT 10",
                (user_id,)
            )
            recent = [dict(row) for row in cursor.fetchall()]

            return {
                "total_searches": stats.get("total_searches") or 0,
                "avg_latency_ms": round(stats.get("avg_latency") or 0.0, 2),
                "avg_confidence": round(stats.get("avg_conf") or 0.0, 2),
                "recent_queries": recent,
            }
    except Exception as e:
        logger.error("Telemetry analytics fetch failed [op=get_analytics, exc_type=%s]", type(e).__name__)
        return {"total_searches": 0, "avg_latency_ms": 0.0, "avg_confidence": 0.0, "recent_queries": []}


def get_chat_history(user_id: str) -> List[Dict]:
    """
    Fetches chat history strictly scoped by authenticated user_id.
    Quarantines/ignores legacy unscoped rows.
    """
    if not user_id:
        return []

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, query, answer, timestamp FROM chat_history "
                "WHERE user_id = ? ORDER BY id DESC LIMIT 50",
                (user_id,)
            )
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error("Telemetry history fetch failed [op=get_chat_history, exc_type=%s]", type(e).__name__)
        return []
