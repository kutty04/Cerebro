import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from app import app
from fastapi import HTTPException

client = TestClient(app)

# Fixtures for multi-tenant and multi-repo tests
USER_A_ID = "user-a-1111-1111-1111-111111111111"
USER_B_ID = "user-b-2222-2222-2222-222222222222"

JARVIS_REPO_ID = "repo-jarvis-aaaa-aaaa-aaaaaaaaaaaa"
BUS_REPO_ID = "repo-bus-bbbb-bbbb-bbbbbbbbbbbb"
USER_B_REPO_ID = "repo-userb-cccc-cccc-cccccccccccc"

MOCK_REPOSITORIES_DB = [
    {
        "id": JARVIS_REPO_ID,
        "user_id": USER_A_ID,
        "repository_name": "Jarvis-portfolio",
        "repo_name": "Jarvis-portfolio",
        "canonical_url": "https://github.com/kutty04/Jarvis-portfolio.git",
        "active_index_version": "v1",
        "status": "ready"
    },
    {
        "id": BUS_REPO_ID,
        "user_id": USER_A_ID,
        "repository_name": "bus-crowding",
        "repo_name": "bus-crowding",
        "canonical_url": "https://github.com/kutty04/bus-crowding.git",
        "active_index_version": "v2",
        "status": "ready"
    },
    {
        "id": USER_B_REPO_ID,
        "user_id": USER_B_ID,
        "repository_name": "user-b-private-repo",
        "repo_name": "user-b-private-repo",
        "canonical_url": "https://github.com/userb/private.git",
        "active_index_version": "v1",
        "status": "ready"
    }
]

MOCK_SNIPPETS_DB = [
    {
        "id": 101,
        "user_id": USER_A_ID,
        "repository_id": JARVIS_REPO_ID,
        "repo_name": "Jarvis-portfolio",
        "file_path": "main.py",
        "language": "python",
        "code_content": "def initialize_jarvis(): print('Jarvis AI online')",
        "source_url": "https://github.com/kutty04/Jarvis-portfolio/blob/main/main.py",
        "index_version": "v1"
    },
    {
        "id": 102,
        "user_id": USER_A_ID,
        "repository_id": JARVIS_REPO_ID,
        "repo_name": "Jarvis-portfolio",
        "file_path": "config.py",
        "language": "python",
        "code_content": "JARVIS_CONFIG = {'version': '2.0'}",
        "source_url": "https://github.com/kutty04/Jarvis-portfolio/blob/main/config.py",
        "index_version": "v1"
    },
    {
        "id": 201,
        "user_id": USER_A_ID,
        "repository_id": BUS_REPO_ID,
        "repo_name": "bus-crowding",
        "file_path": "tracker.py",
        "language": "python",
        "code_content": "def predict_bus_crowding(): return 'High'",
        "source_url": "https://github.com/kutty04/bus-crowding/blob/main/tracker.py",
        "index_version": "v2"
    },
    {
        "id": 301,
        "user_id": USER_B_ID,
        "repository_id": USER_B_REPO_ID,
        "repo_name": "user-b-private-repo",
        "file_path": "secret.py",
        "language": "python",
        "code_content": "SECRET_KEY = 'user_b_private'",
        "source_url": "https://github.com/userb/private/blob/main/secret.py",
        "index_version": "v1"
    }
]


class MockSupabaseQuery:
    def __init__(self, table_name):
        self.table_name = table_name
        self.filters = []
        self.select_cols = "*"
        self.limit_val = None

    def select(self, cols):
        self.select_cols = cols
        return self

    def eq(self, column, value):
        self.filters.append(('eq', column, value))
        return self

    def or_(self, cond_str):
        self.filters.append(('or', cond_str))
        return self

    def limit(self, num):
        self.limit_val = num
        return self

    def ilike(self, column, pattern):
        self.filters.append(('ilike', column, pattern))
        return self

    def execute(self):
        dataset = MOCK_REPOSITORIES_DB if self.table_name == "user_repositories" else MOCK_SNIPPETS_DB
        results = list(dataset)

        for op, col, val in self.filters:
            if op == 'eq':
                results = [r for r in results if str(r.get(col)) == str(val)]
            elif op == 'ilike':
                clean_pat = val.replace('%', '').lower()
                results = [r for r in results if clean_pat in str(r.get(col, '')).lower()]
            elif op == 'or':
                parts = [p.split('.eq.') for p in val.split(',')]
                matched = []
                for r in results:
                    for c_name, c_val in parts:
                        if str(r.get(c_name)) == str(c_val):
                            matched.append(r)
                            break
                results = matched

        if self.limit_val is not None:
            results = results[:self.limit_val]

        res = MagicMock()
        res.data = results
        return res


