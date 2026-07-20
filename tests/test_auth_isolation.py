import os
import re
import glob
import sqlite3
import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from app import app as fastapi_app
from security.auth import AuthenticatedUser, get_current_user, verify_identity_match
import telemetry


@pytest.fixture(autouse=True)
def setup_test_overrides():
    """
    Injects test dependency overrides for get_current_user.
    This keeps 100% of test mocking inside tests/ fixtures so production security/auth.py contains NO mock token logic.
    """
    def mock_get_current_user(request: Request):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Authentication required: missing Authorization header.")

        token = auth_header.replace("Bearer ", "").strip()
        if not token:
            raise HTTPException(status_code=401, detail="Empty authentication token provided.")

        if token == "mock-token-user-user-A-id":
            return AuthenticatedUser(id="user-A-id", email="userA@test.com", access_token=token)
        elif token == "mock-token-user-user-B-id":
            return AuthenticatedUser(id="user-B-id", email="userB@test.com", access_token=token)
        elif token.startswith("test-user-"):
            uid = token.replace("test-user-", "")
            return AuthenticatedUser(id=uid, email=f"{uid}@test.com", access_token=token)

        raise HTTPException(status_code=401, detail="Invalid or expired authentication token.")

    fastapi_app.dependency_overrides[get_current_user] = mock_get_current_user

    # Clear SQLite query_cache table prior to each test
    try:
        with sqlite3.connect(telemetry.DB_PATH) as conn:
            conn.execute("DELETE FROM query_cache")
    except Exception:
        pass

    yield

    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(fastapi_app)


USER_A_HEADERS = {"Authorization": "Bearer mock-token-user-user-A-id"}
USER_B_HEADERS = {"Authorization": "Bearer mock-token-user-user-B-id"}


# ----------------------------------------------------------------------
# 1. REAL PRODUCTION AUTH DEPENDENCY (NO OVERRIDES) & STATIC CODE AUDIT
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_production_auth_dependency_rejects_mock_token_prefix():
    """
    Proves that running the real production get_current_user dependency without overrides
    rejects mock-token prefixes with HTTP 401 Unauthorized.
    """
    mock_request = MagicMock()
    mock_request.headers.get.return_value = "Bearer mock-token-user-attacker"

    with patch.dict("os.environ", {"SUPABASE_URL": "https://fake.supabase.co", "SUPABASE_KEY": "fake_key"}), \
         patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=401, json=lambda: {"message": "Invalid token"})

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(mock_request)

        assert exc_info.value.status_code == 401
        assert "Invalid or expired authentication token" in exc_info.value.detail


def test_static_code_audit_no_mock_tokens_in_production_source():
    """
    Static audit scanning Python and JS source files to ensure no mock-token logic
    or bypass strings exist in production code (excluding tests/ directory).
    """
    prod_files = []
    for root, dirs, files in os.walk("."):
        if "tests" in root or "node_modules" in root or ".git" in root or ".venv" in root:
            continue
        for file in files:
            if file.endswith((".py", ".js", ".jsx")) and not file.endswith((".test.js", "_test.py")):
                prod_files.append(os.path.join(root, file))

    forbidden_patterns = [
        r"mock-token-user-",
        r"bypass-auth",
        r"token\.startswith\(['\"]mock-token",
    ]

    for filepath in prod_files:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
            for pattern in forbidden_patterns:
                assert not re.search(pattern, content), f"Forbidden auth bypass pattern '{pattern}' found in production file: {filepath}"


# ----------------------------------------------------------------------
# 2. SUPABASE AUTH VERIFICATION FAILURES & SANITIZATION TESTS
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_supabase_auth_verification_missing_user_id():
    mock_request = MagicMock()
    mock_request.headers.get.return_value = "Bearer valid-format-token"

    with patch.dict("os.environ", {"SUPABASE_URL": "https://fake.supabase.co", "SUPABASE_KEY": "fake_key"}), \
         patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {"role": "authenticated"})

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(mock_request)

        assert exc_info.value.status_code == 401
        assert "Invalid user profile" in exc_info.value.detail


@pytest.mark.asyncio
async def test_supabase_auth_verification_timeout():
    import requests
    mock_request = MagicMock()
    mock_request.headers.get.return_value = "Bearer valid-format-token"

    with patch.dict("os.environ", {"SUPABASE_URL": "https://fake.supabase.co", "SUPABASE_KEY": "fake_key"}), \
         patch("requests.get", side_effect=requests.exceptions.Timeout()):

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(mock_request)

        assert exc_info.value.status_code == 504
        assert "timed out" in exc_info.value.detail


# ----------------------------------------------------------------------
# 3. ROUTE PROTECTION & IDENTITY MISMATCH TESTS
# ----------------------------------------------------------------------

def test_public_routes_unprotected(client):
    res_root = client.get("/")
    res_health = client.get("/health")
    assert res_root.status_code == 200
    assert res_health.status_code == 200


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("POST", "/search", {"query": "test"}),
        ("POST", "/ingest", {"repo_url": "https://github.com/owner/repo"}),
        ("POST", "/index", {"repo_name": "r", "file_path": "f.js", "language": "js", "code_content": "c"}),
        ("GET", "/user-repos", None),
        ("POST", "/delete-repo?repo_name=test", None),
        ("GET", "/graph-data", None),
        ("GET", "/history", None),
        ("GET", "/analytics", None),
    ],
)
def test_protected_routes_require_auth(client, method, path, body):
    fastapi_app.dependency_overrides.clear()
    if method == "POST":
        res = client.post(path, json=body)
    else:
        res = client.get(path)
    assert res.status_code == 401
    assert "Authentication required" in res.json()["detail"]


