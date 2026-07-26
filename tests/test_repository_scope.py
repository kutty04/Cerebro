import pytest
import uuid
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from app import app
from fastapi import HTTPException

client = TestClient(app)

# Fixtures with RFC4122 UUID compliance
USER_A_ID = "11111111-1111-4111-a111-111111111111"
USER_B_ID = "22222222-2222-4222-a222-222222222222"

JARVIS_REPO_ID = "a1a1a1a1-aaaa-4aaa-a1a1-a1a1a1a1a1a1"
BUS_REPO_ID = "b2b2b2b2-bbbb-4bbb-b2b2-b2b2b2b2b2b2"
USER_B_REPO_ID = "c3c3c3c3-cccc-4ccc-c3c3-c3c3c3c3c3c3"

# Verify test UUIDs are valid RFC4122 UUIDs
for u_str in [USER_A_ID, USER_B_ID, JARVIS_REPO_ID, BUS_REPO_ID, USER_B_REPO_ID]:
    uuid.UUID(u_str)

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
        "code_content": "def initialize_jarvis(): print('Jarvis AI developer portfolio')",
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
        "code_content": "JARVIS_CONFIG = {'type': 'developer_portfolio', 'projects': ['bus_crowding_tracker']}",
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
        self._inserted = None
        self._is_update = False
        self._is_delete = False
        self._update_data = None

    def select(self, cols):
        self.select_cols = cols
        return self

    def eq(self, column, value):
        self.filters.append({'op': 'eq', 'col': column, 'val': value})
        return self

    def or_(self, cond_str):
        self.filters.append({'op': 'or', 'val': cond_str})
        return self

    def limit(self, num):
        self.limit_val = num
        return self

    def ilike(self, column, pattern):
        self.filters.append({'op': 'ilike', 'col': column, 'val': pattern})
        return self

    def insert(self, data):
        new_record = {**data, "id": str(uuid.uuid4())}
        if self.table_name == "user_repositories":
            MOCK_REPOSITORIES_DB.append(new_record)
        else:
            MOCK_SNIPPETS_DB.append(new_record)
        self._inserted = new_record
        return self

    def update(self, data):
        self._is_update = True
        self._update_data = data
        dataset = MOCK_REPOSITORIES_DB if self.table_name == "user_repositories" else MOCK_SNIPPETS_DB
        for r in dataset:
            match = all(
                str(r.get(f['col'])) == str(f['val'])
                for f in self.filters if f['op'] == 'eq'
            )
            if match:
                r.update(data)
        return self

    def delete(self):
        self._is_delete = True
        dataset = MOCK_REPOSITORIES_DB if self.table_name == "user_repositories" else MOCK_SNIPPETS_DB
        keep = [
            r for r in dataset
            if not all(
                str(r.get(f['col'])) == str(f['val'])
                for f in self.filters if f['op'] == 'eq'
            )
        ]
        if self.table_name == "user_repositories":
            MOCK_REPOSITORIES_DB[:] = keep
        else:
            MOCK_SNIPPETS_DB[:] = keep
        return self

    def execute(self):
        res = MagicMock()

        # INSERT: return the newly created record
        if self._inserted is not None:
            res.data = [self._inserted]
            return res

        # UPDATE / DELETE: already applied in-place, return empty success
        if self._is_update or self._is_delete:
            res.data = []
            return res

        # SELECT
        dataset = MOCK_REPOSITORIES_DB if self.table_name == "user_repositories" else MOCK_SNIPPETS_DB
        results = list(dataset)
        for f in self.filters:
            if f['op'] == 'eq':
                results = [r for r in results if str(r.get(f['col'])) == str(f['val'])]
            elif f['op'] == 'ilike':
                clean_pat = f['val'].replace('%', '').lower()
                results = [r for r in results if clean_pat in str(r.get(f['col'], '')).lower()]
            elif f['op'] == 'or':
                parts = [p.split('.eq.') for p in f['val'].split(',')]
                matched = []
                for r in results:
                    for part in parts:
                        if len(part) == 2 and str(r.get(part[0])) == str(part[1]):
                            matched.append(r)
                            break
                results = matched
        if self.limit_val is not None:
            results = results[:self.limit_val]
        res.data = results
        return res


class MockSupabaseRPC:
    def __init__(self, rpc_name, params):
        self.rpc_name = rpc_name
        self.params = params

        # Verify RPC params pass valid UUID strings when supplied
        p_user = params.get("p_user_id")
        if p_user:
            uuid.UUID(str(p_user))

        p_repo = params.get("p_repository_id")
        if p_repo:
            uuid.UUID(str(p_repo))

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


