import os
os.environ["HF_TOKEN"] = "mocked-hf-token-value"
import json
import pytest
import sqlite3
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app import app as fastapi_app, GroundedModelOutput, SearchResponse
from security.auth import AuthenticatedUser, get_current_user

DB_PATH = "coderag_telemetry.db"

@pytest.fixture
def client():
    from fastapi import Request
    def mock_user(request: Request):
        auth = request.headers.get("Authorization", "")
        token = auth.replace("Bearer ", "").strip()
        user_id = token if token.startswith("user-") else "user-123"
        return AuthenticatedUser(id=user_id, email=f"{user_id}@test.com", access_token=token)

    fastapi_app.dependency_overrides[get_current_user] = mock_user
    with TestClient(fastapi_app) as tc:
        yield tc
    fastapi_app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def clean_cache():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM query_cache")
    except Exception:
        pass
    yield
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM query_cache")
    except Exception:
        pass


@pytest.fixture
def mock_supabase():
    with patch("app.db") as mock_db:
        yield mock_db


@pytest.fixture
def mock_embed():
    with patch("app.get_embedding", return_value=[0.1]*384) as m:
        yield m


# 1. AUTHORIZATION AND SCOPE TESTS
def test_user_isolation(client, mock_supabase, mock_embed):
    mock_repos = [{"id": "repo-A", "repository_name": "repoA", "active_index_version": "v1"}]
    
    with patch("db_adapter.DatabaseAdapter.list_owned_repos", return_value=mock_repos):
        with patch("db_adapter.DatabaseAdapter.get_repo_by_name") as mock_get_repo:
            mock_get_repo.side_effect = HTTPException(status_code=404, detail="Repository not found or access denied.")
            
            payload = {
                "query": "find something",
                "repo_filter": "repoB",
                "user_id": "user-A"
            }
            
            headers = {"Authorization": "Bearer user-A"}
            response = client.post("/search", json=payload, headers=headers)
            assert response.status_code == 404


def test_inactive_index_exclusion(client, mock_supabase, mock_embed):
    mock_repos = [{"id": "repo-A", "repository_name": "repoA", "active_index_version": "v2"}]
    
    with patch("db_adapter.DatabaseAdapter.list_owned_repos", return_value=mock_repos):
        mock_rpc_res = MagicMock()
        mock_rpc_res.data = [
            {"id": 1, "repository_id": "repo-A", "index_version": "v2", "file_path": "a.py"},
            {"id": 2, "repository_id": "repo-A", "index_version": "v1", "file_path": "b.py"}
        ]
        mock_supabase.rpc().execute.return_value = mock_rpc_res
        
        mock_select = MagicMock()
        mock_select.data = [
            {"id": 1, "repository_id": "repo-A", "index_version": "v2", "file_path": "a.py", "user_id": "user-123", "code_content": "def f(): pass", "source_url": ""},
            {"id": 2, "repository_id": "repo-A", "index_version": "v1", "file_path": "b.py", "user_id": "user-123", "code_content": "def g(): pass", "source_url": ""}
        ]
        mock_supabase.table().select().eq().in_().execute.return_value = mock_select
        mock_supabase.table().select().eq().ilike().limit().execute.return_value = MagicMock(data=[])
        
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "answer": "Active index test",
                            "summary": "Short summary",
                            "citation_ids": ["src-1"],
                            "follow_ups": ["Next question"],
                            "limitations": []
                        })
                    }
                }]
            }
            mock_post.return_value = mock_resp
            
            headers = {"Authorization": "Bearer user-123"}
            response = client.post("/search", json={"query": "test query", "user_id": "user-123"}, headers=headers)
            assert response.status_code == 200
            data = response.json()
            assert len(data["sources"]) == 1
            assert data["sources"][0]["file"] == "a.py"


# 2. HYBRID RETRIEVAL AND RRF
def test_rrf_and_file_diversity(client, mock_supabase, mock_embed):
    mock_repos = [{"id": "repo-A", "repository_name": "repoA", "active_index_version": "v1"}]
    with patch("db_adapter.DatabaseAdapter.list_owned_repos", return_value=mock_repos):
        vector_rpc = MagicMock()
        vector_rpc.data = [{"id": 1}, {"id": 2}, {"id": 3}]
        mock_supabase.rpc().execute.return_value = vector_rpc
        
        mock_select = MagicMock()
        mock_select.data = [
            {"id": 1, "repository_id": "repo-A", "index_version": "v1", "file_path": "a.py", "user_id": "user-123", "code_content": "doc1", "source_url": ""},
            {"id": 2, "repository_id": "repo-A", "index_version": "v1", "file_path": "a.py", "user_id": "user-123", "code_content": "doc2", "source_url": ""},
            {"id": 3, "repository_id": "repo-A", "index_version": "v1", "file_path": "a.py", "user_id": "user-123", "code_content": "doc3", "source_url": ""}
        ]
        mock_supabase.table().select().eq().in_().execute.return_value = mock_select

        mock_kw = MagicMock()
        mock_kw.data = [
            {"id": 4, "repository_id": "repo-A", "index_version": "v1", "file_path": "b.py", "user_id": "user-123", "code_content": "doc4", "source_url": ""},
            {"id": 1, "repository_id": "repo-A", "index_version": "v1", "file_path": "a.py", "user_id": "user-123", "code_content": "doc1", "source_url": ""}
        ]
        mock_supabase.table().select().eq().ilike().limit().execute.return_value = mock_kw
        
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "answer": "fused results",
                            "summary": "",
                            "citation_ids": ["src-1", "src-2"],
                            "follow_ups": [],
                            "limitations": []
                        })
                    }
                }]
            }
            mock_post.return_value = mock_resp
            
            headers = {"Authorization": "Bearer user-123"}
            response = client.post("/search", json={"query": "test query", "user_id": "user-123", "top_k": 5}, headers=headers)
            assert response.status_code == 200
            data = response.json()
            sources = data["sources"]
            
            assert len(sources) == 3
            a_py_sources = [s for s in sources if s["file"] == "a.py"]
            assert len(a_py_sources) == 2


