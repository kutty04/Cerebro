import pytest
import sqlite3
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app import app
from telemetry import init_db, log_search, save_chat, get_analytics, get_chat_history, DB_PATH

client = TestClient(app)

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"


def mock_supabase_auth_get_user(token: str):
    if token in [USER_A, USER_B]:
        user_mock = MagicMock()
        user_mock.id = token
        res_mock = MagicMock()
        res_mock.user = user_mock
        return res_mock
    raise Exception("Invalid token")


class TestAllEndpointsSecurityCoverage:

    @patch("app.db")
    def test_post_ingest_without_authorization_returns_401(self, mock_db):
        response = client.post("/ingest", json={"repo_url": "https://github.com/test/repo.git", "user_id": USER_A})
        assert response.status_code == 401
        assert "missing Authorization header" in response.json()["detail"]

    @patch("app.db")
    def test_post_ingest_with_forged_body_user_id_returns_403(self, mock_db):
        mock_db.auth.get_user.side_effect = mock_supabase_auth_get_user
        response = client.post(
            "/ingest",
            headers={"Authorization": f"Bearer {USER_A}"},
            json={"repo_url": "https://github.com/test/repo.git", "user_id": USER_B}  # Forged!
        )
        assert response.status_code == 403
        assert "User context mismatch" in response.json()["detail"]

    @patch("app.db")
    def test_post_index_without_authorization_returns_401(self, mock_db):
        response = client.post("/index", params={"repo_name": "repo", "file_path": "a.py", "language": "python", "code_content": "pass"})
        assert response.status_code == 401

    @patch("app.db")
    def test_post_index_with_forged_user_id_returns_403(self, mock_db):
        mock_db.auth.get_user.side_effect = mock_supabase_auth_get_user
        response = client.post(
            "/index",
            headers={"Authorization": f"Bearer {USER_A}"},
            params={"repo_name": "repo", "file_path": "a.py", "language": "python", "code_content": "pass", "user_id": USER_B}
        )
        assert response.status_code == 403

    @patch("app.db")
    def test_get_history_without_authorization_returns_401(self, mock_db):
        response = client.get("/history")
        assert response.status_code == 401

    @patch("app.db")
    def test_get_analytics_without_authorization_returns_401(self, mock_db):
        response = client.get("/analytics")
        assert response.status_code == 401

    @patch("app.db")
    def test_user_b_cannot_read_user_a_history(self, mock_db):
        mock_db.auth.get_user.side_effect = mock_supabase_auth_get_user
        init_db()

        # Seed chat history for User A
        save_chat("t1", "User A query", "User A answer", [], user_id=USER_A)

        # User B fetches history
        response = client.get(
            "/history",
            headers={"Authorization": f"Bearer {USER_B}"}
        )
        assert response.status_code == 200
        history = response.json()
        assert not any(item["query"] == "User A query" for item in history)

    @patch("app.db")
    def test_user_b_cannot_read_user_a_analytics(self, mock_db):
        mock_db.auth.get_user.side_effect = mock_supabase_auth_get_user
        init_db()

        # Seed analytics log for User A
        log_search("User A search text", "Jarvis-portfolio", 95, 120.5, user_id=USER_A)

        # User B fetches analytics
        response = client.get(
            "/analytics",
            headers={"Authorization": f"Bearer {USER_B}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert not any(q["query"] == "User A search text" for q in data.get("recent_queries", []))

    def test_legacy_telemetry_rows_without_user_id_are_quarantined(self):
        init_db()

        # Insert legacy row where user_id IS NULL directly into SQLite
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT INTO search_logs (query, repo_filter, confidence, latency_ms) VALUES ('Legacy query', 'ALL', 80, 50)")
            conn.execute("INSERT INTO chat_history (thread_id, query, answer) VALUES ('t_old', 'Legacy chat', 'Legacy ans')")

        # Fetch for User A
        analytics_a = get_analytics(user_id=USER_A)
        history_a = get_chat_history(user_id=USER_A)

        # Legacy row MUST NOT be present for User A
        assert not any(q["query"] == "Legacy query" for q in analytics_a["recent_queries"])
        assert not any(h["query"] == "Legacy chat" for h in history_a)

        # Fetch for User B
        analytics_b = get_analytics(user_id=USER_B)
        history_b = get_chat_history(user_id=USER_B)

        # Legacy row MUST NOT be present for User B either
        assert not any(q["query"] == "Legacy query" for q in analytics_b["recent_queries"])
        assert not any(h["query"] == "Legacy chat" for h in history_b)

    @patch("app.db")
    def test_user_b_cannot_delete_user_a_repo(self, mock_db):
        mock_db.auth.get_user.side_effect = mock_supabase_auth_get_user
        response = client.post(
            "/delete-repo",
            headers={"Authorization": f"Bearer {USER_B}"},
            params={"repo_name": "Jarvis-portfolio", "user_id": USER_A}  # Mismatch!
        )
        assert response.status_code == 403
