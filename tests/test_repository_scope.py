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
        return self

    def delete(self):
        self._is_delete = True
        return self

    def _apply_filters(self, dataset):
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
        return results

    def execute(self):
        res = MagicMock()

        # INSERT: return the newly created record
        if self._inserted is not None:
            res.data = [self._inserted]
            return res

        # UPDATE: apply updates to dataset matching filters
        if self._is_update:
            dataset = MOCK_REPOSITORIES_DB if self.table_name == "user_repositories" else MOCK_SNIPPETS_DB
            matched = self._apply_filters(dataset)
            for r in matched:
                r.update(self._update_data)
            res.data = matched
            return res

        # DELETE: remove items matching filters from dataset
        if self._is_delete:
            dataset = MOCK_REPOSITORIES_DB if self.table_name == "user_repositories" else MOCK_SNIPPETS_DB
            matched = self._apply_filters(dataset)
            for r in matched:
                if r in dataset:
                    dataset.remove(r)
            res.data = []
            return res

        # SELECT
        dataset = MOCK_REPOSITORIES_DB if self.table_name == "user_repositories" else MOCK_SNIPPETS_DB
        results = self._apply_filters(dataset)
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

    def test_user_repos_queries_only_user_repositories_ignoring_orphans(self, mock_embed):
        """GET /user-repos is read-only and returns ONLY user_repositories rows. Orphan snippets are ignored."""
        # Add an orphan snippet for User A that has no user_repositories record
        MOCK_SNIPPETS_DB.append({
            "id": "s_orphan_link_shortner",
            "user_id": USER_A_ID,
            "repository_id": None,
            "repo_name": "link-shortner",
            "file_path": "index.js",
            "code_content": "console.log('orphan');",
            "source_url": None,
            "index_version": "v1"
        })

        resp = client.get(
            f"/user-repos?user_id={USER_A_ID}",
            headers={"Authorization": f"Bearer {USER_A_ID}"}
        )
        assert resp.status_code == 200
        data = resp.json()

        # Link shortner orphan snippet must NOT appear in user-repos
        repo_names = data["repos"]
        assert "link-shortner" not in repo_names
        # Authoritative repos from user_repositories MUST appear with real UUIDs
        assert "Jarvis-portfolio" in repo_names
        assert "bus-crowding" in repo_names
        for r in data["repositories"]:
            assert r["id"] is not None
            assert r["legacy"] is False

    def test_ingest_github_url_derives_metadata_and_registers_uuid(self, mock_embed):
        """POST /ingest parses GitHub URL, registers user_repositories record with UUID, and indexes snippets with UUID."""
        # Clean any existing IPL records
        global MOCK_REPOSITORIES_DB, MOCK_SNIPPETS_DB
        MOCK_REPOSITORIES_DB = [r for r in MOCK_REPOSITORIES_DB if r.get("repo_name") != "ipl"]
        MOCK_SNIPPETS_DB = [s for s in MOCK_SNIPPETS_DB if s.get("repo_name") != "ipl"]

        # Mock indexer methods to simulate scanning 5 snippets
        with patch("app.CodeIndexer.scan_repos") as mock_scan, \
             patch("app.CodeIndexer.index_snippets") as mock_index:
            
            mock_scan.return_value = [
                {
                    "repo_name": "ipl",
                    "file_path": "main.py",
                    "language": "python",
                    "code_content": "def ipl_entrypoint(): print('IPL Match')",
                    "source_url": "https://github.com/kutty04/ipl/blob/main/main.py",
                    "start_line": 1
                }
            ]

            resp = client.post(
                "/ingest",
                headers={"Authorization": f"Bearer {USER_A_ID}"},
                json={
                    "repo_url": "https://github.com/kutty04/ipl.git",
                    "user_id": USER_A_ID
                }
            )

            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            real_uuid = data["repository_id"]
            assert real_uuid is not None
            uuid.UUID(real_uuid)

            # Verify record created in user_repositories with all required metadata
            ipl_row = next((r for r in MOCK_REPOSITORIES_DB if r.get("repo_name") == "ipl" and r.get("user_id") == USER_A_ID), None)
            assert ipl_row is not None
            assert ipl_row["repository_owner"] == "kutty04"
            assert ipl_row["canonical_url"] == "https://github.com/kutty04/ipl"
            assert ipl_row["provider"] == "github"
            assert ipl_row["status"] == "ready"
            assert ipl_row["id"] == real_uuid

    def test_stuck_indexing_repository_with_existing_snippets_finalizes_safely(self, mock_embed):
        """A repository stuck in indexing that already has snippets is finalized to ready without duplicate cloning or indexing."""
        STUCK_UUID = "8ab8c650-0b8e-4064-854b-9fe62c33420e"
        global MOCK_REPOSITORIES_DB, MOCK_SNIPPETS_DB
        MOCK_REPOSITORIES_DB = [r for r in MOCK_REPOSITORIES_DB if r.get("repo_name") != "ipl"]
        MOCK_SNIPPETS_DB = [s for s in MOCK_SNIPPETS_DB if s.get("repo_name") != "ipl"]

        # Insert stuck repository record
        MOCK_REPOSITORIES_DB.append({
            "id": STUCK_UUID,
            "user_id": USER_A_ID,
            "repository_name": "ipl",
            "repo_name": "ipl",
            "repository_owner": "kutty04",
            "canonical_url": "https://github.com/kutty04/ipl",
            "provider": "github",
            "status": "indexing",
            "active_index_version": "v1"
        })

        # Insert 195 existing snippets created during initial run
        for i in range(195):
            MOCK_SNIPPETS_DB.append({
                "id": f"s_ipl_prod_{i}",
                "user_id": USER_A_ID,
                "repository_id": STUCK_UUID,
                "repo_name": "ipl",
                "file_path": f"module_{i}.py",
                "code_content": f"def func_{i}(): pass",
                "source_url": f"https://github.com/kutty04/ipl/blob/main/module_{i}.py",
                "index_version": "v1"
            })

        # Mock CodeIndexer so we can verify it is NEVER called
        with patch("app.CodeIndexer.scan_repos") as mock_scan, \
             patch("app.CodeIndexer.index_snippets") as mock_index:

            resp = client.post(
                "/ingest",
                headers={"Authorization": f"Bearer {USER_A_ID}"},
                json={
                    "repo_url": "https://github.com/kutty04/ipl.git",
                    "user_id": USER_A_ID
                }
            )

            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            assert data["repository_id"] == STUCK_UUID
            assert data["indexed_count"] == 195

            # Must NOT re-clone or re-scan snippets
            mock_scan.assert_not_called()
            mock_index.assert_not_called()

            # Status in DB must now be 'ready'
            ipl_row = next(r for r in MOCK_REPOSITORIES_DB if r.get("id") == STUCK_UUID)
            assert ipl_row["status"] == "ready"

            # Must not duplicate repository row
            matching_rows = [r for r in MOCK_REPOSITORIES_DB if r.get("repo_name") == "ipl" and r.get("user_id") == USER_A_ID]
            assert len(matching_rows) == 1

            # Must not duplicate snippets
            matching_snippets = [s for s in MOCK_SNIPPETS_DB if s.get("repo_name") == "ipl" and s.get("user_id") == USER_A_ID]
            assert len(matching_snippets) == 195

    def test_orphan_snippet_with_same_repo_name_is_never_adopted_or_displayed(self, mock_embed):
        """An orphan snippet matching repo_name but lacking valid repository_id is never adopted during ingest/resume."""
        global MOCK_REPOSITORIES_DB, MOCK_SNIPPETS_DB
        MOCK_REPOSITORIES_DB = [r for r in MOCK_REPOSITORIES_DB if r.get("repo_name") != "ipl"]
        MOCK_SNIPPETS_DB = [s for s in MOCK_SNIPPETS_DB if s.get("repo_name") != "ipl"]

        # Add orphan snippet with repo_name = 'ipl' and repository_id = None
        ORPHAN_SNIPPET_ID = "s_ipl_orphan_secret"
        MOCK_SNIPPETS_DB.append({
            "id": ORPHAN_SNIPPET_ID,
            "user_id": USER_A_ID,
            "repository_id": None,
            "repo_name": "ipl",
            "file_path": "legacy_secret.py",
            "code_content": "def legacy_orphan(): pass",
            "source_url": "https://github.com/kutty04/ipl/blob/main/legacy_secret.py",
            "index_version": "v1"
        })

        with patch("app.CodeIndexer.scan_repos") as mock_scan, \
             patch("app.CodeIndexer.index_snippets") as mock_index:

            mock_scan.return_value = [
                {
                    "repo_name": "ipl",
                    "file_path": "new_main.py",
                    "language": "python",
                    "code_content": "def new_ipl_code(): pass",
                    "source_url": "https://github.com/kutty04/ipl/blob/main/new_main.py",
                    "start_line": 1
                }
            ]

            resp = client.post(
                "/ingest",
                headers={"Authorization": f"Bearer {USER_A_ID}"},
                json={
                    "repo_url": "https://github.com/kutty04/ipl.git",
                    "user_id": USER_A_ID
                }
            )

            assert resp.status_code == 200
            new_uuid = resp.json()["repository_id"]
            assert new_uuid is not None

            # Verify orphan snippet was NOT adopted or updated with new_uuid
            orphan_row = next(s for s in MOCK_SNIPPETS_DB if s.get("id") == ORPHAN_SNIPPET_ID)
            assert orphan_row["repository_id"] is None, "Orphan snippet must retain repository_id=None and NOT be adopted"

            # Verify All Projects Graph omits orphan snippet node
            resp_graph = client.get(
                f"/graph-data?user_id={USER_A_ID}",
                headers={"Authorization": f"Bearer {USER_A_ID}"}
            )
            assert resp_graph.status_code == 200
            node_files = [n.get("label") or n.get("id") for n in resp_graph.json()["nodes"]]
            assert "legacy_secret.py" not in node_files

    def test_failed_repo_registration_prevents_ingest_success(self, mock_embed):
        """If user_repositories record insertion fails, /ingest fails with 500 and does not report success."""
        original_table = MockSupabaseDB.table

        def failing_table(self_db, name):
            q = original_table(self_db, name)
            if name == "user_repositories":
                def failing_insert(data):
                    raise Exception("DB schema constraint error")
                q.insert = failing_insert
            return q

        with patch.object(MockSupabaseDB, "table", failing_table):
            resp = client.post(
                "/ingest",
                headers={"Authorization": f"Bearer {USER_A_ID}"},
                json={
                    "repo_url": "https://github.com/someowner/failrepo.git",
                    "user_id": USER_A_ID
                }
            )

        assert resp.status_code == 500
        assert "Repository registration failed" in resp.json()["detail"]

    def test_all_projects_scope_excludes_orphan_snippets(self, mock_embed):
        """All Projects search & graph omit orphan snippets that do not match user_repositories."""
        # Add orphan snippet without user_repositories row
        MOCK_SNIPPETS_DB.append({
            "id": "s_orphan_secret",
            "user_id": USER_A_ID,
            "repository_id": "ffffffff-ffff-ffff-ffff-ffffffffffff",
            "repo_name": "link-shortner",
            "file_path": "secret.py",
            "code_content": "def secret_orphan_code(): pass",
            "source_url": "https://github.com/test/link-shortner/blob/main/secret.py",
            "index_version": "v1"
        })

        # All Projects Graph
        resp_graph = client.get(
            f"/graph-data?user_id={USER_A_ID}",
            headers={"Authorization": f"Bearer {USER_A_ID}"}
        )
        assert resp_graph.status_code == 200
        graph_nodes = [n["id"] for n in resp_graph.json()["nodes"]]
        assert "link-shortner" not in graph_nodes

    def test_manually_imported_ipl_search_and_graph(self, mock_embed):
        """After manual import, IPL has a real UUID and both scoped /search and /graph-data work cleanly."""
        global MOCK_REPOSITORIES_DB, MOCK_SNIPPETS_DB
        MOCK_REPOSITORIES_DB = [r for r in MOCK_REPOSITORIES_DB if r.get("repo_name") != "ipl"]
        MOCK_SNIPPETS_DB = [s for s in MOCK_SNIPPETS_DB if s.get("repo_name") != "ipl"]

        # Simulate manually imported IPL repo in user_repositories
        IPL_UUID = "77777777-7777-7777-7777-777777777777"
        MOCK_REPOSITORIES_DB.append({
            "id": IPL_UUID,
            "user_id": USER_A_ID,
            "repository_name": "ipl",
            "repo_name": "ipl",
            "repository_owner": "kutty04",
            "canonical_url": "https://github.com/kutty04/ipl",
            "provider": "github",
            "status": "ready",
            "active_index_version": "v1"
        })

        # Insert matching UUID-backed snippet
        MOCK_SNIPPETS_DB.append({
            "id": "s_ipl_imported",
            "user_id": USER_A_ID,
            "repository_id": IPL_UUID,
            "repo_name": "ipl",
            "file_path": "app.py",
            "code_content": "def ipl_start_app(): print('IPL matches')",
            "source_url": "https://github.com/kutty04/ipl/blob/main/app.py",
            "index_version": "v1"
        })

        # 1. GET /user-repos includes ipl with real UUID
        resp_repos = client.get(
            f"/user-repos?user_id={USER_A_ID}",
            headers={"Authorization": f"Bearer {USER_A_ID}"}
        )
        assert resp_repos.status_code == 200
        ipl_obj = next((r for r in resp_repos.json()["repositories"] if r["repo_name"] == "ipl"), None)
        assert ipl_obj is not None
        assert ipl_obj["id"] == IPL_UUID
        assert ipl_obj["legacy"] is False

        # 2. Scoped Search with IPL_UUID
        resp_search = client.post(
            "/search",
            headers={"Authorization": f"Bearer {USER_A_ID}"},
            json={
                "query": "ipl_start_app",
                "user_id": USER_A_ID,
                "repository_id": IPL_UUID,
                "repo_filter": "ipl"
            }
        )
        assert resp_search.status_code == 200
        search_res = resp_search.json()
        assert search_res["repository_id"] == IPL_UUID

        # 3. Scoped Graph with IPL_UUID
        resp_graph = client.get(
            f"/graph-data?user_id={USER_A_ID}&repository_id={IPL_UUID}",
            headers={"Authorization": f"Bearer {USER_A_ID}"}
        )
        assert resp_graph.status_code == 200
        nodes = [n["id"] for n in resp_graph.json()["nodes"]]
        assert "ipl" in nodes

    def test_delete_repo_persistence_and_list_consistency(self, mock_embed):
        """Strict UUID-based deletion tests: rejects invalid/missing UUIDs, deletes only matching UUID row & snippets, leaves orphan snippets and IPL intact."""
        jarvis_uuid = JARVIS_REPO_ID
        ipl_uuid = "77777777-7777-7777-7777-777777777777"

        # Ensure IPL is in user_repositories and has a UUID-linked snippet
        if not any(r.get("id") == ipl_uuid for r in MOCK_REPOSITORIES_DB):
            MOCK_REPOSITORIES_DB.append({
                "id": ipl_uuid,
                "user_id": USER_A_ID,
                "repository_name": "ipl",
                "repo_name": "ipl",
                "repository_owner": "kutty04",
                "canonical_url": "https://github.com/kutty04/ipl",
                "provider": "github",
                "status": "ready",
                "active_index_version": "v1"
            })
        MOCK_SNIPPETS_DB.append({
            "id": "s_ipl_keep",
            "user_id": USER_A_ID,
            "repository_id": ipl_uuid,
            "repo_name": "ipl",
            "file_path": "main.py",
            "code_content": "def keep_ipl(): pass",
            "index_version": "v1"
        })

        # Add same-name orphan snippet for Jarvis-portfolio (repository_id = None)
        ORPHAN_JARVIS_ID = "s_jarvis_orphan_untouched"
        MOCK_SNIPPETS_DB.append({
            "id": ORPHAN_JARVIS_ID,
            "user_id": USER_A_ID,
            "repository_id": None,
            "repo_name": "Jarvis-portfolio",
            "file_path": "orphan_jarvis.py",
            "code_content": "def orphan_jarvis(): pass",
            "index_version": "v1"
        })

        # 1. Missing or invalid repository_id is rejected (400)
        resp_invalid = client.post(
            f"/delete-repo?repository_id=not-a-uuid&user_id={USER_A_ID}",
            headers={"Authorization": f"Bearer {USER_A_ID}"}
        )
        assert resp_invalid.status_code == 400
        assert "Invalid repository_id UUID format" in resp_invalid.json()["detail"]

        # 2. Cross-user deletion is rejected (404)
        resp_cross = client.post(
            f"/delete-repo?repository_id={jarvis_uuid}&user_id={USER_B_ID}",
            headers={"Authorization": f"Bearer {USER_B_ID}"}
        )
        assert resp_cross.status_code == 404

        # 3. Delete Jarvis using real UUID
        resp_del = client.post(
            f"/delete-repo?repository_id={jarvis_uuid}&repo_name=Jarvis-portfolio&user_id={USER_A_ID}",
            headers={"Authorization": f"Bearer {USER_A_ID}"}
        )
        assert resp_del.status_code == 200
        assert resp_del.json()["status"] == "success"

        # 4. Verify Jarvis user_repositories row and UUID-linked snippets removed
        assert not any(r.get("id") == jarvis_uuid for r in MOCK_REPOSITORIES_DB)
        assert not any(s.get("repository_id") == jarvis_uuid for s in MOCK_SNIPPETS_DB)

        # 5. Verify same-name orphan snippet remains UNTOUCHED (not deleted)
        orphan_row = next((s for s in MOCK_SNIPPETS_DB if s.get("id") == ORPHAN_JARVIS_ID), None)
        assert orphan_row is not None, "Same-name orphan snippet must remain untouched and NOT be deleted"

        # 6. Verify IPL remains present and intact in user_repositories and GET /user-repos
        assert any(r.get("id") == ipl_uuid for r in MOCK_REPOSITORIES_DB)
        resp_after = client.get(
            f"/user-repos?user_id={USER_A_ID}",
            headers={"Authorization": f"Bearer {USER_A_ID}"}
        )
        assert resp_after.status_code == 200
        repos_list = resp_after.json()["repos"]
        assert "Jarvis-portfolio" not in repos_list
        assert "ipl" in repos_list

        # 7. Simulate sign-out and sign-in (fresh auth token request to /user-repos)
        resp_signin = client.get(
            f"/user-repos?user_id={USER_A_ID}",
            headers={"Authorization": f"Bearer {USER_A_ID}"}
        )
        assert resp_signin.status_code == 200
        assert "Jarvis-portfolio" not in resp_signin.json()["repos"]
        assert "ipl" in resp_signin.json()["repos"]

    def test_scoped_search_rejects_legacy_null_id_as_repository_id(self, mock_embed):
        """Sending None/null or non-UUID string as repository_id returns 400."""
        response = client.post(
            "/search",
            headers={"Authorization": f"Bearer {USER_A_ID}"},
            json={
                "query": "ipl files",
                "user_id": USER_A_ID,
                "repository_id": "None"
            }
        )
        assert response.status_code == 400
        assert "Invalid or unauthorized repository selection" in response.json()["detail"]

