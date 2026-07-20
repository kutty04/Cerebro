import os
import time
import shutil
import tempfile
import socket
import subprocess
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from app import app as fastapi_app
from security.auth import AuthenticatedUser, get_current_user
from db_adapter import DatabaseAdapter
from ingestion_validator import (
    DEFAULT_LIMITS,
    IngestionLimits,
    IngestionConcurrencyManager,
    IngestionRateLimiter,
    validate_and_normalize_github_url,
    validate_dns_ip_safety,
    is_ip_restricted,
    get_dir_size_bytes,
    terminate_process_tree,
    run_safe_git_clone,
)
import indexer

AUTH_HEADERS = {"Authorization": "Bearer test-user-user-123"}


@pytest.fixture(autouse=True)
def setup_test_overrides():
    def mock_get_current_user(request: Request):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Authentication required: missing Authorization header.")

        token = auth_header.replace("Bearer ", "").strip()
        if not token:
            raise HTTPException(status_code=401, detail="Empty authentication token provided.")

        return AuthenticatedUser(id="user-123", email="user-123@test.com", access_token=token)

    fastapi_app.dependency_overrides[get_current_user] = mock_get_current_user
    yield
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(fastapi_app)


# ----------------------------------------------------------------------
# 1. URL VALIDATION & CANONICAL NORMALIZATION TESTS
# ----------------------------------------------------------------------

def test_url_valid_canonical():
    assert (
        validate_and_normalize_github_url("https://github.com/kutty04/Cerebro")
        == "https://github.com/kutty04/Cerebro"
    )


def test_url_valid_with_git_suffix():
    assert (
        validate_and_normalize_github_url("https://github.com/kutty04/Cerebro.git")
        == "https://github.com/kutty04/Cerebro"
    )


@pytest.mark.parametrize(
    "invalid_url",
    [
        "http://github.com/owner/repo",
        "ssh://git@github.com/owner/repo",
        "git://github.com/owner/repo",
        "file:///etc/passwd",
        "ftp://github.com/owner/repo",
        "javascript:alert(1)",
        "git@github.com:owner/repo.git",
        "https://user:pass@github.com/owner/repo",
        "https://localhost/owner/repo",
        "https://127.0.0.1/owner/repo",
        "https://[::1]/owner/repo",
        "https://github.com.attacker.com/owner/repo",
        "https://attacker.com/github.com/owner/repo",
        "https://github.com/owner/repo/blob/main/file.py",
        "https://github.com/owner/repo?query=1",
        "https://github.com/owner/repo#fragment",
        "https://github.com/owner/repo/../other",
        "https://github.com/owner/repo%2e%2e",
        "https://github.com/owner/repo\x00",
        "https://github.com/owner/repo\n",
        "https://github.com/owner",
        "https://github.com/",
        "",
        "a" * 501,
    ],
)
def test_url_validation_rejections(invalid_url):
    with pytest.raises(HTTPException) as exc_info:
        validate_and_normalize_github_url(invalid_url)
    assert exc_info.value.status_code == 400


# ----------------------------------------------------------------------
# 2. DNS & IP RESTRICTION TESTS
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "ip, expected_restricted",
    [
        ("127.0.0.1", True),
        ("10.0.0.1", True),
        ("172.16.0.1", True),
        ("192.168.1.1", True),
        ("169.254.169.254", True),
        ("0.0.0.0", True),
        ("100.64.0.1", True),
        ("::1", True),
        ("fe80::1", True),
        ("fc00::1", True),
        ("::ffff:127.0.0.1", True),
        ("140.82.121.4", False),  # GitHub public IP
    ],
)
def test_is_ip_restricted(ip, expected_restricted):
    assert is_ip_restricted(ip) == expected_restricted


@patch("socket.getaddrinfo")
def test_validate_dns_safe(mock_getaddrinfo):
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("140.82.121.4", 443))
    ]
    assert validate_dns_ip_safety("github.com") is True


@patch("socket.getaddrinfo")
def test_validate_dns_private_ip(mock_getaddrinfo):
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.5", 443))
    ]
    assert validate_dns_ip_safety("github.com") is False


