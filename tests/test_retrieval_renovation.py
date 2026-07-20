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


def test_cache_full_restore_and_sources_array_only(client, mock_supabase, mock_embed):
    mock_repos = [{"id": "repo-A", "repository_name": "repoA", "active_index_version": "v1"}]
    with patch("db_adapter.DatabaseAdapter.list_owned_repos", return_value=mock_repos):
        mock_supabase.rpc().execute.return_value = MagicMock(data=[{"id": 1}])
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
                            "answer": "fused results",
                            "summary": "fused summary",
                            "citation_ids": ["src-1"],
                            "follow_ups": ["next trace?"],
                            "limitations": ["limited scope"]
                        })
                    }
                }]
            }
            mock_post.return_value = mock_resp

            headers = {"Authorization": "Bearer user-123"}
            response = client.post("/search", json={"query": "test query", "user_id": "user-123"}, headers=headers)
            assert response.status_code == 200
            data = response.json()
            assert data["answer"] == "fused results"
            assert data["summary"] == "fused summary"
            assert len(data["sources"]) == 1
            assert data["sources"][0]["file"] == "a.py"
            assert data["follow_ups"] == ["next trace?"]
            assert data["limitations"] == ["limited scope"]
            assert "retrievalTimeMs" in data["metadata"]

            # Query database directly to assert column integrity
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT sources_json, response_json FROM query_cache WHERE user_id = 'user-123'")
                row = cursor.fetchone()
                assert row is not None
                
                # sources_json contains ONLY a list of sources
                sources_data = json.loads(row["sources_json"])
                assert isinstance(sources_data, list)
                assert len(sources_data) == 1
                assert "code_content" in sources_data[0] or "code" in sources_data[0]
                
                # response_json contains versioned metadata envelope
                resp_data = json.loads(row["response_json"])
                assert resp_data["cache_schema_version"] == "grounded-response-v1"
                assert resp_data["answer"] == "fused results"
                assert resp_data["summary"] == "fused summary"
                assert resp_data["follow_ups"] == ["next trace?"]
                assert resp_data["limitations"] == ["limited scope"]

            # Call search again to hit the cache, asserting full restore
            mock_post.reset_mock()
            response_hit = client.post("/search", json={"query": "test query", "user_id": "user-123"}, headers=headers)
            assert response_hit.status_code == 200
            hit_data = response_hit.json()
            assert hit_data["answer"] == "fused results"
            assert hit_data["summary"] == "fused summary"
            assert len(hit_data["sources"]) == 1
            assert hit_data["follow_ups"] == ["next trace?"]
            assert hit_data["limitations"] == ["limited scope"]
            assert not mock_post.called


