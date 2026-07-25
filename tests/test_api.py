import sqlite3
import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
import logging

from app import app as fastapi_app
from security.auth import AuthenticatedUser, get_current_user
from db_adapter import DatabaseAdapter

AUTH_HEADERS = {"Authorization": "Bearer test-user-user-123"}


@pytest.fixture(autouse=True)
def setup_test_overrides():
    def mock_get_current_user(request: Request):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Authentication required: missing Authorization header.")

        token = auth_header.replace("Bearer ", "").strip()
        if not token:
            raise HTTPException(status_code=401, detail="Empty authentication token provided.")

        return AuthenticatedUser(id="user-123", email="user-123@test.com", access_token=token)

    fastapi_app.dependency_overrides[get_current_user] = mock_get_current_user

    try:
        with sqlite3.connect("coderag_telemetry.db") as conn:
            conn.execute("DELETE FROM query_cache")
    except Exception:
        pass

    yield

    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(fastapi_app)


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "embedder_ready" in data
    assert "supabase_ready" in data
    assert "hf_ready" in data
    assert data["mode"] == "serverless"


def test_readiness_endpoint_degraded(client):
    with patch("app.db", None), patch.dict("os.environ", {}, clear=True):
        response = client.get("/readiness")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "degraded"
        assert data["database"] == "disconnected"
        assert data["embeddings"] == "unconfigured"


def test_readiness_endpoint_ready(client):
    with patch("app.db", MagicMock()), patch.dict("os.environ", {"HF_TOKEN": "valid_token"}):
        response = client.get("/readiness")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert data["database"] == "connected"
        assert data["embeddings"] == "ready"


def test_search_validation_empty_query(client):
    response = client.post("/search", json={"query": ""}, headers=AUTH_HEADERS)
    assert response.status_code == 422


def test_search_validation_query_too_long(client):
    long_query = "a" * 2001
    response = client.post("/search", json={"query": long_query}, headers=AUTH_HEADERS)
    assert response.status_code == 422


def test_search_validation_invalid_top_k(client):
    response = client.post("/search", json={"query": "test", "top_k": 0}, headers=AUTH_HEADERS)
    assert response.status_code == 422

    response = client.post("/search", json={"query": "test", "top_k": 100}, headers=AUTH_HEADERS)
    assert response.status_code == 422


