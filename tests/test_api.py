import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

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


def test_readiness_endpoint(client):
    response = client.get("/readiness")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "database" in data
    assert "embeddings" in data
    assert "llm" in data


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
def test_search_success_mocked(mock_req_post, mock_get_embedding, mock_db, client):
    # Mock embedding
    mock_get_embedding.return_value = [0.1] * 384

    # Mock Supabase RPC
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

    # Mock Supabase keyword search table
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.ilike.return_value = mock_table
    mock_table.limit.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[])
    mock_db.table.return_value = mock_table

    # Mock HF Router LLM response
    mock_llm_response = MagicMock()
    mock_llm_response.status_code = 200
    mock_llm_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": "This is a test answer based on the code.\n\nFOLLOW_UPS:\n- Question 1\n- Question 2\n- Question 3"
                }
            }
        ]
    }
    mock_req_post.return_value = mock_llm_response

    # Set dummy token for test
    with patch.dict("os.environ", {"HF_TOKEN": "mock_token"}):
        response = client.post("/search", json={"query": "How do I run main?", "top_k": 5})

    assert response.status_code == 200
    data = response.json()
    assert "This is a test answer" in data["answer"]
    assert len(data["sources"]) == 1
    assert data["sources"][0]["repo"] == "test-repo"
    assert len(data["follow_ups"]) == 3
    assert data["confidence"] > 0


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


def test_ingest_endpoint_non_https_validation(client):
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