def test_legacy_cache_and_mixed_data_fallback(client, mock_supabase, mock_embed):
    # Insert legacy sources-only record (sources_json is flat list, response_json is None)
    from telemetry import get_cache_key
    key = get_cache_key(query="legacy query", user_id="user-123", repo_filter=None, model="meta-llama/Llama-3.1-8B-Instruct", index_version="repo-A:v1", retrieval_strategy="rrf-v1")
    
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO query_cache (query_hash, user_id, repo_filter, answer, sources_json, response_json, confidence) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key, "user-123", "ALL", "legacy answer", json.dumps([{"file": "legacy.py", "code": "print(1)"}]), None, 80)
        )

    mock_repos = [{"id": "repo-A", "repository_name": "repoA", "active_index_version": "v1"}]
    with patch("db_adapter.DatabaseAdapter.list_owned_repos", return_value=mock_repos):
        with patch("requests.post") as mock_post:
            headers = {"Authorization": "Bearer user-123"}
            response = client.post("/search", json={"query": "legacy query", "user_id": "user-123"}, headers=headers)
            assert response.status_code == 200
            data = response.json()
            assert data["answer"] == "legacy answer"
            assert len(data["sources"]) == 1
            assert data["sources"][0]["file"] == "legacy.py"
            assert data["summary"] is None
            assert data["limitations"] == []
            assert not mock_post.called

    # Insert mixed legacy data in sources_json (sources_json is dict, response_json is None)
    key_mixed = get_cache_key(query="mixed query", user_id="user-123", repo_filter=None, model="meta-llama/Llama-3.1-8B-Instruct", index_version="repo-A:v1", retrieval_strategy="rrf-v1")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO query_cache (query_hash, user_id, repo_filter, answer, sources_json, response_json, confidence) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key_mixed, "user-123", "ALL", "mixed answer", json.dumps({"sources": [{"file": "mixed.py"}], "summary": "oops"}), None, 80)
        )

    # Mixed legacy cache entry must safely produce a cache miss and call LLM
    with patch("db_adapter.DatabaseAdapter.list_owned_repos", return_value=mock_repos):
        mock_supabase.rpc().execute.return_value = MagicMock(data=[{"id": 1}])
        mock_select = MagicMock()
        mock_select.data = [{"id": 1, "repository_id": "repo-A", "index_version": "v1", "file_path": "a.py", "user_id": "user-123", "code_content": "doc1", "source_url": ""}]
        mock_supabase.table().select().eq().in_().execute.return_value = mock_select
        mock_supabase.table().select().eq().ilike().limit().execute.return_value = MagicMock(data=[])

        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"choices": [{"message": {"content": json.dumps({"answer": "llm output", "summary": "", "citation_ids": [], "follow_ups": [], "limitations": []})}}]}
            mock_post.return_value = mock_resp

            headers = {"Authorization": "Bearer user-123"}
            response = client.post("/search", json={"query": "mixed query", "user_id": "user-123"}, headers=headers)
            assert response.status_code == 200
            assert response.json()["answer"] == "llm output"
            assert mock_post.called

    # Insert schema-version changed entry
    key_v_change = get_cache_key(query="vchange query", user_id="user-123", repo_filter=None, model="meta-llama/Llama-3.1-8B-Instruct", index_version="repo-A:v1", retrieval_strategy="rrf-v1")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO query_cache (query_hash, user_id, repo_filter, answer, sources_json, response_json, confidence) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key_v_change, "user-123", "ALL", "vchange answer", json.dumps([]), json.dumps({"cache_schema_version": "grounded-response-v999", "answer": "old version"}), 80)
        )

    # Schema-version changed response must produce cache miss
    with patch("db_adapter.DatabaseAdapter.list_owned_repos", return_value=mock_repos):
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"choices": [{"message": {"content": json.dumps({"answer": "llm output v2", "summary": "", "citation_ids": [], "follow_ups": [], "limitations": []})}}]}
            mock_post.return_value = mock_resp

            headers = {"Authorization": "Bearer user-123"}
            response = client.post("/search", json={"query": "vchange query", "user_id": "user-123"}, headers=headers)
            assert response.status_code == 200
            assert response.json()["answer"] == "llm output v2"
            assert mock_post.called


def test_invalid_and_unknown_citation_filtering(client, mock_supabase, mock_embed):
    mock_repos = [{"id": "repo-A", "repository_name": "repoA", "active_index_version": "v1"}]
    with patch("db_adapter.DatabaseAdapter.list_owned_repos", return_value=mock_repos):
        mock_supabase.rpc().execute.return_value = MagicMock(data=[{"id": 1}])
        mock_select = MagicMock()
        mock_select.data = [{"id": 1, "repository_id": "repo-A", "index_version": "v1", "file_path": "a.py", "user_id": "user-123", "code_content": "doc1", "source_url": "https://github.com/a.py"}]
        mock_supabase.table().select().eq().in_().execute.return_value = mock_select
        mock_supabase.table().select().eq().ilike().limit().execute.return_value = MagicMock(data=[])

        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "answer": "Model cited unknown source",
                            "summary": "",
                            # Model cited src-1 (valid) and src-999 (invalid/unknown citation ID)
                            "citation_ids": ["src-1", "src-999"],
                            "follow_ups": [],
                            "limitations": []
                        })
                    }
                }]
            }
            mock_post.return_value = mock_resp

            headers = {"Authorization": "Bearer user-123"}
            response = client.post("/search", json={"query": "test citation filter", "user_id": "user-123"}, headers=headers)
            assert response.status_code == 200
            data = response.json()
            # Only src-1 (a.py) should be in final sources list; src-999 must be ignored/dropped
            assert len(data["sources"]) == 1
            assert data["sources"][0]["file"] == "a.py"