class MockSupabaseRPC:
    def __init__(self, rpc_name, params):
        self.rpc_name = rpc_name
        self.params = params

    def execute(self):
        user_id = self.params.get("p_user_id")
        repo_id = self.params.get("p_repository_id")
        ver = self.params.get("p_index_version")

        results = [
            r for r in MOCK_SNIPPETS_DB 
            if str(r["user_id"]) == str(user_id)
            and (repo_id is None or str(r["repository_id"]) == str(repo_id))
            and (ver is None or str(r["index_version"]) == str(ver))
        ]

        res = MagicMock()
        res.data = results
        return res


class MockSupabaseDB:
    def table(self, name):
        return MockSupabaseQuery(name)

    def rpc(self, name, params):
        return MockSupabaseRPC(name, params)


@patch("app.db", MockSupabaseDB())
@patch("app.get_embedding", return_value=[0.1] * 1536)
class TestRepositoryScopeIntegrity:

    def test_search_scoped_to_jarvis_never_returns_bus_snippets(self, mock_embed):
        response = client.post("/search", json={
            "query": "What is this project about?",
            "user_id": USER_A_ID,
            "repository_id": JARVIS_REPO_ID,
            "repo_filter": "Jarvis-portfolio"
        })

        assert response.status_code == 200
        data = response.json()
        assert len(data["sources"]) > 0
        for s in data["sources"]:
            assert s["repo"] == "Jarvis-portfolio"
            assert "bus-crowding" not in s["repo"]
            assert "tracker.py" not in s["file"]

    def test_graph_scoped_to_jarvis_contains_only_jarvis_nodes(self, mock_embed):
        response = client.get(f"/graph-data?user_id={USER_A_ID}&repository_id={JARVIS_REPO_ID}")

        assert response.status_code == 200
        data = response.json()
        node_ids = [n["id"] for n in data["nodes"] if n["id"] != "ME"]
        assert "Jarvis-portfolio" in node_ids
        assert "bus-crowding" not in node_ids
        assert all("bus-crowding" not in nid for nid in node_ids)

    def test_all_projects_scope_contains_both_repositories(self, mock_embed):
        response = client.get(f"/graph-data?user_id={USER_A_ID}")

        assert response.status_code == 200
        data = response.json()
        node_ids = [n["id"] for n in data["nodes"] if n["id"] != "ME"]
        assert "Jarvis-portfolio" in node_ids
        assert "bus-crowding" in node_ids

    def test_user_b_cannot_query_user_a_repository_id(self, mock_embed):
        response = client.post("/search", json={
            "query": "Give me access",
            "user_id": USER_B_ID,
            "repository_id": JARVIS_REPO_ID  # User A's repo!
        })

        assert response.status_code == 400
        assert "Invalid or unauthorized repository selection" in response.json()["detail"]

    def test_invalid_repository_id_does_not_fall_back_to_all_repos(self, mock_embed):
        response = client.post("/search", json={
            "query": "Show files",
            "user_id": USER_A_ID,
            "repository_id": "non-existent-repo-999"
        })

        assert response.status_code == 400
        assert "Invalid or unauthorized repository selection" in response.json()["detail"]

    def test_user_repos_returns_authoritative_repositories_list(self, mock_embed):
        response = client.get(f"/user-repos?user_id={USER_A_ID}")

        assert response.status_code == 200
        data = response.json()
        assert "repos" in data
        assert "repositories" in data
        assert len(data["repositories"]) == 2
        repo_ids = [r["id"] for r in data["repositories"]]
        assert JARVIS_REPO_ID in repo_ids
        assert BUS_REPO_ID in repo_ids