# 3. GROUNDING AND ZERO EVIDENCE
def test_zero_evidence_fallback(client, mock_supabase, mock_embed):
    mock_repos = [{"id": "repo-A", "repository_name": "repoA", "active_index_version": "v1"}]
    with patch("db_adapter.DatabaseAdapter.list_owned_repos", return_value=mock_repos):
        mock_supabase.rpc().execute.return_value = MagicMock(data=[])
        mock_supabase.table().select().eq().ilike().limit().execute.return_value = MagicMock(data=[])
        
        with patch("requests.post") as mock_post:
            headers = {"Authorization": "Bearer user-123"}
            response = client.post("/search", json={"query": "missing feature", "user_id": "user-123"}, headers=headers)
            
            assert response.status_code == 200
            data = response.json()
            assert "couldn't find" in data["answer"].lower()
            assert not mock_post.called
            assert len(data["sources"]) == 0
            assert len(data["limitations"]) > 0


# 4. CACHE AND RESILIENCE
def test_cache_miss_on_index_or_strategy_change(client, mock_supabase, mock_embed):
    mock_repos = [{"id": "repo-A", "repository_name": "repoA", "active_index_version": "v1"}]
    with patch("db_adapter.DatabaseAdapter.list_owned_repos", return_value=mock_repos):
        mock_rpc_res = MagicMock()
        mock_rpc_res.data = [{"id": 1}]
        mock_supabase.rpc().execute.return_value = mock_rpc_res
        
        mock_select = MagicMock()
        mock_select.data = [{"id": 1, "repository_id": "repo-A", "index_version": "v1", "file_path": "a.py", "user_id": "user-123", "code_content": "doc1", "source_url": ""}]
        mock_supabase.table().select().eq().in_().execute.return_value = mock_select
        mock_supabase.table().select().eq().ilike().limit().execute.return_value = MagicMock(data=[])

        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "answer": "cached answer",
                            "summary": "",
                            "citation_ids": ["src-1"],
                            "follow_ups": [],
                            "limitations": []
                        })
                    }
                }]
            }
            mock_post.return_value = mock_resp
            
            headers = {"Authorization": "Bearer user-123"}
            payload = {"query": "cache query", "user_id": "user-123"}
            
            res1 = client.post("/search", json=payload, headers=headers)
            assert res1.status_code == 200
            assert mock_post.call_count == 1
            
            mock_post.reset_mock()
            res2 = client.post("/search", json=payload, headers=headers)
            assert res2.status_code == 200
            assert mock_post.call_count == 0
            
            mock_repos[0]["active_index_version"] = "v2"
            mock_select.data[0]["index_version"] = "v2"
            mock_rpc_res.data = [{"id": 1}]
            
            mock_post.reset_mock()
            res3 = client.post("/search", json=payload, headers=headers)
            assert res3.status_code == 200
            assert mock_post.call_count == 1


def test_rate_limit_and_error_mappings(client, mock_supabase, mock_embed):
    mock_repos = [{"id": "repo-A", "repository_name": "repoA", "active_index_version": "v1"}]
    with patch("db_adapter.DatabaseAdapter.list_owned_repos", return_value=mock_repos):
        mock_supabase.rpc().execute.return_value = MagicMock(data=[{"id": 1}])
        mock_select = MagicMock()
        mock_select.data = [{"id": 1, "repository_id": "repo-A", "index_version": "v1", "file_path": "a.py", "user_id": "user-123", "code_content": "doc1", "source_url": ""}]
        mock_supabase.table().select().eq().in_().execute.return_value = mock_select
        mock_supabase.table().select().eq().ilike().limit().execute.return_value = MagicMock(data=[])

        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 429
            mock_post.return_value = mock_resp
            
            headers = {"Authorization": "Bearer user-123"}
            response = client.post("/search", json={"query": "test rate limit", "user_id": "user-123"}, headers=headers)
            assert response.status_code == 429
            assert "rate limit" in response.json()["detail"].lower()