def test_timing_values_correctness(client, mock_supabase, mock_embed):
    mock_repos = [{"id": "repo-A", "repository_name": "repoA", "active_index_version": "v1"}]
    with patch("db_adapter.DatabaseAdapter.list_owned_repos", return_value=mock_repos):
        mock_supabase.rpc().execute.return_value = MagicMock(data=[{"id": 1}])
        mock_select = MagicMock()
        mock_select.data = [{"id": 1, "repository_id": "repo-A", "index_version": "v1", "file_path": "a.py", "user_id": "user-123", "code_content": "doc1", "source_url": ""}]
        mock_supabase.table().select().eq().in_().execute.return_value = mock_select
        mock_supabase.table().select().eq().ilike().limit().execute.return_value = MagicMock(data=[])

        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"choices": [{"message": {"content": json.dumps({"answer": "ok", "summary": "", "citation_ids": [], "follow_ups": [], "limitations": []})}}]}
            mock_post.return_value = mock_resp

            headers = {"Authorization": "Bearer user-123"}
            response = client.post("/search", json={"query": "test timing", "user_id": "user-123"}, headers=headers)
            assert response.status_code == 200
            metadata = response.json()["metadata"]
            assert metadata["retrievalTimeMs"] >= 0
            assert metadata["generationTimeMs"] >= 0
            assert metadata["totalTimeMs"] >= 0


def test_retry_matrix_and_rate_limiting(client, mock_supabase, mock_embed):
    mock_repos = [{"id": "repo-A", "repository_name": "repoA", "active_index_version": "v1"}]
    with patch("db_adapter.DatabaseAdapter.list_owned_repos", return_value=mock_repos):
        mock_supabase.rpc().execute.return_value = MagicMock(data=[{"id": 1}])
        mock_select = MagicMock()
        mock_select.data = [{"id": 1, "repository_id": "repo-A", "index_version": "v1", "file_path": "a.py", "user_id": "user-123", "code_content": "doc1", "source_url": ""}]
        mock_supabase.table().select().eq().in_().execute.return_value = mock_select
        mock_supabase.table().select().eq().ilike().limit().execute.return_value = MagicMock(data=[])

        # 1. 429 produces no retry and propagates Retry-After header
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 429
            mock_resp.headers = {"Retry-After": "45"}
            mock_post.return_value = mock_resp

            headers = {"Authorization": "Bearer user-123"}
            response = client.post("/search", json={"query": "rate query", "user_id": "user-123"}, headers=headers)
            assert response.status_code == 429
            assert response.headers.get("Retry-After") == "45"
            assert mock_post.call_count == 1  # No retries!

            # Check cache: must NOT write a cache entry on 429
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM query_cache WHERE user_id = 'user-123'")
                assert cursor.fetchone()[0] == 0

        # 2. Transient failures (502, 503, 504) retry at most once
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 503
            mock_post.return_value = mock_resp

            response = client.post("/search", json={"query": "transient query", "user_id": "user-123"}, headers=headers)
            assert response.status_code == 503
            assert mock_post.call_count == 2  # Original call + 1 retry = 2 calls!


def test_secret_leak_prevention(client, mock_supabase, mock_embed, caplog):
    import logging
    mock_repos = [{"id": "repo-A", "repository_name": "repoA", "active_index_version": "v1"}]
    with patch("db_adapter.DatabaseAdapter.list_owned_repos", return_value=mock_repos):
        # Embed a fake secret/credential in the DB RPC failure exception text
        fake_secret_pw = "SuperSecretDBPassword999!"
        mock_supabase.rpc().execute.side_effect = Exception(f"Connection failed with password={fake_secret_pw}")

        headers = {"Authorization": "Bearer user-123"}
        with caplog.at_level(logging.ERROR):
            response = client.post("/search", json={"query": "secret query", "user_id": "user-123"}, headers=headers)
            
            # API must fail closed with 500
            assert response.status_code == 500
            
            # Sanitized response and logs must NOT leak the fake secret password
            assert fake_secret_pw not in response.text
            assert fake_secret_pw not in caplog.text