class MockSupabaseAuth:
    def get_user(self, token):
        user_mock = MagicMock()
        user_mock.id = token
        res_mock = MagicMock()
        res_mock.user = user_mock
        return res_mock


class MockSupabaseDB:
    def __init__(self):
        self.auth = MockSupabaseAuth()

    def table(self, name):
        return MockSupabaseQuery(name)

    def rpc(self, name, params):
        return MockSupabaseRPC(name, params)


@patch("app.db", MockSupabaseDB())
@patch("app.get_embedding", return_value=[0.1] * 1536)
class TestRepositoryScopeIntegrity:

    def test_search_scoped_to_jarvis_never_returns_bus_snippets(self, mock_embed):
        response = client.post(
            "/search",
            headers={"Authorization": f"Bearer {USER_A_ID}"},
            json={
                "query": "What is this project about?",
                "user_id": USER_A_ID,
                "repository_id": JARVIS_REPO_ID,
                "repo_filter": "Jarvis-portfolio"
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["repository_id"] == JARVIS_REPO_ID
        assert data["index_version"] == "v1"
        assert len(data["sources"]) > 0
        for s in data["sources"]:
            assert s["repo"] == "Jarvis-portfolio"
            assert "bus-crowding" not in s["repo"]
            assert "tracker.py" not in s["file"]

    def test_graph_scoped_to_jarvis_contains_only_jarvis_nodes(self, mock_embed):
        response = client.get(
            f"/graph-data?user_id={USER_A_ID}&repository_id={JARVIS_REPO_ID}",
            headers={"Authorization": f"Bearer {USER_A_ID}"}
        )

        assert response.status_code == 200
        data = response.json()
        node_ids = [n["id"] for n in data["nodes"] if n["id"] != "ME"]
        assert "Jarvis-portfolio" in node_ids
        assert "bus-crowding" not in node_ids
        assert all("bus-crowding" not in nid for nid in node_ids)

    def test_all_projects_scope_contains_both_repositories(self, mock_embed):
        response = client.get(
            f"/graph-data?user_id={USER_A_ID}",
            headers={"Authorization": f"Bearer {USER_A_ID}"}
        )

        assert response.status_code == 200
        data = response.json()
        node_ids = [n["id"] for n in data["nodes"] if n["id"] != "ME"]
        assert "Jarvis-portfolio" in node_ids
        assert "bus-crowding" in node_ids

    def test_user_b_cannot_query_user_a_repository_id(self, mock_embed):
        response = client.post(
            "/search",
            headers={"Authorization": f"Bearer {USER_B_ID}"},
            json={
                "query": "Give me access",
                "user_id": USER_B_ID,
                "repository_id": JARVIS_REPO_ID  # User A's repo!
            }
        )

        assert response.status_code == 400
        assert "Invalid or unauthorized repository selection" in response.json()["detail"]

    def test_invalid_non_uuid_repository_id_returns_400(self, mock_embed):
        response = client.post(
            "/search",
            headers={"Authorization": f"Bearer {USER_A_ID}"},
            json={
                "query": "Show files",
                "user_id": USER_A_ID,
                "repository_id": "non-existent-repo-999"
            }
        )

        assert response.status_code == 400
        assert "Invalid or unauthorized repository selection" in response.json()["detail"]

    def test_user_repos_returns_authoritative_repositories_list(self, mock_embed):
        response = client.get(
            f"/user-repos?user_id={USER_A_ID}",
            headers={"Authorization": f"Bearer {USER_A_ID}"}
        )

        assert response.status_code == 200
        data = response.json()
        assert "repos" in data
        assert "repositories" in data
        # Known repos from user_repositories have real UUIDs
        repo_ids = [r["id"] for r in data["repositories"]]
        assert JARVIS_REPO_ID in repo_ids
        assert BUS_REPO_ID in repo_ids
        # All normal repos are not legacy
        normal_repos = [r for r in data["repositories"] if r["id"] in [JARVIS_REPO_ID, BUS_REPO_ID]]
        for r in normal_repos:
            assert r.get("legacy") is False

    def test_user_repos_is_strictly_read_only_no_writes(self, mock_embed):
        """GET /user-repos must perform zero inserts, updates, or deletes."""
        insert_calls = []
        update_calls = []
        delete_calls = []

        original_table = MockSupabaseDB.table

        def tracking_table(self_db, name):
            q = original_table(self_db, name)
            original_insert = q.insert
            original_update = q.update
            original_delete = q.delete

            def tracked_insert(data):
                insert_calls.append((name, data))
                return original_insert(data)

            def tracked_update(data):
                update_calls.append((name, data))
                return original_update(data)

            def tracked_delete():
                delete_calls.append(name)
                return original_delete()

            q.insert = tracked_insert
            q.update = tracked_update
            q.delete = tracked_delete
            return q

        with patch.object(MockSupabaseDB, "table", tracking_table):
            response = client.get(
                f"/user-repos?user_id={USER_A_ID}",
                headers={"Authorization": f"Bearer {USER_A_ID}"}
            )

        assert response.status_code == 200
        assert insert_calls == [], f"GET /user-repos performed unexpected inserts: {insert_calls}"
        assert update_calls == [], f"GET /user-repos performed unexpected updates: {update_calls}"
        assert delete_calls == [], f"GET /user-repos performed unexpected deletes: {delete_calls}"

    def test_legacy_snippet_only_repo_appears_with_null_id_and_legacy_flag(self, mock_embed):
        """IPL is snippet-only (no user_repositories row): must appear as legacy=True, id=None."""
        # Add IPL snippet that has no corresponding user_repositories row
        MOCK_SNIPPETS_DB.append({
            "id": "s_ipl_legacy",
            "user_id": USER_A_ID,
            "repository_id": None,
            "repo_name": "ipl",
            "file_path": "ipl.py",
            "code_content": "# ipl code",
            "index_version": "v1"
        })

        response = client.get(
            f"/user-repos?user_id={USER_A_ID}",
            headers={"Authorization": f"Bearer {USER_A_ID}"}
        )

        assert response.status_code == 200
        data = response.json()
        assert "ipl" in data["repos"]

        ipl_entry = next((r for r in data["repositories"] if r["repo_name"] == "ipl"), None)
        assert ipl_entry is not None, "ipl must appear in repositories list"
        assert ipl_entry["id"] is None, "Legacy repo must have id=None, not a fake string"
        assert ipl_entry["legacy"] is True, "Legacy repo must have legacy=True"

    def test_reconcile_legacy_creates_real_uuid_record_for_authenticated_user_only(self, mock_embed):
        """POST /repositories/reconcile-legacy creates real user_repositories rows for legacy repos."""
        # Ensure ipl snippet exists without a user_repositories row
        ipl_name_in_repos = any(r["repo_name"] == "ipl" for r in MOCK_REPOSITORIES_DB if r.get("user_id") == USER_A_ID)
        if not ipl_name_in_repos:
            # Only add the snippet if it doesn't exist yet
            if not any(s.get("repo_name") == "ipl" and s.get("user_id") == USER_A_ID for s in MOCK_SNIPPETS_DB):
                MOCK_SNIPPETS_DB.append({
                    "id": "s_ipl_reconcile",
                    "user_id": USER_A_ID,
                    "repository_id": None,
                    "repo_name": "ipl",
                    "file_path": "ipl.py",
                    "code_content": "# ipl",
                    "index_version": "v1"
                })

        initial_repo_count = len([r for r in MOCK_REPOSITORIES_DB if r.get("user_id") == USER_A_ID])

        response = client.post(
            "/repositories/reconcile-legacy",
            headers={"Authorization": f"Bearer {USER_A_ID}"}
        )

        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "ok"
        assert isinstance(result["reconciled"], list)

        # After reconciliation, ipl should now have a real UUID in user_repositories
        ipl_repo = next(
            (r for r in MOCK_REPOSITORIES_DB if r.get("repo_name") == "ipl" and r.get("user_id") == USER_A_ID),
            None
        )
        assert ipl_repo is not None, "Reconcile must create a user_repositories row for ipl"
        assert ipl_repo.get("id") is not None, "Reconciled repo must have a real UUID id"
        try:
            uuid.UUID(str(ipl_repo["id"]))
        except ValueError:
            assert False, f"Reconciled id must be a valid UUID, got: {ipl_repo['id']}"

        # User B's repos must NOT have been touched
        user_b_repos_after = [r for r in MOCK_REPOSITORIES_DB if r.get("user_id") == USER_B_ID]
        assert all(r.get("repo_name") == "user-b-private-repo" for r in user_b_repos_after)

    def test_scoped_search_rejects_legacy_null_id_as_repository_id(self, mock_embed):
        """A legacy repo has id=None; sending None/null as repository_id must return 400."""
        # Attempting to search with repository_id="None" (string) or an invalid ID that
        # doesn't match any user_repositories row must be rejected.
        response = client.post(
            "/search",
            headers={"Authorization": f"Bearer {USER_A_ID}"},
            json={
                "query": "ipl files",
                "user_id": USER_A_ID,
                "repository_id": "None"   # string "None" — must not be accepted as a real UUID
            }
        )
        assert response.status_code == 400
        assert "Invalid or unauthorized repository selection" in response.json()["detail"]