@patch("app.db")
@patch("app.get_embedding")
@patch("app.http_client.post")
def test_search_user_scoped_rpc(mock_req_post, mock_get_embedding, mock_db, client):
    mock_get_embedding.return_value = [0.1] * 384

    mock_rpc = MagicMock()
    mock_rpc.eq.return_value = mock_rpc
    mock_rpc.execute.return_value = MagicMock(
        data=[
            {
                "id": 1,
                "repo_name": "test-repo",
                "file_path": "main.py",
                "language": "python",
                "code_content": "def main(): pass",
                "source_url": "https://github.com/test/repo/blob/main/main.py",
                "similarity": 0.85,
            }
        ]
    )
    mock_db.rpc.return_value = mock_rpc

    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.ilike.return_value = mock_table
    mock_table.limit.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[])
    mock_db.table.return_value = mock_table

    mock_llm_response = MagicMock()
    mock_llm_response.status_code = 200
    mock_llm_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": "This is a test answer.\n\nFOLLOW_UPS:\n- Q1\n- Q2\n- Q3"
                }
            }
        ]
    }
    mock_req_post.return_value = mock_llm_response

    with patch.dict("os.environ", {"HF_TOKEN": "mock_token"}):
        response = client.post(
            "/search",
            json={"query": "How do I run main in search_user_scoped_rpc?", "top_k": 5},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200
    assert "conversation_id" in response.json()
    mock_db.rpc.assert_called_once_with(
        "search_code_snippets",
        {
            "query_embedding": [0.1] * 384,
            "match_count": 5,
            "p_user_id": "user-123",
        },
    )


@patch("app.db")
@patch("app.get_embedding")
def test_search_user_scoped_rpc_failure_fails_closed(mock_get_embedding, mock_db, client):
    mock_get_embedding.return_value = [0.1] * 384
    mock_db.rpc.side_effect = Exception("PGRST202: Could not find function search_code_snippets with p_user_id")

    with patch.dict("os.environ", {"HF_TOKEN": "mock_token"}):
        response = client.post(
            "/search",
            json={"query": "How do I run main in test_fails_closed?", "top_k": 5},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 500
    data = response.json()
    assert "user isolation query could not be executed safely" in data["detail"]
    assert "PGRST202" not in data["detail"]


@patch("app.db")
@patch("app.get_embedding")
def test_search_secret_bearing_exception_sanitization(mock_get_embedding, mock_db, caplog, client):
    mock_get_embedding.return_value = [0.1] * 384

    fake_secret_password = "SuperSecretPassword123!"
    fake_db_url = "postgresql://admin:SuperSecretPassword123!@db.supabase.co:5432/postgres"
    fake_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.SecretTokenContent"

    fake_secret_exception = Exception(
        f"Database driver crash connecting to {fake_db_url} with token={fake_token}"
    )

    mock_db.rpc.side_effect = fake_secret_exception

    with caplog.at_level(logging.ERROR), patch.dict("os.environ", {"HF_TOKEN": "mock_token"}):
        response = client.post(
            "/search",
            json={"query": "How do I connect secret test?"},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 500
    response_text = response.text
    log_text = caplog.text

    assert fake_secret_password not in response_text
    assert fake_db_url not in response_text
    assert fake_token not in response_text

    assert fake_secret_password not in log_text
    assert fake_db_url not in log_text
    assert fake_token not in log_text

    assert "User-scoped vector search RPC failed [op=search_user_rpc, exc_type=Exception]" in log_text


@patch("app.db")
@patch("app.get_embedding")
def test_search_upstream_embedding_failure(mock_get_embedding, mock_db, client):
    mock_get_embedding.return_value = None

    response = client.post("/search", json={"query": "test search"}, headers=AUTH_HEADERS)
    assert response.status_code == 502
    assert "Embedding service unavailable" in response.json()["detail"]


@patch("app.db")
@patch("app.get_embedding")
def test_search_db_not_initialized(mock_get_embedding, mock_db, client):
    with patch("app.db", None):
        response = client.post("/search", json={"query": "test search"}, headers=AUTH_HEADERS)
        assert response.status_code == 500
        assert "Database client is not initialized" in response.json()["detail"]


@patch("app.db")
@patch("app.get_embedding")
def test_index_endpoint_success(mock_get_embedding, mock_db, client):
    mock_get_embedding.return_value = [0.2] * 384

    mock_table = MagicMock()
    mock_table.insert.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[{"id": 42}])
    mock_db.table.return_value = mock_table

    payload = {
        "repo_name": "my-repo",
        "file_path": "src/utils.js",
        "language": "javascript",
        "code_content": "console.log('hello');",
        "source_url": "https://github.com/user/repo",
    }
    response = client.post("/index", json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["snippet_id"] == 42


def test_index_endpoint_validation(client):
    payload = {
        "repo_name": "",
        "file_path": "src/utils.js",
        "language": "javascript",
        "code_content": "console.log('hello');",
    }
    response = client.post("/index", json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 422


@patch("app.db")
def test_ingest_endpoint_non_https_validation(mock_db, client):
    payload = {
        "repo_url": "http://github.com/user/repo",
    }
    response = client.post("/ingest", json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 400
    assert "Only HTTPS" in response.json()["detail"]


@patch("app.db")
@patch.object(DatabaseAdapter, "list_owned_repos")
def test_user_repos_endpoint(mock_list_repos, mock_db, client):
    mock_list_repos.return_value = [
        {"repository_name": "repo-a", "id": "repo-a-id"},
        {"repository_name": "repo-b", "id": "repo-b-id"},
        {"repository_name": "repo-a", "id": "repo-a-id"},
    ]

    response = client.get("/user-repos", headers=AUTH_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["repos"] == ["repo-a", "repo-b"]


@patch("app.db")
def test_graph_data_endpoint(mock_db, client):
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.execute.return_value = MagicMock(
        data=[
            {"repo_name": "repo-a", "file_path": "src/index.js"},
            {"repo_name": "repo-a", "file_path": "src/utils.js"},
        ]
    )
    mock_db.table.return_value = mock_table

    response = client.get("/graph-data", headers=AUTH_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert "nodes" in data
    assert "links" in data
    assert len(data["nodes"]) == 4


# ----------------------------------------------------------------------
# PHASE 8 - Deployment Readiness & Telemetry Hardening Tests
# ----------------------------------------------------------------------

from app import validate_startup_config
from telemetry import prune_old_telemetry, delete_user_repo_telemetry, DB_PATH
import os


def test_validate_startup_config_valid():
    with patch.dict(os.environ, {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "valid-service-role-key-123",
        "HF_TOKEN": "valid-hf-token-abc"
    }):
        assert validate_startup_config() is True


def test_validate_startup_config_missing():
    with patch.dict(os.environ, {}, clear=True):
        assert validate_startup_config() is False


def test_validate_startup_config_placeholders():
    with patch.dict(os.environ, {
        "SUPABASE_URL": "https://your-project.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "your_supabase_anon_key_here",
        "HF_TOKEN": "your_huggingface_api_token_here"
    }):
        assert validate_startup_config() is False


def test_readiness_fails_with_placeholders_in_production(client):
    with patch.dict(os.environ, {
        "PRODUCTION": "true",
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "your_supabase_anon_key_here",
        "HF_TOKEN": "your_huggingface_api_token_here"
    }), patch("app.db", MagicMock()):
        response = client.get("/readiness")
        assert response.status_code == 503
        assert response.json()["status"] == "degraded"


def test_security_headers_middleware(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "Permissions-Policy" in response.headers


def test_cache_control_on_sensitive_routes(client):
    # Sensitive endpoints should return no-store
    response = client.get("/history", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    assert "no-cache" in response.headers["Cache-Control"]
    assert response.headers["Pragma"] == "no-cache"

    # Non-sensitive root path should not have no-store cache control enforced by middleware
    response = client.get("/")
    assert response.status_code == 200
    assert "Cache-Control" not in response.headers


def test_telemetry_pruning():
    import sqlite3
    # Insert old entries into query_cache and search_logs, verify they get pruned
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM query_cache")
        conn.execute("DELETE FROM search_logs")
        conn.execute("DELETE FROM chat_messages")
        conn.execute("DELETE FROM conversations")
        
        # 1. Old cache entry
        conn.execute(
            "INSERT INTO query_cache (query_hash, user_id, repo_filter, answer, sources_json, confidence, timestamp) "
            "VALUES ('old-hash', 'user-123', 'repo-a', 'ans', '[]', 80, datetime('now', '-2 days'))"
        )
        # Fresh cache entry
        conn.execute(
            "INSERT INTO query_cache (query_hash, user_id, repo_filter, answer, sources_json, confidence, timestamp) "
            "VALUES ('fresh-hash', 'user-123', 'repo-a', 'ans', '[]', 80, datetime('now'))"
        )
        # 2. Old search log
        conn.execute(
            "INSERT INTO search_logs (query, repo_filter, confidence, latency_ms, user_id, timestamp) "
            "VALUES ('old-query', 'repo-a', 80, 100, 'user-123', datetime('now', '-31 days'))"
        )
        # Fresh search log
        conn.execute(
            "INSERT INTO search_logs (query, repo_filter, confidence, latency_ms, user_id, timestamp) "
            "VALUES ('fresh-query', 'repo-a', 80, 100, 'user-123', datetime('now'))"
        )
        conn.commit()

    prune_old_telemetry()

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Cache pruning check
        cursor.execute("SELECT query_hash FROM query_cache")
        hashes = [r["query_hash"] for r in cursor.fetchall()]
        assert "fresh-hash" in hashes
        assert "old-hash" not in hashes

        # Search logs pruning check
        cursor.execute("SELECT query FROM search_logs")
        queries = [r["query"] for r in cursor.fetchall()]
        assert "fresh-query" in queries
        assert "old-query" not in queries


def test_telemetry_user_repo_scoped_purge():
    import sqlite3
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM query_cache")
        conn.execute("DELETE FROM search_logs")
        conn.execute("DELETE FROM chat_messages")
        conn.execute("DELETE FROM conversations")
        
        # Insert entries for user-123 repo-a
        conn.execute("INSERT INTO query_cache (query_hash, user_id, repo_filter, answer, sources_json, confidence) VALUES ('h1', 'user-123', 'repo-a', 'ans', '[]', 80)")
        # Insert entries for user-123 repo-b
        conn.execute("INSERT INTO query_cache (query_hash, user_id, repo_filter, answer, sources_json, confidence) VALUES ('h2', 'user-123', 'repo-b', 'ans', '[]', 80)")
        # Insert search log for user-123 repo-a
        conn.execute("INSERT INTO search_logs (query, repo_filter, confidence, latency_ms, user_id) VALUES ('q1', 'repo-a', 80, 100, 'user-123')")
        
        # Conversation for user-123 repo-a
        conn.execute("INSERT INTO conversations (id, user_id, repo_filter) VALUES ('conv-a', 'user-123', 'repo-a')")
        conn.execute("INSERT INTO chat_messages (conversation_id, user_id, role, content) VALUES ('conv-a', 'user-123', 'user', 'hello')")
        
        # Conversation for user-123 repo-b
        conn.execute("INSERT INTO conversations (id, user_id, repo_filter) VALUES ('conv-b', 'user-123', 'repo-b')")
        conn.execute("INSERT INTO chat_messages (conversation_id, user_id, role, content) VALUES ('conv-b', 'user-123', 'user', 'hello')")
        conn.commit()

    # Purge repo-a telemetry for user-123
    delete_user_repo_telemetry("user-123", "repo-a")

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Cache check
        cursor.execute("SELECT query_hash FROM query_cache")
        hashes = [r["query_hash"] for r in cursor.fetchall()]
        assert "h2" in hashes
        assert "h1" not in hashes

        # Search log check
        cursor.execute("SELECT query FROM search_logs")
        queries = [r["query"] for r in cursor.fetchall()]
        assert len(queries) == 0

        # Conversation check
        cursor.execute("SELECT id FROM conversations")
        convs = [r["id"] for r in cursor.fetchall()]
        assert "conv-b" in convs
        assert "conv-a" not in convs


def test_telemetry_does_not_store_sensitive_keys():
    import sqlite3
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM query_cache")
        rows = cursor.fetchall()
        for r in rows:
            for val in r:
                if val and isinstance(val, str):
                    assert "bearer" not in val.lower()
                    assert "service_role" not in val.lower()
                    assert "supabase_key" not in val.lower()


# ----------------------------------------------------------------------
# STATIC AUDIT - Production files must not contain test-infrastructure code
# ----------------------------------------------------------------------

def test_no_pytest_contamination_in_production_sources():
    """
    Enforces that production application modules contain no pytest-specific
    runtime behaviour.  Forbidden patterns:

    - 'pytest' in sys.modules
    - os.getenv('PYTEST_CURRENT_TEST')
    - PYTEST_CURRENT_TEST (string literal)
    - _test_post  (test-only HTTP wrapper symbol)
    - mock_token  (test-credential literal)
    - test-mode   (generic test-mode bypass string)

    The tests/ directory is explicitly excluded.
    """
    import pathlib, re

    # Production modules to audit (relative to repo root)
    production_files = [
        "app.py",
        "indexer.py",
        "telemetry.py",
        "db_adapter.py",
        "ingestion_validator.py",
        "security/auth.py",
    ]

    forbidden_patterns = [
        (r'"pytest"\s+in\s+sys\.modules', '"pytest" in sys.modules'),
        (r"'pytest'\s+in\s+sys\.modules", "'pytest' in sys.modules"),
        (r'PYTEST_CURRENT_TEST', 'PYTEST_CURRENT_TEST'),
        (r'\b_test_post\b', '_test_post (test-only wrapper)'),
        (r'\bmock_token\b', 'mock_token (test credential)'),
        (r'test[_-]mode', 'test-mode bypass'),
    ]

    root = pathlib.Path(__file__).parent.parent
    violations = []

    for rel_path in production_files:
        fpath = root / rel_path
        if not fpath.exists():
            continue
        source = fpath.read_text(encoding="utf-8")
        for pattern, label in forbidden_patterns:
            if re.search(pattern, source):
                violations.append(f"{rel_path}: contains [{label}]")

    assert not violations, (
        "Production source files contain test-infrastructure code:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


# ----------------------------------------------------------------------
# CORS CONFIGURATION TESTS
# Tests for parse_cors_origins() and CORS middleware behaviour.
# ----------------------------------------------------------------------

from app import parse_cors_origins


def test_cors_allows_production_vercel_origin():
    """The real Vercel production origin must pass validation."""
    origins = parse_cors_origins("https://cerebro-delta-silk.vercel.app")
    assert "https://cerebro-delta-silk.vercel.app" in origins


def test_cors_allows_localhost_origins():
    """Local development origins must pass validation."""
    origins = parse_cors_origins("http://localhost:5173,http://localhost:3000")
    assert "http://localhost:5173" in origins
    assert "http://localhost:3000" in origins


def test_cors_rejects_unknown_origin():
    """An unrelated HTTPS origin must be parsed but not auto-permitted.
    The test validates parse_cors_origins does not silently add unknown
    origins — only origins explicitly listed are accepted."""
    origins = parse_cors_origins("https://cerebro-delta-silk.vercel.app")
    assert "https://attacker.example.com" not in origins


def test_cors_rejects_wildcard():
    """Wildcard '*' must be rejected by parse_cors_origins."""
    origins = parse_cors_origins("*")
    assert "*" not in origins
    assert len(origins) == 0


def test_cors_rejects_wildcard_mixed_with_valid():
    """Wildcard mixed into a list must be stripped; valid origins survive."""
    origins = parse_cors_origins("https://cerebro-delta-silk.vercel.app,*,http://localhost:5173")
    assert "*" not in origins
    assert "https://cerebro-delta-silk.vercel.app" in origins
    assert "http://localhost:5173" in origins


def test_cors_rejects_http_non_localhost():
    """HTTP (non-HTTPS) for non-localhost hosts must be rejected."""
    origins = parse_cors_origins("http://cerebro-delta-silk.vercel.app")
    assert len(origins) == 0


def test_cors_rejects_origin_with_path_segment():
    """Origins containing path segments must be rejected."""
    origins = parse_cors_origins("https://cerebro-delta-silk.vercel.app/app")
    assert len(origins) == 0


def test_cors_rejects_origin_with_embedded_credentials():
    """Origins with user:pass credentials must be rejected."""
    origins = parse_cors_origins("https://user:pass@cerebro-delta-silk.vercel.app")
    assert len(origins) == 0


def test_cors_rejects_wildcard_subdomain():
    """Wildcard sub-domain patterns must be rejected."""
    origins = parse_cors_origins("https://*.vercel.app")
    assert len(origins) == 0


def test_cors_rejects_malformed_origin():
    """Strings that are not valid URLs must be skipped."""
    origins = parse_cors_origins("not-a-url,ftp://bad-scheme.com")
    assert len(origins) == 0


def test_cors_deduplicates_identical_origins():
    """Duplicate origins in the list must appear only once."""
    origins = parse_cors_origins(
        "https://cerebro-delta-silk.vercel.app,https://cerebro-delta-silk.vercel.app"
    )
    assert origins.count("https://cerebro-delta-silk.vercel.app") == 1


def test_cors_preflight_headers_present(client):
    """CORS middleware must expose Authorization and Content-Type for preflight."""
    response = client.options(
        "/search",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization, Content-Type",
        },
    )
    # FastAPI CORSMiddleware returns 200 for allowed preflight
    assert response.status_code == 200
    acao = response.headers.get("access-control-allow-origin", "")
    assert acao != "*", "Wildcard CORS must never be returned with credentials"
    acah = response.headers.get("access-control-allow-headers", "")
    assert "authorization" in acah.lower() or "content-type" in acah.lower()


def test_cors_no_wildcard_with_credentials():
    """validate_startup_config must reject CORS_ALLOWED_ORIGINS containing '*'."""
    from app import validate_startup_config
    with patch.dict(os.environ, {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "valid-key-abc123",
        "HF_TOKEN": "valid-hf-token-abc",
        "CORS_ALLOWED_ORIGINS": "*",
    }):
        assert validate_startup_config() is False


def test_cors_validates_startup_wildcard_rejection():
    """validate_startup_config must reject even partially wildcarded lists."""
    from app import validate_startup_config
    with patch.dict(os.environ, {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "valid-key-abc123",
        "HF_TOKEN": "valid-hf-token-abc",
        "CORS_ALLOWED_ORIGINS": "https://cerebro-delta-silk.vercel.app,*",
    }):
        assert validate_startup_config() is False


# ----------------------------------------------------------------------
# PRODUCTION CORS STARTUP ENFORCEMENT TESTS
# ----------------------------------------------------------------------

def _prod_env(**extra):
    """Helper: return a dict of valid prod creds + any extra overrides."""
    base = {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "valid-key-abc123",
        "HF_TOKEN": "valid-hf-token-abc",
        "PRODUCTION": "true",
    }
    base.update(extra)
    return base


def test_cors_prod_valid_vercel_origin_passes():
    """Production mode with a valid Vercel HTTPS origin must pass."""
    from app import validate_startup_config
    with patch.dict(os.environ, _prod_env(
        CORS_ALLOWED_ORIGINS="https://cerebro-delta-silk.vercel.app"
    ), clear=True):
        assert validate_startup_config() is True


def test_cors_prod_explicit_preview_origin_passes():
    """Production mode with an explicit individually-listed preview URL must pass."""
    from app import validate_startup_config
    with patch.dict(os.environ, _prod_env(
        CORS_ALLOWED_ORIGINS=(
            "https://cerebro-delta-silk.vercel.app,"
            "https://cerebro-git-feature-xyz-team.vercel.app"
        )
    ), clear=True):
        assert validate_startup_config() is True


def test_cors_prod_missing_origins_fails():
    """Production mode with no CORS_ALLOWED_ORIGINS must fail startup."""
    from app import validate_startup_config
    env = _prod_env()
    env.pop("CORS_ALLOWED_ORIGINS", None)
    with patch.dict(os.environ, env, clear=True):
        assert validate_startup_config() is False


def test_cors_prod_empty_origins_fails():
    """Production mode with empty CORS_ALLOWED_ORIGINS must fail startup."""
    from app import validate_startup_config
    with patch.dict(os.environ, _prod_env(CORS_ALLOWED_ORIGINS=""), clear=True):
        assert validate_startup_config() is False


def test_cors_prod_wildcard_fails():
    """Production mode with wildcard CORS_ALLOWED_ORIGINS must fail startup."""
    from app import validate_startup_config
    with patch.dict(os.environ, _prod_env(CORS_ALLOWED_ORIGINS="*"), clear=True):
        assert validate_startup_config() is False


def test_cors_prod_malformed_origin_fails():
    """Production mode with only malformed/invalid origins must fail startup."""
    from app import validate_startup_config
    with patch.dict(os.environ, _prod_env(
        CORS_ALLOWED_ORIGINS="not-a-url"
    ), clear=True):
        assert validate_startup_config() is False


def test_cors_prod_http_non_localhost_fails():
    """Production mode with HTTP (non-HTTPS) external origin must fail startup."""
    from app import validate_startup_config
    with patch.dict(os.environ, _prod_env(
        CORS_ALLOWED_ORIGINS="http://cerebro-delta-silk.vercel.app"
    ), clear=True):
        assert validate_startup_config() is False


def test_cors_dev_defaults_work_without_env_var():
    """Development mode with no CORS_ALLOWED_ORIGINS must not fail startup."""
    from app import validate_startup_config
    with patch.dict(os.environ, {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "valid-key-abc123",
        "HF_TOKEN": "valid-hf-token-abc",
        "PRODUCTION": "false",
    }, clear=True):
        # validate_startup_config() succeeds for dev (CORS not required)
        assert validate_startup_config() is True


# ----------------------------------------------------------------------
# URL DEFAULT CONSISTENCY TEST
# ----------------------------------------------------------------------

def test_default_backend_port_is_7860():
    """
    The documented local backend port must be 7860 everywhere.
    Checks: app.py __main__ default, apiClient.js fallback, vite.config.js
    proxy/SW matcher, .env.example, README.
    """
    import pathlib, re

    checks = {
        "app.py": (r'os\.environ\.get\(["\']PORT["\'],\s*7860\)', True),
        "coderag-frontend/src/apiClient.js": (r'localhost:7860', True),
        "coderag-frontend/vite.config.js": (r'localhost:7860', True),
        ".env.example": (r'PORT=7860', True),
        "README.md": (r'localhost:7860', True),
        # These must NOT contain the wrong port as a default
        "coderag-frontend/src/apiClient.js": (r"localhost:8000", False),
        "coderag-frontend/vite.config.js": (r"localhost:8000", False),
    }

    root = pathlib.Path(__file__).parent.parent
    failures = []

    for rel_path, (pattern, should_match) in checks.items():
        fpath = root / rel_path
        if not fpath.exists():
            failures.append(f"{rel_path}: file not found")
            continue
        src = fpath.read_text(encoding="utf-8", errors="replace")
        found = bool(re.search(pattern, src))
        if found != should_match:
            if should_match:
                failures.append(f"{rel_path}: missing expected pattern [{pattern}]")
            else:
                failures.append(f"{rel_path}: found forbidden pattern [{pattern}]")

    assert not failures, "URL default inconsistencies:\n" + "\n".join(f"  - {f}" for f in failures)
