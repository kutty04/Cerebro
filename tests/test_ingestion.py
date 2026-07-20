import os
import shutil
import tempfile
import socket
import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import app as fastapi_app
from ingestion_validator import (
    DEFAULT_LIMITS,
    IngestionLimits,
    IngestionConcurrencyManager,
    validate_and_normalize_github_url,
    validate_dns_ip_safety,
    is_ip_restricted,
)
import indexer


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
# 3. CONCURRENCY & CAPACITY TESTS
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


# ----------------------------------------------------------------------
# 4. END-TO-END INGESTION ROUTE TESTS (MOCKED CLONE & DB)
# ----------------------------------------------------------------------

@patch("app.db")
@patch("app.validate_dns_ip_safety")
@patch("subprocess.run")
@patch.object(indexer.CodeIndexer, "get_serverless_embedding")
def test_ingest_success_mocked(
    mock_get_embedding, mock_subproc_run, mock_dns_safety, mock_db, client
):
    mock_dns_safety.return_value = True
    mock_get_embedding.return_value = [0.1] * 384

    mock_table = MagicMock()
    mock_table.insert.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[{"id": 101}])
    mock_db.table.return_value = mock_table

    def fake_clone(cmd, **kwargs):
        temp_dir_path = cmd[-1]
        test_file = os.path.join(temp_dir_path, "main.py")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("def hello(): return 'world'")
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_subproc_run.side_effect = fake_clone

    payload = {
        "repo_url": "https://github.com/kutty04/Cerebro.git",
        "user_id": "user-test-123",
    }
    with patch.dict("os.environ", {"HF_TOKEN": "mock_token", "SUPABASE_URL": "https://mock.url", "SUPABASE_KEY": "mock_key"}):
        response = client.post("/ingest", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["indexed_count"] >= 1


@patch("app.db")
@patch("app.validate_dns_ip_safety")
@patch("subprocess.run")
def test_ingest_clone_timeout(mock_subproc_run, mock_dns_safety, mock_db, client):
    mock_dns_safety.return_value = True
    import subprocess
    mock_subproc_run.side_effect = subprocess.TimeoutExpired(cmd="git clone", timeout=60)

    payload = {
        "repo_url": "https://github.com/kutty04/Cerebro",
        "user_id": "user-test-123",
    }
    response = client.post("/ingest", json=payload)
    assert response.status_code == 504
    assert "timed out" in response.json()["detail"]


@patch("app.db")
@patch("app.validate_dns_ip_safety")
@patch("subprocess.run")
@patch.object(indexer.CodeIndexer, "get_serverless_embedding")
def test_ingest_database_failure_rolls_back(
    mock_get_embedding, mock_subproc_run, mock_dns_safety, mock_db, client
):
    mock_dns_safety.return_value = True
    mock_get_embedding.return_value = [0.1] * 384

    def fake_clone(cmd, **kwargs):
        temp_dir_path = cmd[-1]
        test_file = os.path.join(temp_dir_path, "main.py")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("def hello(): pass")
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_subproc_run.side_effect = fake_clone

    mock_table = MagicMock()
    mock_table.insert.side_effect = Exception("Database write crash")
    mock_db.table.return_value = mock_table

    payload = {
        "repo_url": "https://github.com/kutty04/Cerebro",
        "user_id": "user-test-rollback",
    }
    with patch.dict("os.environ", {"HF_TOKEN": "mock_token", "SUPABASE_URL": "https://mock.url", "SUPABASE_KEY": "mock_key"}):
        response = client.post("/ingest", json=payload)
        assert response.status_code == 500
        assert "Failed to index" in response.json()["detail"]


# ----------------------------------------------------------------------
# 5. FILESYSTEM & LIMIT TESTS IN INDEXER
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


def test_indexer_symlink_escape_skipped():
    temp_dir = tempfile.mkdtemp()
    outside_dir = tempfile.mkdtemp()
    try:
        outside_file = os.path.join(outside_dir, "outside.py")
        with open(outside_file, "w", encoding="utf-8") as f:
            f.write("secret = 'outside'")

        link_file = os.path.join(temp_dir, "linked.py")
        try:
            os.symlink(outside_file, link_file)
        except OSError:
            pytest.skip("Symlinks not supported without admin on this OS")

        idx = indexer.CodeIndexer(repos_path=temp_dir)
        snippets = idx.scan_repos()
        assert len(snippets) == 0
    finally:
        shutil.rmtree(temp_dir)
        shutil.rmtree(outside_dir)