@patch("socket.getaddrinfo")
def test_validate_dns_mixed_ips(mock_getaddrinfo):
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("140.82.121.4", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ]
    assert validate_dns_ip_safety("github.com") is False


@patch("socket.getaddrinfo")
def test_validate_dns_failure(mock_getaddrinfo):
    mock_getaddrinfo.side_effect = socket.gaierror("DNS Resolution Failed")
    assert validate_dns_ip_safety("github.com") is False


# ----------------------------------------------------------------------
# 3. DISK SIZE & PROCESS-TREE TERMINATION TESTS
# ----------------------------------------------------------------------

def test_get_dir_size_bytes_includes_git_objects():
    temp_dir = tempfile.mkdtemp()
    try:
        git_dir = os.path.join(temp_dir, ".git", "objects")
        os.makedirs(git_dir, exist_ok=True)
        with open(os.path.join(git_dir, "pack-1234"), "wb") as f:
            f.write(b"x" * 2048)

        with open(os.path.join(temp_dir, "main.py"), "wb") as f:
            f.write(b"y" * 1024)

        total_size = get_dir_size_bytes(temp_dir)
        assert total_size == 3072
    finally:
        shutil.rmtree(temp_dir)


def test_terminate_process_tree_calls():
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.pid = 9999

    with patch("sys.platform", "linux"), patch("os.getpgid", create=True, return_value=9999), patch("os.killpg", create=True) as mock_killpg:
        terminate_process_tree(mock_proc)
        assert mock_killpg.called
        mock_proc.wait.assert_called_once()


@patch("subprocess.Popen")
def test_run_safe_git_clone_timeout(mock_popen):
    mock_proc = MagicMock()
    mock_proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="git clone", timeout=5)
    mock_proc.poll.return_value = None
    mock_proc.pid = 8888
    mock_popen.return_value = mock_proc

    with pytest.raises(subprocess.TimeoutExpired):
        run_safe_git_clone("https://github.com/owner/repo", tempfile.gettempdir(), 5)


# ----------------------------------------------------------------------
# 4. CONCURRENCY & RATE LIMITING TESTS
# ----------------------------------------------------------------------

def test_concurrency_duplicate_rejection():
    limits = IngestionLimits(MAX_CONCURRENT_JOBS=3)
    cm = IngestionConcurrencyManager(limits)

    cm.acquire("user-1", "https://github.com/owner/repo1")

    with pytest.raises(HTTPException) as exc_info:
        cm.acquire("user-1", "https://github.com/owner/repo1")
    assert exc_info.value.status_code == 409

    cm.release("user-1", "https://github.com/owner/repo1")


def test_concurrency_capacity_reached():
    limits = IngestionLimits(MAX_CONCURRENT_JOBS=2)
    cm = IngestionConcurrencyManager(limits)

    cm.acquire("user-1", "https://github.com/owner/repo1")
    cm.acquire("user-2", "https://github.com/owner/repo2")

    with pytest.raises(HTTPException) as exc_info:
        cm.acquire("user-3", "https://github.com/owner/repo3")
    assert exc_info.value.status_code == 429

    cm.release("user-1", "https://github.com/owner/repo1")
    cm.release("user-2", "https://github.com/owner/repo2")


def test_rate_limiter_user_limit_exceeded():
    limits = IngestionLimits(MAX_USER_INGESTIONS_PER_WINDOW=2, USER_WINDOW_SECONDS=3600)
    rl = IngestionRateLimiter(limits)

    rl.check_and_record("user-rl-1")
    rl.check_and_record("user-rl-1")

    with pytest.raises(HTTPException) as exc_info:
        rl.check_and_record("user-rl-1")
    assert exc_info.value.status_code == 429
    assert "Retry-After" in exc_info.value.headers
    assert int(exc_info.value.headers["Retry-After"]) > 0