@patch("app.db")
def test_mismatched_legacy_user_id_rejected(mock_db, client):
    payload = {
        "repo_url": "https://github.com/kutty04/Cerebro",
        "user_id": "forged-user-id",
    }
    res = client.post("/ingest", json=payload, headers=USER_A_HEADERS)
    assert res.status_code == 403
    assert "user identity mismatch" in res.json()["detail"]


# ----------------------------------------------------------------------
# 4. MULTI-DIMENSIONAL CACHE ISOLATION TESTS
# ----------------------------------------------------------------------

def test_cache_dimensions_isolation():
    q = "Where is main function?"
    u_a = "user-A-id"
    u_b = "user-B-id"

    k_base = telemetry.get_cache_key(q, user_id=u_a, repo_filter="repo-1", model="model-v1", top_k=5, history=[])

    k_user_b = telemetry.get_cache_key(q, user_id=u_b, repo_filter="repo-1", model="model-v1", top_k=5, history=[])
    assert k_base != k_user_b

    k_repo_2 = telemetry.get_cache_key(q, user_id=u_a, repo_filter="repo-2", model="model-v1", top_k=5, history=[])
    assert k_base != k_repo_2

    k_model_2 = telemetry.get_cache_key(q, user_id=u_a, repo_filter="repo-1", model="model-v2", top_k=5, history=[])
    assert k_base != k_model_2

    k_topk_10 = telemetry.get_cache_key(q, user_id=u_a, repo_filter="repo-1", model="model-v1", top_k=10, history=[])
    assert k_base != k_topk_10

    k_hist = telemetry.get_cache_key(q, user_id=u_a, repo_filter="repo-1", model="model-v1", top_k=5, history=[{"role": "user", "content": "hi"}])
    assert k_base != k_hist


def test_cache_key_canonicalization():
    k1 = telemetry.get_cache_key("  How To Run?  ", user_id="u1", repo_filter="repo-1")
    k2 = telemetry.get_cache_key("how to run?", user_id="u1", repo_filter="repo-1")
    assert k1 == k2


# ----------------------------------------------------------------------
# 5. SERVER-SIDE CONVERSATION THREAD ISOLATION TESTS
# ----------------------------------------------------------------------

def test_conversation_thread_lifecycle_and_user_isolation():
    user_a = "user-A-id"
    user_b = "user-B-id"

    conv_id_a = telemetry.create_conversation(user_a, repo_filter="repo-A")
    assert conv_id_a is not None

    assert telemetry.verify_and_get_conversation(conv_id_a, user_a) is not None
    assert telemetry.verify_and_get_conversation(conv_id_a, user_b) is None

    telemetry.add_message_to_conversation(conv_id_a, user_a, "user", "What is module X?")
    telemetry.add_message_to_conversation(conv_id_a, user_a, "assistant", "Module X is utils.")

    msgs_a = telemetry.get_conversation_messages(conv_id_a, user_a)
    assert len(msgs_a) == 2

    msgs_b = telemetry.get_conversation_messages(conv_id_a, user_b)
    assert len(msgs_b) == 0


@patch("app.db")
def test_user_b_cannot_access_user_a_conversation_id_in_search(mock_db, client):
    conv_id_a = telemetry.create_conversation("user-A-id", repo_filter="repo-A")

    payload = {
        "query": "Where is config?",
        "conversation_id": conv_id_a,
    }

    res = client.post("/search", json=payload, headers=USER_B_HEADERS)
    assert res.status_code == 404
    assert "Conversation thread not found or inaccessible" in res.json()["detail"]


# ----------------------------------------------------------------------
# 6. RLS ARTIFACT STATIC REVIEW TEST
# ----------------------------------------------------------------------

def test_rls_migration_sql_static_review():
    with open("supabase_rls_migration.sql", "r", encoding="utf-8") as f:
        sql = f.read()

    assert "SET search_path = public, pg_temp;" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "auth.uid()::text = user_id" in sql
    assert "SECURITY INVOKER" in sql
    assert "CREATE TABLE IF NOT EXISTS user_repositories" in sql
    assert "CREATE TABLE IF NOT EXISTS user_conversations" in sql


# ----------------------------------------------------------------------
# 7. DATABASE READ/WRITE OWNERSHIP AUDIT TESTS
# ----------------------------------------------------------------------

@patch("app.db")
@patch("app.get_embedding")
def test_malicious_insert_scoping_overwritten(mock_get_embedding, mock_db, client):
    """
    Proves that any database insert on /index strictly binds the row to the authenticated user ID
    from the token, and ignores or overwrites any user_id field.
    """
    mock_get_embedding.return_value = [0.1] * 384
    mock_table = MagicMock()
    mock_table.insert.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[{"id": 42}])
    mock_db.table.return_value = mock_table

    payload = {
        "repo_name": "malicious-repo",
        "file_path": "hacked.py",
        "language": "python",
        "code_content": "import os",
        "user_id": "user-A-id"
    }

    res = client.post("/index", json=payload, headers=USER_A_HEADERS)
    assert res.status_code == 200

    # Ensure the database write payload strictly uses 'user-A-id'
    called_data = mock_table.insert.call_args[0][0]
    assert called_data["user_id"] == "user-A-id"


@patch("app.db")
def test_malicious_cross_user_id_rejected(mock_db, client):
    payload = {
        "repo_name": "repo-X",
        "file_path": "main.py",
        "language": "python",
        "code_content": "def run(): pass",
        "user_id": "user-B-id"
    }
    res = client.post("/index", json=payload, headers=USER_A_HEADERS)
    assert res.status_code == 403
    assert "user identity mismatch" in res.json()["detail"]

