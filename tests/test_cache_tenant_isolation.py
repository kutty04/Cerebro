import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app import app
from telemetry import get_cache_key, get_cached_query, set_cached_query

client = TestClient(app)

USER_A_ID = "11111111-1111-4111-a111-111111111111"
USER_B_ID = "22222222-2222-4222-a222-222222222222"
QUERY = "What is the entrypoint of this project?"


def mock_supabase_auth_get_user(token: str):
    """
    Mock Supabase auth client that verifies valid tokens.
    """
    if token == "valid-token-user-a":
        user_mock = MagicMock()
        user_mock.id = USER_A_ID
        res_mock = MagicMock()
        res_mock.user = user_mock
        return res_mock
    elif token == "valid-token-user-b":
        user_mock = MagicMock()
        user_mock.id = USER_B_ID
        res_mock = MagicMock()
        res_mock.user = user_mock
        return res_mock
    else:
        # Invalid / expired token raises exception or returns None
        raise Exception("Invalid JWT signature or token expired")


class TestFailClosedAuthentication:

    @patch("app.db")
    def test_missing_authorization_header_returns_401(self, mock_db):
        response = client.post("/search", json={"query": QUERY, "user_id": USER_A_ID})
        assert response.status_code == 401
        assert "missing Authorization header" in response.json()["detail"]

    @patch("app.db")
    def test_malformed_authorization_header_returns_401(self, mock_db):
        # 1. Non-bearer scheme
        res1 = client.post("/search", headers={"Authorization": "Basic token123"}, json={"query": QUERY})
        assert res1.status_code == 401
        assert "malformed Authorization header" in res1.json()["detail"]

        # 2. Empty token after Bearer
        res2 = client.post("/search", headers={"Authorization": "Bearer "}, json={"query": QUERY})
        assert res2.status_code == 401

    @patch("app.db")
    def test_invalid_expired_token_returns_401(self, mock_db):
        mock_db.auth.get_user.side_effect = Exception("JWT expired")
        response = client.post(
            "/search",
            headers={"Authorization": "Bearer invalid-or-expired-token"},
            json={"query": QUERY}
        )
        assert response.status_code == 401
        assert "invalid or expired token" in response.json()["detail"]

    @patch("app.db")
    def test_supabase_verification_failure_returns_401(self, mock_db):
        mock_db.auth.get_user.return_value = None
        response = client.post(
            "/search",
            headers={"Authorization": "Bearer fake-token-xyz"},
            json={"query": QUERY}
        )
        assert response.status_code == 401

    @patch("app.db")
    def test_arbitrary_bearer_text_never_becomes_authenticated_identity(self, mock_db):
        mock_db.auth.get_user.side_effect = Exception("User not found")
        response = client.post(
            "/search",
            headers={"Authorization": "Bearer usr_fake_identity_string"},
            json={"query": QUERY}
        )
        assert response.status_code == 401

    @patch("app.db")
    def test_request_user_id_without_authorization_header_returns_401(self, mock_db):
        # Even if request.user_id is passed in body, missing Authorization header must fail 401
        response = client.post(
            "/search",
            json={"query": QUERY, "user_id": USER_A_ID}
        )
        assert response.status_code == 401

    @patch("app.db")
    def test_user_a_session_submitting_user_b_body_returns_403(self, mock_db):
        mock_db.auth.get_user.side_effect = mock_supabase_auth_get_user
        response = client.post(
            "/search",
            headers={"Authorization": "Bearer valid-token-user-a"},
            json={"query": QUERY, "user_id": USER_B_ID}  # Mismatch!
        )
        assert response.status_code == 403
        assert "User context mismatch" in response.json()["detail"]

    @patch("app.db")
    @patch("app.get_embedding", return_value=[0.1] * 1536)
    def test_user_a_cannot_read_user_b_cached_all_projects_result(self, mock_embed, mock_db):
        mock_db.auth.get_user.side_effect = mock_supabase_auth_get_user

        # Seed cache for User B with verified UUID
        sources_b = [{"repo": "user-b-private", "file": "secret.py", "code": "secret = 123"}]
        set_cached_query(
            query=QUERY,
            user_id=USER_B_ID,
            repo_scope="ALL",
            answer="User B secret answer",
            sources=sources_b,
            confidence=90,
            index_version="v1"
        )

        # User A makes a query authenticated as User A
        response = client.post(
            "/search",
            headers={"Authorization": "Bearer valid-token-user-a"},
            json={"query": QUERY, "user_id": USER_A_ID}
        )

        # User A receives NO access to User B's cache
        assert response.status_code == 200
        assert "User B secret answer" not in response.text

    @patch("app.db")
    @patch("app.get_embedding", return_value=[0.1] * 1536)
    def test_valid_user_a_session_receives_own_cache_hit(self, mock_embed, mock_db):
        mock_db.auth.get_user.side_effect = mock_supabase_auth_get_user

        # Seed cache for User A
        sources_a = [{"repo": "Jarvis-portfolio", "file": "main.py", "code": "init()"}]
        set_cached_query(
            query=QUERY,
            user_id=USER_A_ID,
            repo_scope="ALL",
            answer="User A cached summary",
            sources=sources_a,
            confidence=95,
            index_version="v1"
        )

        response = client.post(
            "/search",
            headers={"Authorization": "Bearer valid-token-user-a"},
            json={"query": QUERY, "user_id": USER_A_ID}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["answer"] == "User A cached summary"
        assert data["sources"] == sources_a


class TestCacheKeyLogic:

    def test_cache_keys_are_unique_per_user_uuid(self):
        key_a = get_cache_key(query=QUERY, user_id=USER_A_ID, repo_scope="ALL", index_version="v1")
        key_b = get_cache_key(query=QUERY, user_id=USER_B_ID, repo_scope="ALL", index_version="v1")

        assert key_a != key_b
        assert len(key_a) == 64  # SHA-256 hex string

    def test_cache_keys_are_unique_per_repository_scope(self):
        key_jarvis = get_cache_key(query=QUERY, user_id=USER_A_ID, repo_scope="a1a1a1a1-aaaa-4aaa-a1a1-a1a1a1a1a1a1", index_version="v1")
        key_bus = get_cache_key(query=QUERY, user_id=USER_A_ID, repo_scope="b2b2b2b2-bbbb-4bbb-b2b2-b2b2b2b2b2b2", index_version="v1")
        key_all = get_cache_key(query=QUERY, user_id=USER_A_ID, repo_scope="ALL", index_version="v1")

        assert len({key_jarvis, key_bus, key_all}) == 3

    def test_cache_keys_are_unique_per_index_version(self):
        key_v1 = get_cache_key(query=QUERY, user_id=USER_A_ID, repo_scope="a1a1a1a1-aaaa-4aaa-a1a1-a1a1a1a1a1a1", index_version="v1")
        key_v2 = get_cache_key(query=QUERY, user_id=USER_A_ID, repo_scope="a1a1a1a1-aaaa-4aaa-a1a1-a1a1a1a1a1a1", index_version="v2")

        assert key_v1 != key_v2