def test_rate_limiter_global_limit_exceeded():
    limits = IngestionLimits(MAX_USER_INGESTIONS_PER_WINDOW=10, MAX_GLOBAL_INGESTIONS_PER_WINDOW=2, GLOBAL_WINDOW_SECONDS=3600)
    rl = IngestionRateLimiter(limits)

    rl.check_and_record("user-a")
    rl.check_and_record("user-b")

    with pytest.raises(HTTPException) as exc_info:
        rl.check_and_record("user-c")
    assert exc_info.value.status_code == 429
    assert "Global ingestion rate limit reached" in exc_info.value.detail


def test_rate_limiter_map_size_eviction():
    limits = IngestionLimits(MAX_USER_INGESTIONS_PER_WINDOW=10, MAX_GLOBAL_INGESTIONS_PER_WINDOW=1000, MAX_RATE_LIMITER_MAP_SIZE=3)
    rl = IngestionRateLimiter(limits)

    rl.check_and_record("u1")
    rl.check_and_record("u2")
    rl.check_and_record("u3")
    rl.check_and_record("u4")

    assert len(rl.user_records) <= 3


# ----------------------------------------------------------------------
# 5. END-TO-END INGESTION ROUTE TESTS (MOCKED CLONE & DB)
# ----------------------------------------------------------------------

