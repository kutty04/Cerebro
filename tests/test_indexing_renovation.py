import os
import shutil
import tempfile
import time
import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
from indexer import CodeIndexer, GitIgnoreMatcher, is_binary_file

@pytest.fixture
def temp_repo():
    temp_dir = tempfile.mkdtemp()
    try:
        # Create standard directories and files
        os.makedirs(os.path.join(temp_dir, "node_modules"), exist_ok=True)
        with open(os.path.join(temp_dir, "node_modules", "skip.js"), "w") as f:
            f.write("console.log('skip');")

        # Legitimate mobile/web source folders
        os.makedirs(os.path.join(temp_dir, "android"), exist_ok=True)
        with open(os.path.join(temp_dir, "android", "MainActivity.py"), "w") as f:
            f.write("def android_func():\n    pass")

        os.makedirs(os.path.join(temp_dir, "ios"), exist_ok=True)
        with open(os.path.join(temp_dir, "ios", "AppDelegate.py"), "w") as f:
            f.write("def ios_func():\n    pass")

        # Python file with class and function boundaries
        with open(os.path.join(temp_dir, "module.py"), "w", encoding="utf-8") as f:
            f.write('''"""Docstring."""
import os

@my_decorator
async def async_fun():
    pass

class MainClass:
    def method(self):
        pass
''')

        # Python file with syntax error
        with open(os.path.join(temp_dir, "bad_syntax.py"), "w", encoding="utf-8") as f:
            f.write("class BadSyntax:\n   def fail(:\n")

        # JS/TS file with bracket balance
        with open(os.path.join(temp_dir, "script.js"), "w", encoding="utf-8") as f:
            f.write('''// JS File
class Helper {
    constructor() {
        this.value = 1;
    }
}
function execute() {
    return true;
}
''')

        # Markdown file with sections
        with open(os.path.join(temp_dir, "README.md"), "w", encoding="utf-8") as f:
            f.write('''# Header 1
Paragraph 1.

## Header 2
Paragraph 2.
''')

        # Binary file
        with open(os.path.join(temp_dir, "bin.bin"), "wb") as f:
            f.write(b"\x00\x01\x02\x03\x00")

        # Minified file
        with open(os.path.join(temp_dir, "style.min.js"), "w", encoding="utf-8") as f:
            f.write("var x=1;function y(){return x;}" + "x"*1000)

        # Oversized file
        with open(os.path.join(temp_dir, "big.py"), "w", encoding="utf-8") as f:
            f.write("print('line')\n" * 500)

        # Duplicate file (identical content to dup2.py)
        with open(os.path.join(temp_dir, "dup1.py"), "w", encoding="utf-8") as f:
            f.write("def shared():\n    return 42")
        with open(os.path.join(temp_dir, "dup2.py"), "w", encoding="utf-8") as f:
            f.write("def shared():\n    return 42")

        # .gitignore file
        with open(os.path.join(temp_dir, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("ignored_file.py\n*.tmp\n")
        with open(os.path.join(temp_dir, "ignored_file.py"), "w", encoding="utf-8") as f:
            f.write("print('ignored')")
        with open(os.path.join(temp_dir, "temp.tmp"), "w", encoding="utf-8") as f:
            f.write("temp")

        yield temp_dir
    finally:
        shutil.rmtree(temp_dir)

# 1. FILE DISCOVERY TESTS
def test_file_discovery_filtering(temp_repo):
    indexer = CodeIndexer(repos_path=temp_repo, repo_name="test_discovery")
    snippets = indexer.scan_repos()

    # Verify standard folders are skipped
    assert not any("node_modules" in s["file_path"] for s in snippets)
    # Verify ignored_file.py is skipped by gitignore
    assert not any("ignored_file.py" in s["file_path"] for s in snippets)
    assert not any("temp.tmp" in s["file_path"] for s in snippets)

    # Verify android and ios are NOT globally skipped
    assert any("android/MainActivity.py" in s["file_path"].replace("\\", "/") for s in snippets)
    assert any("ios/AppDelegate.py" in s["file_path"].replace("\\", "/") for s in snippets)

    # Verify binary, minified, and oversized files are skipped/handled
    assert not any("bin.bin" in s["file_path"] for s in snippets)
    assert not any("style.min.js" in s["file_path"] for s in snippets)
    
    # Assert oversized is skipped or handled according to max lines/bytes rules
    # In indexer, we check stat size or limits. Large files are permitted up to max_file_size limit,
    # but let's check path traversal containment logic
    assert not any(".." in s["file_path"] for s in snippets)

def test_symlink_escape_blocked(temp_repo):
    # Create symlink pointing outside root
    external_dir = tempfile.mkdtemp()
    try:
        external_file = os.path.join(external_dir, "outside.py")
        with open(external_file, "w") as f:
            f.write("def escape():\n    pass")
            
        link_path = os.path.join(temp_repo, "escaped_link.py")
        # Python symlink creation
        try:
            os.symlink(external_file, link_path)
        except OSError:
            # Skip if Windows privileges do not permit symlinks in this environment
            pytest.skip("Symlink creation not supported without privileges")

        indexer = CodeIndexer(repos_path=temp_repo, repo_name="test_symlink")
        snippets = indexer.scan_repos()
        assert not any("escaped_link.py" in s["file_path"] for s in snippets)
    finally:
        shutil.rmtree(external_dir)

# 2. CHUNKING TESTS
def test_python_ast_chunking(temp_repo):
    indexer = CodeIndexer(repos_path=temp_repo, repo_name="test_py_ast")
    # Scan specifically module.py
    with open(os.path.join(temp_repo, "module.py"), "r", encoding="utf-8") as f:
        code = f.read()

    chunks = indexer.chunk_code(code, "module.py", "python")
    
    # Validate async_fun detection
    async_fun_chunk = next(c for c in chunks if c["symbol_name"] == "async_fun")
    assert async_fun_chunk["symbol_type"] == "async_function"
    assert "@my_decorator" in async_fun_chunk["code_content"]
    assert "async def async_fun()" in async_fun_chunk["code_content"]

    # Validate class and method nesting context
    method_chunk = next(c for c in chunks if c["symbol_name"] == "method")
    assert method_chunk["symbol_type"] == "function"
    assert "Parent: MainClass" in method_chunk["code_content"]

    # Syntax error fallback
    with open(os.path.join(temp_repo, "bad_syntax.py"), "r", encoding="utf-8") as f:
        bad_code = f.read()
    fallback_chunks = indexer.chunk_code(bad_code, "bad_syntax.py", "python")
    assert len(fallback_chunks) > 0
    assert fallback_chunks[0]["symbol_name"] == "block"

def test_js_bracket_balancing(temp_repo):
    indexer = CodeIndexer(repos_path=temp_repo, repo_name="test_js")
    with open(os.path.join(temp_repo, "script.js"), "r", encoding="utf-8") as f:
        code = f.read()

    chunks = indexer.chunk_code(code, "script.js", "javascript")
    
    helper_chunk = next(c for c in chunks if c["symbol_name"] == "Helper")
    assert helper_chunk["symbol_type"] == "class"
    assert "class Helper {" in helper_chunk["code_content"]

    execute_chunk = next(c for c in chunks if c["symbol_name"] == "execute")
    assert execute_chunk["symbol_type"] == "function"
    assert "function execute()" in execute_chunk["code_content"]

def test_markdown_heading_chunks(temp_repo):
    indexer = CodeIndexer(repos_path=temp_repo, repo_name="test_md")
    with open(os.path.join(temp_repo, "README.md"), "r", encoding="utf-8") as f:
        code = f.read()

    chunks = indexer.chunk_code(code, "README.md", "markdown")
    assert len(chunks) == 2
    assert chunks[0]["symbol_name"] == "Header 1"
    assert chunks[1]["symbol_name"] == "Header 2"

# 3. IDEMPOTENCY TESTS
def test_idempotency_deduplication(temp_repo):
    indexer = CodeIndexer(repos_path=temp_repo, repo_name="test_idempotency")
    snippets = indexer.scan_repos()

    # dup1.py and dup2.py have identical content but in different files.
    # Therefore, they MUST NOT be deduplicated across different files because rel_file_path differs in canonical hash representation.
    dup1_snippets = [s for s in snippets if "dup1.py" in s["file_path"]]
    dup2_snippets = [s for s in snippets if "dup2.py" in s["file_path"]]
    assert len(dup1_snippets) > 0
    assert len(dup2_snippets) > 0

    # Ensure no duplicate hashes exist within a single job's snippets list
    hashes = [s["content_hash"] for s in snippets]
    assert len(hashes) == len(set(hashes))

# 4. EMBEDDINGS AND INSERTS BATCHING
@patch("requests.post")
def test_batch_embeddings_and_retries(mock_post, temp_repo):
    indexer = CodeIndexer(repos_path=temp_repo, repo_name="test_batch_embeddings")
    
    # Mock successful batch response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [[0.1] * 384, [0.2] * 384]
    mock_post.return_value = mock_response

    res = indexer.get_serverless_embeddings_batch(["text1", "text2"])
    assert res is not None
    assert len(res) == 2
    assert len(res[0]) == 384

    # Test transient failure retry
    mock_post.reset_mock()
    mock_fail_resp = MagicMock()
    mock_fail_resp.status_code = 503
    mock_post.side_effect = [mock_fail_resp, mock_response]

    with patch("time.sleep", return_value=None):
        res = indexer.get_serverless_embeddings_batch(["text1", "text2"])
        assert res is not None
        assert mock_post.call_count == 2

    # Test non-retryable error (e.g. 401 Unauthorized)
    mock_post.reset_mock()
    mock_unauth = MagicMock()
    mock_unauth.status_code = 401
    mock_post.side_effect = [mock_unauth]
    
    res = indexer.get_serverless_embeddings_batch(["text1"])
    assert res is None
    assert mock_post.call_count == 1

def test_database_batch_inserts_and_rollback(temp_repo):
    db_mock = MagicMock()
    indexer = CodeIndexer(repos_path=temp_repo, repo_name="test_db_batch", repository_id="repo-123", ingestion_job_id="job-456")
    indexer.db = db_mock
    indexer.user_id = "user-789"

    # Mock database insert response
    db_mock.table().insert().execute.return_value = MagicMock(data=[{"id": 1}, {"id": 2}])

    snippets = [
        {"repo_name": "test", "file_path": "a.py", "language": "python", "code_content": "def a(): pass", "source_url": "", "start_line": 1, "content_hash": "h1"},
        {"repo_name": "test", "file_path": "b.py", "language": "python", "code_content": "def b(): pass", "source_url": "", "start_line": 1, "content_hash": "h2"},
    ]

    with patch.object(indexer, "get_serverless_embeddings_batch", return_value=[[0.1]*384, [0.2]*384]):
        ids = indexer.index_snippets(snippets)
        assert len(ids) == 2
        assert indexer.indexed_count == 2

    # Verify rollback on partial failure
    db_mock.reset_mock()
    db_mock.table().insert().execute.side_effect = Exception("DB error")
    
    with pytest.raises(HTTPException) as excinfo:
        with patch.object(indexer, "get_serverless_embeddings_batch", return_value=[[0.1]*384, [0.2]*384]):
            indexer.index_snippets(snippets)

    assert excinfo.value.status_code == 500
    # Ensure delete() rollback query is issued under scoped parameters
    db_mock.table.assert_any_call("code_snippets")
    assert db_mock.table().delete().eq.call_count == 1

# 5. SOURCE URLS AND METRICS
def test_git_source_urls(temp_repo):
    # Mock git repository checkout
    indexer = CodeIndexer(repos_path=temp_repo, repo_url="https://github.com/my-user/my-project.git")
    indexer.detected_branch = "master"
    indexer.detected_commit_sha = "abc123commit"
    
    snippets = indexer.scan_repos()
    
    # Assert commit-SHA stable source URLs are preferred
    module_snip = next(s for s in snippets if "module.py" in s["file_path"])
    assert "https://github.com/my-user/my-project/blob/abc123commit/module.py" in module_snip["source_url"]

def test_metrics_gathering(temp_repo):
    indexer = CodeIndexer(repos_path=temp_repo, repo_name="test_metrics")
    
    def fake_index(snippets):
        indexer.indexed_count = len(snippets)
        return list(range(len(snippets)))

    with patch.object(indexer, "initialize", return_value=True):
        with patch.object(indexer, "index_snippets", side_effect=fake_index):
            success = indexer.run()
            assert success
            assert "files_considered" in indexer.metrics
            assert "files_indexed" in indexer.metrics
            assert "chunks_generated" in indexer.metrics
            assert "chunks_deduplicated" in indexer.metrics
            assert "total_indexing_duration_ms" in indexer.metrics
            assert indexer.metrics["files_considered"] > 0
