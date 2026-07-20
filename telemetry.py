import sqlite3
import time
import json
import hashlib
import uuid
import datetime
import logging
from typing import List, Dict, Optional, Any

DB_PATH = "coderag_telemetry.db"
logger = logging.getLogger(__name__)


def init_db():
    """
    Initializes SQLite telemetry DB and safely performs idempotent schema migrations
    to support versioned conversations, chat messages, search logs, and query cache.
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            # Table 1: Conversations (Server-side UUID threads)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    repo_filter TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Table 2: Chat Messages (Associated with conversations and user_id)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sources_json TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
                )
            ''')

            # Table 3: Search Logs
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

            # Table 4: Query Cache (Multi-dimensional SHA-256 payload key)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS query_cache (
                    query_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    repo_filter TEXT,
                    answer TEXT NOT NULL,
                    sources_json TEXT,
                    confidence INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Create Indexes for fast user-scoped queries
            conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_conv_user ON chat_messages(conversation_id, user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_search_logs_user ON search_logs(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_query_cache_user_repo ON query_cache(user_id, repo_filter)")

            # Migration: Ensure columns exist if upgraded from older schema
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(search_logs)")
            cols = [row[1] for row in cursor.fetchall()]
            if "user_id" not in cols:
                conn.execute("ALTER TABLE search_logs ADD COLUMN user_id TEXT")

    except Exception as e:
        logger.error("Telemetry database init failed [op=init_db, exc_type=%s]", type(e).__name__)


# Auto-initialize database on module import
init_db()


def get_cache_key(
    query: str,
    user_id: str,
    repo_filter: Optional[str] = None,
    model: str = "meta-llama/Llama-3.1-8B-Instruct",
    index_version: str = "v1",
    top_k: int = 5,
    retrieval_strategy: str = "hybrid_v1",
    prompt_version: str = "v1",
    history: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Computes a canonical SHA-256 cache key using deterministic JSON payload serialization.
    Enforces multi-dimensional isolation across query, repo, model, index, strategy, top_k, prompt, and conversation context.
    """
    context_str = json.dumps(history or [], sort_keys=True)
    context_hash = hashlib.sha256(context_str.encode("utf-8")).hexdigest()

    payload = {
        "version": "v1",
        "user_id": user_id,
        "repo_filter": repo_filter or "ALL",
        "normalized_query": query.strip().lower(),
        "model": model,
        "index_version": index_version,
        "top_k": top_k,
        "retrieval_strategy": retrieval_strategy,
        "prompt_version": prompt_version,
        "context_hash": context_hash,
    }
    canonical_str = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()


def get_cached_query(
    query: str,
    user_id: str,
    repo_filter: Optional[str] = None,
    model: str = "meta-llama/Llama-3.1-8B-Instruct",
    index_version: str = "v1",
    top_k: int = 5,
    retrieval_strategy: str = "hybrid_v1",
    prompt_version: str = "v1",
    history: Optional[List[Dict[str, Any]]] = None,
):
    """
    Reads query cache strictly matching canonical SHA-256 hash and user_id (valid for 24 hours).
    """
    if not user_id:
        return None

    try:
        key = get_cache_key(
            query=query,
            user_id=user_id,
            repo_filter=repo_filter,
            model=model,
            index_version=index_version,
            top_k=top_k,
            retrieval_strategy=retrieval_strategy,
            prompt_version=prompt_version,
            history=history,
        )
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
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


