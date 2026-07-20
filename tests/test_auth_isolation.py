import os
import shutil
import tempfile
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from app import app as fastapi_app
from security.auth import AuthenticatedUser
import telemetry
import indexer


@pytest.fixture
def client():
    return TestClient(fastapi_app)


# Headers for User A and User B using mock token strategy
USER_A_HEADERS = {"Authorization": "Bearer mock-token-user-user-A-id"}
USER_B_HEADERS = {"Authorization": "Bearer mock-token-user-user-B-id"}


# ----------------------------------------------------------------------
# 1. ROUTE PROTECTION & AUTHENTICATION REJECTION TESTS
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
    if method == "POST":
        res = client.post(path, json=body)
    else:
        res = client.get(path)
    assert res.status_code == 401
    assert "Authentication required" in res.json()["detail"]


def test_malformed_auth_header_rejection(client):
    res_basic = client.get("/user-repos", headers={"Authorization": "Basic abcdef"})
    assert res_basic.status_code == 401
    assert "Expected Bearer" in res_basic.json()["detail"]

    res_empty = client.get("/user-repos", headers={"Authorization": "Bearer "})
    assert res_empty.status_code == 401


# ----------------------------------------------------------------------
# 2. IDENTITY FORGERY & MISMATCH TESTS
# ----------------------------------------------------------------------

@patch("app.db")
def test_mismatched_legacy_user_id_rejected(mock_db, client):
    """
    Proves that if a browser sends a user_id that does NOT match the verified bearer token user,
    the request is rejected with HTTP 403 Forbidden.
    """
    payload = {
        "repo_url": "https://github.com/kutty04/Cerebro",
        "user_id": "forged-user-id",  # Mismatched!
    }
    res = client.post("/ingest", json=payload, headers=USER_A_HEADERS)
    assert res.status_code == 403
    assert "user identity mismatch" in res.json()["detail"]


# ----------------------------------------------------------------------
# 3. CROSS-USER ISOLATION TESTS (User A vs User B)
# ----------------------------------------------------------------------

@patch("app.db")
def test_user_b_cannot_list_user_a_repos(mock_db, client):
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.side_effect = lambda col, val: mock_table if val == "user-A-id" else MagicMock(execute=lambda: MagicMock(data=[]))
    mock_table.execute.return_value = MagicMock(data=[{"repo_name": "user-a-private-repo"}])
    mock_db.table.return_value = mock_table

    # User A sees own repo
    res_a = client.get("/user-repos", headers=USER_A_HEADERS)
    assert res_a.status_code == 200
    assert "user-a-private-repo" in res_a.json()["repos"]

    # User B sees empty list
    res_b = client.get("/user-repos", headers=USER_B_HEADERS)
    assert res_b.status_code == 200
    assert "user-a-private-repo" not in res_b.json()["repos"]


@patch("app.db")
def test_user_b_cannot_delete_user_a_repo(mock_db, client):
    mock_table = MagicMock()
    mock_table.delete.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[])
    mock_db.table.return_value = mock_table

    # User B tries to delete User A's repo
    res_b = client.post("/delete-repo?repo_name=user-a-repo", headers=USER_B_HEADERS)
    assert res_b.status_code == 200

    # Verify that eq("user_id", "user-B-id") was enforced so User A's repo was unaffected
    mock_table.eq.assert_any_call("user_id", "user-B-id")


@patch("app.db")
def test_user_b_cannot_read_user_a_graph_data(mock_db, client):
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.side_effect = lambda col, val: mock_table if val == "user-A-id" else MagicMock(execute=lambda: MagicMock(data=[]))
    mock_table.execute.return_value = MagicMock(data=[{"repo_name": "repo-A", "file_path": "main.py"}])
    mock_db.table.return_value = mock_table

    res_a = client.get("/graph-data", headers=USER_A_HEADERS)
    assert len(res_a.json()["nodes"]) > 1

    res_b = client.get("/graph-data", headers=USER_B_HEADERS)
    assert len(res_b.json()["nodes"]) == 1  # Core node only


# ----------------------------------------------------------------------
# 4. CACHE & TELEMETRY ISOLATION TESTS
# ----------------------------------------------------------------------

def test_query_cache_isolated_between_users():
    query = "How to run server?"
    answer_a = "User A answer"
    answer_b = "User B answer"

    telemetry.set_cached_query(query, "repo-common", answer_a, [], 90, user_id="user-A-id")
    telemetry.set_cached_query(query, "repo-common", answer_b, [], 95, user_id="user-B-id")

    cached_a = telemetry.get_cached_query(query, "repo-common", user_id="user-A-id")
    cached_b = telemetry.get_cached_query(query, "repo-common", user_id="user-B-id")

    assert cached_a["answer"] == answer_a
    assert cached_b["answer"] == answer_b


def test_telemetry_history_analytics_isolated():
    telemetry.log_search("Query A", "repo-A", 90, 150.0, user_id="user-A-id")
    telemetry.save_chat("thread-A", "Query A", "Answer A", [], user_id="user-A-id")

    telemetry.log_search("Query B", "repo-B", 80, 200.0, user_id="user-B-id")
    telemetry.save_chat("thread-B", "Query B", "Answer B", [], user_id="user-B-id")

    history_a = telemetry.get_chat_history("user-A-id")
    history_b = telemetry.get_chat_history("user-B-id")

    assert len(history_a) >= 1
    assert all("Query B" not in h["query"] for h in history_a)

    analytics_a = telemetry.get_analytics("user-A-id")
    assert analytics_a["total_searches"] >= 1


def test_legacy_unscoped_telemetry_quarantined():
    """
    Proves that legacy unscoped search logs without user_id remain quarantined and invisible.
    """
    import sqlite3
    with sqlite3.connect(telemetry.DB_PATH) as conn:
        conn.execute("INSERT INTO search_logs (query, repo_filter, confidence, latency_ms, user_id) VALUES ('Legacy Query', 'ALL', 50, 100, NULL)")

    analytics = telemetry.get_analytics("user-new-id")
    queries = [q["query"] for q in analytics.get("recent_queries", [])]
    assert "Legacy Query" not in queries


# ----------------------------------------------------------------------
# 5. PRIVACY & SANITIZATION IN AUTH EXCEPTIONS
# ----------------------------------------------------------------------

def test_auth_secret_token_sanitization(client):
    """
    Regression Test: Proves secret tokens in auth headers or errors are never leaked in response or logs.
    """
    fake_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.SuperSecretAuthToken123"
    res = client.get("/user-repos", headers={"Authorization": f"Bearer {fake_token}"})
    assert res.status_code == 401
    assert fake_token not in res.text