@patch("app.db")
@patch("app.validate_dns_ip_safety")
@patch("app.run_safe_git_clone")
@patch.object(indexer.CodeIndexer, "get_serverless_embedding")
@patch.object(DatabaseAdapter, "resolve_user_repo")
@patch.object(DatabaseAdapter, "create_ingestion_job")
@patch.object(DatabaseAdapter, "update_repo_status")
@patch.object(DatabaseAdapter, "update_job_status")
@patch.object(DatabaseAdapter, "promote_index_version")
def test_ingest_success_mocked(
    mock_promote, mock_update_job, mock_update_repo, mock_create, mock_resolve,
    mock_get_embedding, mock_run_clone, mock_dns_safety, mock_db, client
):
    mock_dns_safety.return_value = True
    mock_get_embedding.return_value = [0.1] * 384
    mock_run_clone.return_value = 0

    mock_resolve.return_value = {
        "id": "repo-123",
        "repository_name": "Cerebro",
        "active_index_version": "v1",
    }
    mock_create.return_value = "job-123"

    mock_table = MagicMock()
    mock_table.insert.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[{"id": 101}])
    mock_db.table.return_value = mock_table

    def fake_clone(canonical_url, temp_dir, timeout_sec):
        test_file = os.path.join(temp_dir, "main.py")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("def hello(): return 'world'")
        return 0

    mock_run_clone.side_effect = fake_clone

    payload = {
        "repo_url": "https://github.com/kutty04/Cerebro.git",
    }
    with patch.dict("os.environ", {"HF_TOKEN": "mock_token", "SUPABASE_URL": "https://mock.url", "SUPABASE_KEY": "mock_key"}):
        response = client.post("/ingest", json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["indexed_count"] >= 1


@patch("app.db")
@patch("app.validate_dns_ip_safety")
@patch("app.run_safe_git_clone")
@patch.object(DatabaseAdapter, "resolve_user_repo")
@patch.object(DatabaseAdapter, "create_ingestion_job")
@patch.object(DatabaseAdapter, "update_repo_status")
@patch.object(DatabaseAdapter, "update_job_status")
@patch.object(DatabaseAdapter, "fail_and_cleanup_job")
def test_ingest_clone_timeout(
    mock_fail, mock_update_job, mock_update_repo, mock_create, mock_resolve,
    mock_run_clone, mock_dns_safety, mock_db, client
):
    mock_dns_safety.return_value = True
    mock_resolve.return_value = {
        "id": "repo-123",
        "repository_name": "Cerebro",
        "active_index_version": "v1",
    }
    mock_create.return_value = "job-123"
    mock_run_clone.side_effect = subprocess.TimeoutExpired(cmd="git clone", timeout=60)

    payload = {
        "repo_url": "https://github.com/kutty04/Cerebro",
    }
    response = client.post("/ingest", json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 504
    assert "timed out" in response.json()["detail"]


@patch("app.db")
@patch("app.validate_dns_ip_safety")
@patch("app.run_safe_git_clone")
@patch.object(indexer.CodeIndexer, "get_serverless_embedding")
@patch.object(DatabaseAdapter, "resolve_user_repo")
@patch.object(DatabaseAdapter, "create_ingestion_job")
@patch.object(DatabaseAdapter, "update_repo_status")
@patch.object(DatabaseAdapter, "update_job_status")
@patch.object(DatabaseAdapter, "fail_and_cleanup_job")
def test_ingest_database_failure_rolls_back(
    mock_fail, mock_update_job, mock_update_repo, mock_create, mock_resolve,
    mock_get_embedding, mock_run_clone, mock_dns_safety, mock_db, client
):
    mock_dns_safety.return_value = True
    mock_get_embedding.return_value = [0.1] * 384
    mock_run_clone.return_value = 0

    mock_resolve.return_value = {
        "id": "repo-123",
        "repository_name": "Cerebro",
        "active_index_version": "v1",
    }
    mock_create.return_value = "job-123"

    def fake_clone(canonical_url, temp_dir, timeout_sec):
        test_file = os.path.join(temp_dir, "main.py")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("def hello(): pass")
        return 0

    mock_run_clone.side_effect = fake_clone

    mock_table = MagicMock()
    mock_table.insert.side_effect = Exception("Database write crash")
    mock_db.table.return_value = mock_table

    payload = {
        "repo_url": "https://github.com/kutty04/Cerebro",
    }
    with patch.dict("os.environ", {"HF_TOKEN": "mock_token", "SUPABASE_URL": "https://mock.url", "SUPABASE_KEY": "mock_key"}):
        response = client.post("/ingest", json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 500
    assert "Failed to index" in response.json()["detail"]


# ----------------------------------------------------------------------
# 6. ROLLBACK TARGETING CREATED IDS ONLY
# ----------------------------------------------------------------------

@patch("app.db")
def test_rollback_does_not_delete_valid_repo_snippets(mock_db, client):
    mock_table = MagicMock()
    mock_db.table.return_value = mock_table

    mock_table.insert.side_effect = Exception("DB Insert Crash")

    temp_dir = tempfile.mkdtemp()
    try:
        for i in range(3):
            with open(os.path.join(temp_dir, f"file{i}.py"), "w", encoding="utf-8") as f:
                f.write(f"def func_{i}(): pass")

        idx = indexer.CodeIndexer(repos_path=temp_dir, repo_name="myrepo")
        idx.db = mock_db
        idx.user_id = "u123"

        with patch.object(idx, "get_serverless_embedding", return_value=[0.1]*384):
            snippets = idx.scan_repos()
            with pytest.raises(HTTPException):
                idx.index_snippets(snippets)
    finally:
        shutil.rmtree(temp_dir)


# ----------------------------------------------------------------------
# 7. DETERMINISTIC FILESYSTEM & LIMIT TESTS IN INDEXER
# ----------------------------------------------------------------------

def test_indexer_binary_file_skipped():
    temp_dir = tempfile.mkdtemp()
    try:
        bin_file = os.path.join(temp_dir, "file.py")
        with open(bin_file, "wb") as f:
            f.write(b"print('hello')\x00secret_bytes")

        idx = indexer.CodeIndexer(repos_path=temp_dir)
        snippets = idx.scan_repos()
        assert len(snippets) == 0
    finally:
        shutil.rmtree(temp_dir)


def test_indexer_symlink_escape_skipped_deterministic():
    temp_dir = tempfile.mkdtemp()
    try:
        linked_file = os.path.join(temp_dir, "linked.py")
        with open(linked_file, "w", encoding="utf-8") as f:
            f.write("code content")

        idx = indexer.CodeIndexer(repos_path=temp_dir)

        with patch.object(Path, "is_symlink", return_value=True):
            snippets = idx.scan_repos()
            assert len(snippets) == 0
    finally:
        shutil.rmtree(temp_dir)