def set_cached_query(
    query: str,
    user_id: str,
    answer: str,
    sources: list,
    confidence: int,
    repo_filter: Optional[str] = None,
    model: str = "meta-llama/Llama-3.1-8B-Instruct",
    index_version: str = "v1",
    top_k: int = 5,
    retrieval_strategy: str = "hybrid_v1",
    prompt_version: str = "v1",
    history: Optional[List[Dict[str, Any]]] = None,
):
    """
    Writes query cache strictly scoped by canonical SHA-256 hash and authenticated user_id.
    """
    if not user_id:
        return

    try:
        key = get_cache_key(
            query=query,
            user_id=user_id,
            repo_filter=repo_filter,
            model=model,
            index_version=index_version,
            top_k=top_k,
            retrieval_strategy=retrieval_strategy,
            prompt_version=prompt_version,
            history=history,
        )
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('''
                INSERT OR REPLACE INTO query_cache (query_hash, user_id, repo_filter, answer, sources_json, confidence)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (key, user_id, repo_filter or "ALL", answer, json.dumps(sources), confidence))
    except Exception as e:
        logger.error("Telemetry cache write failed [op=cache_write, exc_type=%s]", type(e).__name__)


def invalidate_user_repo_cache(user_id: str, repo_filter: Optional[str] = None):
    """
    Purges cache entries owned by user_id (and optionally matching repo_filter)
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


# ----------------------------------------------------------------------
# CONVERSATION THREAD MANAGEMENT
# ----------------------------------------------------------------------

def create_conversation(user_id: str, repo_filter: Optional[str] = None) -> str:
    """Creates a new server-side conversation thread UUID for the authenticated user."""
    conv_id = str(uuid.uuid4())
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO conversations (id, user_id, repo_filter, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (conv_id, user_id, repo_filter or "ALL", now, now)
            )
    except Exception as e:
        logger.error("Failed to create conversation [op=create_conversation, exc_type=%s]", type(e).__name__)
    return conv_id


def verify_and_get_conversation(conv_id: str, user_id: str) -> Optional[Dict]:
    """
    Verifies that a conversation thread exists and belongs strictly to user_id.
    Returns conversation metadata if valid, or None if unknown or owned by another user.
    """
    if not conv_id or not user_id:
        return None
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, user_id, repo_filter, created_at, updated_at FROM conversations WHERE id = ? AND user_id = ?",
                (conv_id, user_id)
            )
            row = cursor.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error("Failed to verify conversation [op=verify_conversation, exc_type=%s]", type(e).__name__)
        return None


def add_message_to_conversation(conv_id: str, user_id: str, role: str, content: str, sources: Optional[List[dict]] = None):
    """Appends a message to a conversation thread and updates the updated_at timestamp."""
    try:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO chat_messages (conversation_id, user_id, role, content, sources_json) VALUES (?, ?, ?, ?, ?)",
                (conv_id, user_id, role, content, json.dumps(sources or []))
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ? AND user_id = ?",
                (now, conv_id, user_id)
            )
    except Exception as e:
        logger.error("Failed to add message to conversation [op=add_message, exc_type=%s]", type(e).__name__)


def get_conversation_messages(conv_id: str, user_id: str, limit: int = 12) -> List[Dict]:
    """Retrieves up to `limit` recent messages from a conversation owned by user_id."""
    if not verify_and_get_conversation(conv_id, user_id):
        return []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, conversation_id, role, content, sources_json, timestamp FROM chat_messages "
                "WHERE conversation_id = ? AND user_id = ? ORDER BY id ASC LIMIT ?",
                (conv_id, user_id, limit)
            )
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error("Failed to fetch conversation messages [op=get_messages, exc_type=%s]", type(e).__name__)
        return []


def log_search(query: str, repo_filter: Optional[str], confidence: int, latency_ms: float, user_id: Optional[str] = None):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('''
                INSERT INTO search_logs (query, repo_filter, confidence, latency_ms, user_id)
                VALUES (?, ?, ?, ?, ?)
            ''', (query, str(repo_filter) if repo_filter else "ALL", confidence, latency_ms, user_id))
    except Exception as e:
        logger.error("Telemetry search logging failed [op=log_search, exc_type=%s]", type(e).__name__)


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
    Fetches recent conversations and messages strictly scoped by authenticated user_id.
    """
    if not user_id:
        return []

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, conversation_id, role, content, timestamp FROM chat_messages "
                "WHERE user_id = ? ORDER BY id DESC LIMIT 50",
                (user_id,)
            )
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error("Telemetry history fetch failed [op=get_chat_history, exc_type=%s]", type(e).__name__)
        return []
