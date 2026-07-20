import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
import logging

import app
from app import app as fastapi_app


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
    # When dependencies are unconfigured/disconnected, expect 503 Service Unavailable
    with patch("app.db", None), patch.dict("os.environ", {}, clear=True):
        response = client.get("/readiness")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "degraded"
        assert data["database"] == "disconnected"
        assert data["embeddings"] == "unconfigured"


def test_readiness_endpoint_ready(client):
    # When all dependencies are ready, expect 200 OK
    with patch("app.db", MagicMock()), patch.dict("os.environ", {"HF_TOKEN": "valid_token"}):
        response = client.get("/readiness")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert data["database"] == "connected"
        assert data["embeddings"] == "ready"


def test_search_validation_empty_query(client):
    response = client.post("/search", json={"query": ""})
    assert response.status_code == 422


def test_search_validation_query_too_long(client):
    long_query = "a" * 2001
    response = client.post("/search", json={"query": long_query})
    assert response.status_code == 422


def test_search_validation_invalid_top_k(client):
    response = client.post("/search", json={"query": "test", "top_k": 0})
    assert response.status_code == 422

    response = client.post("/search", json={"query": "test", "top_k": 100})
    assert response.status_code == 422


@patch("app.db")
@patch("app.get_embedding")
@patch("requests.post")
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
            json={"query": "How do I run main?", "top_k": 5, "user_id": "user-abc"},
        )

    assert response.status_code == 200
    mock_db.rpc.assert_called_once_with(
        "search_code_snippets",
        {
            "query_embedding": [0.1] * 384,
            "match_count": 5,
            "p_user_id": "user-abc",
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
            json={"query": "How do I run main?", "top_k": 5, "user_id": "user-abc"},
        )

    assert response.status_code == 500
    data = response.json()
    assert "user isolation query could not be executed safely" in data["detail"]
    assert "PGRST202" not in data["detail"]


@patch("app.db")
@patch("app.get_embedding")
def test_search_secret_bearing_exception_sanitization(mock_get_embedding, mock_db, caplog, client):
    """
    Regression Test: Proves that secret-bearing exception content (database URL, passwords, tokens)
    does NOT leak into public API responses or server logs.
    """
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
            json={"query": "How do I connect?", "user_id": "user-secret-test"},
        )

    assert response.status_code == 500
    response_text = response.text
    log_text = caplog.text

    # Assert secrets do NOT appear in API response
    assert fake_secret_password not in response_text
    assert fake_db_url not in response_text
    assert fake_token not in response_text

    # Assert secrets do NOT appear in captured server logs
    assert fake_secret_password not in log_text
    assert fake_db_url not in log_text
    assert fake_token not in log_text

    # Assert that sanitized structured log message WAS recorded instead
    assert "User-scoped vector search RPC failed [op=search_user_rpc, exc_type=Exception]" in log_text


@patch("app.db")
@patch("app.get_embedding")
def test_search_upstream_embedding_failure(mock_get_embedding, mock_db, client):
    mock_get_embedding.return_value = None

    response = client.post("/search", json={"query": "test search"})
    assert response.status_code == 502
    assert "Embedding service unavailable" in response.json()["detail"]


@patch("app.db")
@patch("app.get_embedding")
def test_search_db_not_initialized(mock_get_embedding, mock_db, client):
    with patch("app.db", None):
        response = client.post("/search", json={"query": "test search"})
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
        "user_id": "user-123",
    }
    response = client.post("/index", json=payload)
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
    response = client.post("/index", json=payload)
    assert response.status_code == 422


@patch("app.db")
def test_ingest_endpoint_non_https_validation(mock_db, client):
    payload = {
        "repo_url": "http://github.com/user/repo",
        "user_id": "user-123",
    }
    response = client.post("/ingest", json=payload)
    assert response.status_code == 400
    assert "Only HTTPS" in response.json()["detail"]


@patch("app.db")
def test_user_repos_endpoint(mock_db, client):
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.execute.return_value = MagicMock(
        data=[{"repo_name": "repo-a"}, {"repo_name": "repo-b"}, {"repo_name": "repo-a"}]
    )
    mock_db.table.return_value = mock_table

    response = client.get("/user-repos?user_id=user-123")
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

    response = client.get("/graph-data?user_id=user-123")
    assert response.status_code == 200
    data = response.json()
    assert "nodes" in data
    assert "links" in data
    assert len(data["nodes"]) == 4  # Core + Repo + 2 Files
