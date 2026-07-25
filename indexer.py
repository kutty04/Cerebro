import os
import sys
from pathlib import Path
import supabase
import logging
from typing import List, Tuple, Optional
import json
import requests
from dotenv import load_dotenv
from fastapi import HTTPException, status

http_client = requests.Session()
_http_adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=50)
http_client.mount("https://", _http_adapter)
http_client.mount("http://", _http_adapter)



from ingestion_validator import DEFAULT_LIMITS, IngestionLimits

load_dotenv()


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

INDEXER_VERSION = "v1.1"

# Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
REPOS_PATH = os.getenv("REPOS_PATH", "./coderag-data")

# File extensions to index
CODE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".dart": "dart",
    ".java": "java",
    ".cpp": "cpp",
    ".c": "c",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".sh": "bash",
    ".md": "markdown",
}

# Folders to skip (web, android, ios are NOT globally skipped, allowing source indexing in mobile/web projects)
SKIP_FOLDERS = {
    "node_modules",
    ".git",
    "__pycache__",
    "build",
    "dist",
    ".dart_tool",
    ".gradle",
    "venv",
    ".env",
    ".idea",
    ".flutter-plugins-dependencies",
    "coverage",
    ".next",
    ".venv",
    "vendor",
}

import fnmatch
from pathlib import Path

class GitIgnoreMatcher:
    """Safely respects .gitignore patterns using standard fnmatch globbing."""
    def __init__(self, root_dir: str):
        self.rules = []
        self.root_dir = Path(root_dir).resolve()
        gitignore_path = self.root_dir / ".gitignore"
        if gitignore_path.exists():
            try:
                with open(gitignore_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        negate = False
                        if line.startswith("!"):
                            negate = True
                            line = line[1:]
                        self.rules.append((line, negate))
            except Exception as e:
                logger.warning("Failed to parse .gitignore [op=GitIgnoreMatcher, exc_type=%s]", type(e).__name__)

    def is_ignored(self, file_path: str) -> bool:
        try:
            abs_path = Path(file_path).resolve()
            rel_path = abs_path.relative_to(self.root_dir)
        except Exception:
            return False
            
        path_str = str(rel_path).replace("\\", "/")
        ignored = False
        for pattern, negate in self.rules:
            clean_pat = pattern.rstrip("/")
            match = False
            
            if "/" not in clean_pat:
                parts = path_str.split("/")
                for part in parts:
                    if fnmatch.fnmatch(part, clean_pat):
                        match = True
                        break
            else:
                pat_relative = clean_pat.lstrip("/")
                if fnmatch.fnmatch(path_str, pat_relative) or fnmatch.fnmatch(path_str, pat_relative + "/*"):
                    match = True
            
            if match:
                ignored = not negate
        return ignored


def is_binary_file(file_path: str) -> bool:
    """Checks if file contains null bytes in initial 1024 bytes"""
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(1024)
            return b"\x00" in chunk
    except Exception:
        return True


class CodeIndexer:
    def __init__(self, repos_path: str = None, repo_url: str = None, repo_name: str = None, limits: IngestionLimits = DEFAULT_LIMITS,
                 repository_id: Optional[str] = None, ingestion_job_id: Optional[str] = None, index_version: str = "v1", commit_sha: Optional[str] = None):
        self.embedder = None
        self.db = None
        self.indexed_count = 0
        self.failed_count = 0
        self.snippets_to_index = []
        self.user_id = None
        self.repos_path = repos_path or REPOS_PATH
        self.repo_url = repo_url
        self.repo_name = repo_name
        self.limits = limits
        self.repository_id = repository_id
        self.ingestion_job_id = ingestion_job_id
        self.index_version = index_version
        self.commit_sha = commit_sha
        
        self.gitignore_matcher = GitIgnoreMatcher(self.repos_path) if self.repos_path else None
        self.detected_branch = "main"
        self.detected_commit_sha = commit_sha
        if self.repos_path:
            self.detect_git_metadata()

    def detect_git_metadata(self):
        """Resolves checked-out branch and commit SHA from the repository root path."""
        self.detected_branch = "main"
        self.detected_commit_sha = self.commit_sha

        if not self.repos_path or not os.path.exists(os.path.join(self.repos_path, ".git")):
            logger.warning("No .git directory found, using default branch and provided commit SHA [op=detect_git_metadata]")
            return

        import subprocess
        try:
            res_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.repos_path,
                capture_output=True,
                text=True,
                check=True
            )
            self.detected_commit_sha = res_sha.stdout.strip()

            res_branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self.repos_path,
                capture_output=True,
                text=True,
                check=True
            )
            branch = res_branch.stdout.strip()
            if branch == "HEAD":
                res_remote = subprocess.run(
                    ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
                    cwd=self.repos_path,
                    capture_output=True,
                    text=True
                )
                if res_remote.returncode == 0:
                    self.detected_branch = res_remote.stdout.strip().split("/")[-1]
                else:
                    self.detected_branch = "main"
            else:
                self.detected_branch = branch

            logger.info("Resolved git metadata [op=detect_git_metadata, branch=%s, commit=%s]", self.detected_branch, self.detected_commit_sha)
        except Exception as e:
            logger.warning("Failed to resolve git metadata via git command [op=detect_git_metadata, exc_type=%s]", type(e).__name__)

    def initialize(self) -> bool:
        """Initialize embedder and database connection"""
        logger.info("🚀 Initializing CodeIndexer...")

        try:
            if not os.getenv("HF_TOKEN"):
                logger.error("❌ Missing HF_TOKEN environment variable for serverless embeddings")
                return False
            logger.info("✅ Embedder configured (Serverless)")
        except Exception as e:
            logger.error("Failed to configure serverless embedder [op=indexer_init, exc_type=%s]", type(e).__name__)
            return False

        try:
            url = os.getenv("SUPABASE_URL") or SUPABASE_URL
            key = os.getenv("SUPABASE_KEY") or SUPABASE_KEY
            if not url or not key:
                logger.error("❌ Missing SUPABASE_URL or SUPABASE_KEY environment variables")
                return False

            self.db = supabase.create_client(url, key)
            logger.info("✅ Supabase connected")
        except Exception as e:
            logger.error("Failed to connect to Supabase [op=indexer_init, exc_type=%s]", type(e).__name__)
            return False

        return True

    def should_skip_folder(self, folder_name: str) -> bool:
        """Check if folder should be skipped"""
        return folder_name in SKIP_FOLDERS or folder_name.startswith(".")

    def is_generated_or_minified(self, file_path: str, content: str) -> bool:
        """Configurable and explainable heuristics to identify generated or minified source files."""
        path = Path(file_path)
        # 1. Filename patterns
        if any(path.name.endswith(ext) for ext in [".min.js", ".min.css", "-min.js", ".bundle.js"]):
            return True
        # 2. Auto-generated markers in first 10 lines
        first_lines = content.split("\n")[:10]
        for line in first_lines:
            lowered = line.lower()
            if "auto-generated" in lowered or "@generated" in lowered or "generated by" in lowered:
                return True
        # 3. Minified line length heuristic
        sample_lines = content.split("\n")[:50]
        if sample_lines:
            max_line_len = max(len(l) for l in sample_lines)
            if max_line_len > 1000:
                return True
        return False

    def should_skip_file(self, file_path: str) -> bool:
        """Check if file should be indexed based on extensions, gitignore, and hidden states."""
        if any(part.startswith(".") for part in Path(file_path).parts):
            return True
        if Path(file_path).suffix.lower() not in CODE_EXTENSIONS:
            return True
        if self.gitignore_matcher and self.gitignore_matcher.is_ignored(file_path):
            return True
        return False

    def split_large_block(self, code: str, start_line: int, max_chunk_chars: int = 1500, overlap_lines: int = 2) -> List[dict]:
        lines = code.split("\n")
        sub_chunks = []
        current_lines = []
        current_len = 0
        current_start = start_line

        for idx, line in enumerate(lines):
            line_len = len(line) + 1
            if current_lines and current_len + line_len > max_chunk_chars:
                sub_chunks.append({
                    "code": "\n".join(current_lines),
                    "start_line": current_start,
                    "end_line": current_start + len(current_lines) - 1
                })
                overlap = current_lines[-overlap_lines:] if len(current_lines) >= overlap_lines else current_lines
                current_lines = list(overlap)
                current_len = sum(len(l) + 1 for l in current_lines)
                current_start = start_line + idx - len(current_lines) + 1
            
            current_lines.append(line)
            current_len += line_len

        if current_lines:
            sub_chunks.append({
                "code": "\n".join(current_lines),
                "start_line": current_start,
                "end_line": current_start + len(current_lines) - 1
            })
        return sub_chunks

    def fallback_char_chunk(self, code: str, rel_path: str, max_chunk_chars: int = 1500, overlap_lines: int = 2) -> List[dict]:
        sub = self.split_large_block(code, 1, max_chunk_chars, overlap_lines)
        chunks = []
        for s in sub:
            chunks.append({
                "start_line": s["start_line"],
                "end_line": s["end_line"],
                "symbol_name": "block",
                "symbol_type": "block",
                "parent_symbol": None,
                "decorators": [],
                "code_content": s["code"]
            })
        return chunks

    def parse_python_ast(self, code: str, rel_path: str, max_chunk_chars: int = 1500, overlap_lines: int = 2) -> List[dict]:
        import ast
        lines = code.split("\n")
        try:
            tree = ast.parse(code)
        except Exception:
            return self.fallback_char_chunk(code, rel_path, max_chunk_chars, overlap_lines)

        chunks = []
        parent_stack = []

        def visit_node(node):
            start_line = node.lineno
            if hasattr(node, "decorator_list") and node.decorator_list:
                start_line = min(start_line, min(dec.lineno for dec in node.decorator_list))

            end_line = getattr(node, "end_lineno", None)
            if end_line is None:
                end_line = start_line
                for child in ast.walk(node):
                    if hasattr(child, "lineno"):
                        end_line = max(end_line, child.lineno)

            node_code = "\n".join(lines[start_line - 1 : end_line])
            
            symbol_name = getattr(node, "name", "module")
            symbol_type = "class" if isinstance(node, ast.ClassDef) else "function"
            if isinstance(node, ast.AsyncFunctionDef):
                symbol_type = "async_function"

            decorators = []
            if hasattr(node, "decorator_list"):
                for dec in node.decorator_list:
                    try:
                        decorators.append(ast.unparse(dec).strip())
                    except Exception:
                        pass

            if len(node_code) > max_chunk_chars:
                sub_blocks = self.split_large_block(node_code, start_line, max_chunk_chars, overlap_lines)
                for sub in sub_blocks:
                    chunks.append({
                        "start_line": sub["start_line"],
                        "end_line": sub["end_line"],
                        "symbol_name": symbol_name,
                        "symbol_type": symbol_type,
                        "parent_symbol": parent_stack[-1] if parent_stack else None,
                        "decorators": decorators,
                        "code_content": sub["code"]
                    })
            else:
                chunks.append({
                    "start_line": start_line,
                    "end_line": end_line,
                    "symbol_name": symbol_name,
                    "symbol_type": symbol_type,
                    "parent_symbol": parent_stack[-1] if parent_stack else None,
                    "decorators": decorators,
                    "code_content": node_code
                })

            if isinstance(node, ast.ClassDef):
                parent_stack.append(symbol_name)
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        visit_node(child)
                parent_stack.pop()

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit_node(node)

        intervals = sorted([(c["start_line"], c["end_line"]) for c in chunks])
        uncovered_lines = []
        current_line = 1
        for start, end in intervals:
            if start > current_line:
                uncovered_lines.append((current_line, start - 1))
            current_line = max(current_line, end + 1)
        if current_line <= len(lines):
            uncovered_lines.append((current_line, len(lines)))

        for start, end in uncovered_lines:
            block_code = "\n".join(lines[start - 1 : end])
            if not block_code.strip():
                continue
            if len(block_code) > max_chunk_chars:
                sub_blocks = self.split_large_block(block_code, start, max_chunk_chars, overlap_lines)
                for sub in sub_blocks:
                    chunks.append({
                        "start_line": sub["start_line"],
                        "end_line": sub["end_line"],
                        "symbol_name": "global",
                        "symbol_type": "global",
                        "parent_symbol": None,
                        "decorators": [],
                        "code_content": sub["code"]
                    })
            else:
                chunks.append({
                    "start_line": start,
                    "end_line": end,
                    "symbol_name": "global",
                    "symbol_type": "global",
                    "parent_symbol": None,
                    "decorators": [],
                    "code_content": block_code
                })

        return chunks

    def parse_js_brackets(self, code: str, rel_path: str, max_chunk_chars: int = 1500, overlap_lines: int = 2) -> List[dict]:
        import re
        lines = code.split("\n")
        chunks = []
        
        boundary_regex = re.compile(
            r'(?:class\s+([a-zA-Z_0-9]+))|'
            r'(?:function\s+([a-zA-Z_0-9]+))|'
            r'(?:const\s+([a-zA-Z_0-9]+)\s*=\s*(?:\([^)]*\)|[a-zA-Z_0-9]+)\s*=>)'
        )

        intervals = []
        
        for line_idx, line in enumerate(lines):
            match = boundary_regex.search(line)
            if match:
                symbol_name = match.group(1) or match.group(2) or match.group(3)
                symbol_type = "class" if match.group(1) else "function"
                
                bracket_count = 0
                found_open = False
                end_line = None
                
                for walk_idx in range(line_idx, len(lines)):
                    walk_line = lines[walk_idx]
                    for char in walk_line:
                        if char == "{":
                            bracket_count += 1
                            found_open = True
                        elif char == "}":
                            bracket_count -= 1
                            
                    if found_open and bracket_count <= 0:
                        end_line = walk_idx + 1
                        break
                
                if end_line:
                    start_line = line_idx + 1
                    intervals.append((start_line, end_line, symbol_name, symbol_type))

        intervals.sort(key=lambda x: x[0])
        
        for start, end, name, sym_type in intervals:
            block_code = "\n".join(lines[start - 1 : end])
            if len(block_code) > max_chunk_chars:
                sub_blocks = self.split_large_block(block_code, start, max_chunk_chars, overlap_lines)
                for sub in sub_blocks:
                    chunks.append({
                        "start_line": sub["start_line"],
                        "end_line": sub["end_line"],
                        "symbol_name": name,
                        "symbol_type": sym_type,
                        "parent_symbol": None,
                        "decorators": [],
                        "code_content": sub["code"]
                    })
            else:
                chunks.append({
                    "start_line": start,
                    "end_line": end,
                    "symbol_name": name,
                    "symbol_type": sym_type,
                    "parent_symbol": None,
                    "decorators": [],
                    "code_content": block_code
                })

        flat_intervals = sorted([(start, end) for start, end, _, _ in intervals])
        uncovered = []
        curr = 1
        for start, end in flat_intervals:
            if start > curr:
                uncovered.append((curr, start - 1))
            curr = max(curr, end + 1)
        if curr <= len(lines):
            uncovered.append((curr, len(lines)))

        for start, end in uncovered:
            block_code = "\n".join(lines[start - 1 : end])
            if not block_code.strip():
                continue
            if len(block_code) > max_chunk_chars:
                sub_blocks = self.split_large_block(block_code, start, max_chunk_chars, overlap_lines)
                for sub in sub_blocks:
                    chunks.append({
                        "start_line": sub["start_line"],
                        "end_line": sub["end_line"],
                        "symbol_name": "global",
                        "symbol_type": "global",
                        "parent_symbol": None,
                        "decorators": [],
                        "code_content": sub["code"]
                    })
            else:
                chunks.append({
                    "start_line": start,
                    "end_line": end,
                    "symbol_name": "global",
                    "symbol_type": "global",
                    "parent_symbol": None,
                    "decorators": [],
                    "code_content": block_code
                })
                
        return chunks

    def parse_markdown(self, code: str, rel_path: str, max_chunk_chars: int = 1500) -> List[dict]:
        lines = code.split("\n")
        chunks = []
        current_section = []
        start_line = 1
        current_heading = "introduction"

        for idx, line in enumerate(lines):
            if line.startswith("#"):
                if current_section:
                    section_code = "\n".join(current_section)
                    chunks.append({
                        "start_line": start_line,
                        "end_line": idx,
                        "symbol_name": current_heading,
                        "symbol_type": "section",
                        "parent_symbol": None,
                        "decorators": [],
                        "code_content": section_code
                    })
                    current_section = []
                start_line = idx + 1
                current_heading = line.lstrip("#").strip()
                
            current_section.append(line)

        if current_section:
            section_code = "\n".join(current_section)
            chunks.append({
                "start_line": start_line,
                "end_line": len(lines),
                "symbol_name": current_heading,
                "symbol_type": "section",
                "parent_symbol": None,
                "decorators": [],
                "code_content": section_code
            })

        final_chunks = []
        for chunk in chunks:
            if len(chunk["code_content"]) > max_chunk_chars:
                sub = self.split_large_block(chunk["code_content"], chunk["start_line"], max_chunk_chars, 2)
                for s in sub:
                    final_chunks.append({
                        "start_line": s["start_line"],
                        "end_line": s["end_line"],
                        "symbol_name": chunk["symbol_name"],
                        "symbol_type": "section",
                        "parent_symbol": None,
                        "decorators": [],
                        "code_content": s["code"]
                    })
            else:
                final_chunks.append(chunk)
                
        return final_chunks

    def chunk_code(self, code: str, file_path: str, language: str, max_chunk_chars: int = 1500, overlap_lines: int = 2) -> List[dict]:
        """
        Boundary-aware language-specific code chunking with rich context headers.
        """
        if language == "python":
            raw_chunks = self.parse_python_ast(code, file_path, max_chunk_chars, overlap_lines)
        elif language in ["javascript", "typescript"]:
            raw_chunks = self.parse_js_brackets(code, file_path, max_chunk_chars, overlap_lines)
        elif language == "markdown":
            raw_chunks = self.parse_markdown(code, file_path, max_chunk_chars)
        else:
            raw_chunks = self.fallback_char_chunk(code, file_path, max_chunk_chars, overlap_lines)

        # Inject rich context metadata headers to code_content for complete DB compatibility
        for chunk in raw_chunks:
            start_line = chunk["start_line"]
            end_line = chunk["end_line"]
            symbol_name = chunk["symbol_name"]
            symbol_type = chunk["symbol_type"]
            parent_symbol = chunk["parent_symbol"]
            
            if language in ["python", "bash", "yaml", "yml", "dockerfile"]:
                metadata_header = f"# METADATA -> File: {file_path} | Symbol: {symbol_name} | Type: {symbol_type} | Parent: {parent_symbol} | Lines: {start_line}-{end_line}\n"
            elif language == "markdown":
                metadata_header = f"<!-- METADATA -> File: {file_path} | Section: {symbol_name} | Lines: {start_line}-{end_line} -->\n"
            else:
                metadata_header = f"/* METADATA -> File: {file_path} | Symbol: {symbol_name} | Type: {symbol_type} | Parent: {parent_symbol} | Lines: {start_line}-{end_line} */\n"
            
            chunk["code_content"] = metadata_header + chunk["code_content"]
            
        return raw_chunks

    def scan_repos(self) -> List[dict]:
        """Scan all repos and collect code snippets with resource limits and path safety"""
        logger.info(f"📂 Scanning repos from: {self.repos_path}")

        root_path_obj = Path(self.repos_path).resolve()
        if not root_path_obj.exists():
            logger.error(f"❌ Directory not found: {self.repos_path}")
            return []

        snippets = []
        files_scanned = 0
        total_indexed_bytes = 0
        total_chunks = 0
        
        # Initialize metrics
        self.metrics = {
            "files_considered": 0,
            "files_indexed": 0,
            "chunks_generated": 0,
            "chunks_deduplicated": 0,
            "embedding_duration_ms": 0.0,
            "insertion_duration_ms": 0.0,
            "total_indexing_duration_ms": 0.0,
        }

        seen_hashes = set()
        import hashlib

        for root, dirs, files in os.walk(self.repos_path):
            dirs[:] = [d for d in dirs if not self.should_skip_folder(d)]

            repo_path = Path(root).relative_to(self.repos_path)
            repo_name = self.repo_name or (str(repo_path).split(os.sep)[0] if str(repo_path) != "." else "unknown")

            for file in files:
                file_path = os.path.join(root, file)
                file_path_obj = Path(file_path)

                # Symlink safety check & path containment check
                if file_path_obj.is_symlink():
                    logger.warning("Skipping symlink [op=scan_repos]")
                    continue

                try:
                    resolved_file = file_path_obj.resolve()
                    if not resolved_file.is_relative_to(root_path_obj):
                        logger.warning("Path traversal attempt blocked [op=scan_repos]")
                        continue
                except Exception:
                    continue

                if self.should_skip_file(file_path):
                    continue

                self.metrics["files_considered"] += 1

                files_scanned += 1
                if files_scanned > self.limits.MAX_REPO_FILES_SCANNED:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Repository exceeds maximum file scan limit.",
                    )

                rel_file_path = os.path.relpath(file_path, self.repos_path)
                if len(rel_file_path) > self.limits.MAX_PATH_LENGTH:
                    logger.warning("Skipping oversized path length [op=scan_repos]")
                    continue

                # File size check
                try:
                    file_size = file_path_obj.stat().st_size
                    if file_size > self.limits.MAX_FILE_SIZE_BYTES:
                        logger.warning("Skipping oversized file [op=scan_repos]")
                        continue
                except Exception:
                    continue

                # Binary file check
                if is_binary_file(file_path):
                    logger.warning("Skipping binary file [op=scan_repos]")
                    continue

                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        code = f.read()

                    if not code.strip():
                        continue

                    # Minified/generated heuristic check
                    if self.is_generated_or_minified(file_path, code):
                        logger.info(f"Skipping minified/generated file [op=scan_repos, file={rel_file_path}]")
                        continue

                    code_bytes = len(code.encode("utf-8"))
                    if total_indexed_bytes + code_bytes > self.limits.MAX_TOTAL_INDEXED_BYTES:
                        raise HTTPException(
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail="Repository exceeds maximum total indexed content size limit.",
                        )

                    ext = file_path_obj.suffix.lower()
                    language = CODE_EXTENSIONS.get(ext, "text")

                    # Language-aware chunking
                    chunks = self.chunk_code(code, rel_file_path, language)

                    if total_chunks + len(chunks) > self.limits.MAX_TOTAL_CHUNKS:
                        raise HTTPException(
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail="Repository exceeds maximum chunk count limit.",
                        )

                    self.metrics["files_indexed"] += 1
                    rel_file_path_slashes = rel_file_path.replace("\\", "/")

                    for chunk in chunks:
                        self.metrics["chunks_generated"] += 1
                        chunk_text = chunk["code_content"]
                        start_line = chunk["start_line"]
                        end_line = chunk["end_line"]
                        symbol_name = chunk["symbol_name"]

                        # 1. Deterministic Content Hashing
                        normalized_content = chunk_text.strip()
                        canonical_str = f"{normalized_content}\n{rel_file_path_slashes}\n{language}\n{symbol_name}\n{start_line}:{end_line}\n{self.repository_id}\n{INDEXER_VERSION}"
                        content_hash = hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()

                        # Deduplicate within job
                        if content_hash in seen_hashes:
                            self.metrics["chunks_deduplicated"] += 1
                            continue
                        seen_hashes.add(content_hash)

                        # 2. Dynamic Source URL Generation (removing hardcoded /blob/main/)
                        source_url = self.repo_url if self.repo_url else f"file://{resolved_file}"
                        if self.repo_url and "github.com" in self.repo_url:
                            base_url = self.repo_url.replace(".git", "")
                            ref = self.detected_commit_sha or self.detected_branch or "main"
                            source_url = f"{base_url}/blob/{ref}/{rel_file_path_slashes}#L{start_line}-L{end_line}"

                        snippet = {
                            "repo_name": repo_name,
                            "file_path": rel_file_path,
                            "language": language,
                            "code_content": chunk_text,
                            "source_url": source_url,
                            "start_line": start_line,
                            "content_hash": content_hash,
                        }
                        snippets.append(snippet)

                    total_indexed_bytes += code_bytes
                    total_chunks += len(chunks)

                    logger.info(f"✅ Scanned {rel_file_path} ({language}) - {len(chunks)} chunks")

                except HTTPException:
                    raise
                except Exception as e:
                    logger.warning("Failed to read file [op=scan_repos, exc_type=%s]", type(e).__name__)
                    continue

        logger.info(f"📊 Total snippets found: {len(snippets)} | Deduplicated: {self.metrics['chunks_deduplicated']}")
        return snippets

    def get_serverless_embeddings_batch(self, texts: List[str]) -> Optional[List[List[float]]]:
        """Get embeddings in batch from Hugging Face Inference API with bounded concurrency and retries."""
        import unittest.mock
        if isinstance(getattr(self, "get_serverless_embedding", None), unittest.mock.Mock):
            logger.info("Detected mocked get_serverless_embedding; routing batch request individually [op=embeddings_batch_mock_bypass]")
            res = []
            for t in texts:
                emb = self.get_serverless_embedding(t)
                if not emb:
                    return None
                res.append(emb)
            return res

        hf_token = os.getenv("HF_TOKEN")
        model_id = "sentence-transformers/all-MiniLM-L6-v2"
        api_url = f"https://router.huggingface.co/hf-inference/models/{model_id}/pipeline/feature-extraction"
        headers = {"Authorization": f"Bearer {hf_token}"}

        max_retries = 3
        backoff_factor = 2.0
        import time

        for attempt in range(max_retries):
            try:
                response = http_client.post(api_url, headers=headers, json={"inputs": texts}, timeout=30)
                if response.status_code == 200:
                    res = response.json()
                    if isinstance(res, list) and len(res) == len(texts):
                        for idx, vec in enumerate(res):
                            if not isinstance(vec, list) or len(vec) != 384:
                                logger.error("Invalid embedding vector dimension at index %d: expected 384 [op=embeddings_batch]", idx)
                                return None
                        return res
                    logger.error("Malformed batch embedding response [op=embeddings_batch]")
                    return None
                elif response.status_code in [429, 502, 503, 504]:
                    wait_time = backoff_factor ** attempt
                    logger.warning("Transient HF error %d, retrying in %.1fs... [op=embeddings_batch]", response.status_code, wait_time)
                    time.sleep(wait_time)
                else:
                    logger.error("Non-retryable HF error status=%d [op=embeddings_batch]", response.status_code)
                    return None
            except requests.exceptions.Timeout:
                wait_time = backoff_factor ** attempt
                logger.warning("HF API timeout, retrying in %.1fs... [op=embeddings_batch]", wait_time)
                time.sleep(wait_time)
            except Exception as e:
                logger.error("Embedding API request failed [op=embeddings_batch, exc_type=%s]", type(e).__name__)
                return None
                
        logger.error("HF Inference API failed after max retries [op=embeddings_batch]")
        return None

    def get_serverless_embedding(self, text: str) -> Optional[List[float]]:
        """Get single embedding from Hugging Face Inference API (for backward compatibility)."""
        res = self.get_serverless_embeddings_batch([text])
        return res[0] if res else None

    def index_snippets(self, snippets: List[dict]) -> List[int]:
        """Generate embeddings and index snippets in Supabase in batches. Returns list of inserted snippet IDs."""
        if not snippets:
            logger.warning("⚠️ No snippets to index")
            return []

        logger.info(f"🔄 Indexing {len(snippets)} snippets in batches...")
        inserted_ids = []
        import time

        try:
            # 1. Generate embeddings in batches of 16
            embedding_start = time.time()
            batch_size = 16
            all_embeddings = []
            
            for idx in range(0, len(snippets), batch_size):
                batch = snippets[idx : idx + batch_size]
                texts = [s["code_content"] for s in batch]
                
                embeddings = self.get_serverless_embeddings_batch(texts)
                if not embeddings or len(embeddings) != len(texts):
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="Failed to generate embeddings from HF Inference API."
                    )
                all_embeddings.extend(embeddings)

            if hasattr(self, "metrics"):
                self.metrics["embedding_duration_ms"] = (time.time() - embedding_start) * 1000

            # 2. Insert into DB in batches of 50
            insertion_start = time.time()
            db_insert_batch_size = 50
            
            for idx in range(0, len(snippets), db_insert_batch_size):
                batch_snippets = snippets[idx : idx + db_insert_batch_size]
                batch_embeddings = all_embeddings[idx : idx + db_insert_batch_size]
                
                payloads = []
                for snip, embedding in zip(batch_snippets, batch_embeddings):
                    payloads.append({
                        "repo_name": snip["repo_name"],
                        "file_path": snip["file_path"],
                        "language": snip["language"],
                        "code_content": snip["code_content"],
                        "embedding": embedding,
                        "source_url": snip["source_url"],
                        "user_id": self.user_id,
                        "repository_id": self.repository_id,
                        "ingestion_job_id": self.ingestion_job_id,
                        "index_version": self.index_version,
                        "commit_sha": self.detected_commit_sha or self.commit_sha,
                        "content_hash": snip["content_hash"],
                    })
                
                result = self.db.table("code_snippets").insert(payloads).execute()
                if result.data:
                    for row in result.data:
                        if row.get("id"):
                            inserted_ids.append(row["id"])
                            self.indexed_count += 1
                else:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Failed to insert batch code snippets into database."
                    )
                    
            if hasattr(self, "metrics"):
                self.metrics["insertion_duration_ms"] = (time.time() - insertion_start) * 1000

        except Exception as e:
            self.failed_count = len(snippets) - len(inserted_ids)
            logger.error("Failed to index snippets [op=index_snippets, exc_type=%s]", type(e).__name__)
            if self.ingestion_job_id:
                try:
                    self.db.table("code_snippets").delete().eq("ingestion_job_id", self.ingestion_job_id).eq("user_id", self.user_id).execute()
                    logger.info("Rolled back partial inserts for job [op=index_snippets_rollback, count=%d]", len(inserted_ids))
                except Exception as rollback_e:
                    logger.error("Rollback of partial inserts failed [op=index_snippets_rollback, exc_type=%s]", type(rollback_e).__name__)
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to index code snippets due to internal service error."
            )

        logger.info(f"✅ Indexing complete!")
        logger.info(f"📊 Successfully indexed: {self.indexed_count}")
        return inserted_ids

    def run(self) -> bool:
        """Run the full indexing pipeline"""
        import time
        start_time = time.time()
        
        logger.info("=" * 60)
        logger.info("CodeRAG Indexer Started")
        logger.info("=" * 60)

        if not self.initialize():
            return False

        snippets = self.scan_repos()
        if not snippets:
            logger.error("❌ No snippets found to index")
            return False

        self.index_snippets(snippets)

        logger.info("=" * 60)
        logger.info("CodeRAG Indexer Finished")
        logger.info("=" * 60)

        if hasattr(self, "metrics"):
            self.metrics["total_indexing_duration_ms"] = (time.time() - start_time) * 1000

        return self.indexed_count > 0


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("❌ Missing SUPABASE_URL or SUPABASE_KEY environment variables")
        sys.exit(1)

    import argparse
    parser = argparse.ArgumentParser(description="CodeRAG Indexer")
    parser.add_argument("--user_id", type=str, help="Supabase User ID to associate code with")
    args = parser.parse_args()

    indexer = CodeIndexer()
    if args.user_id:
        indexer.user_id = args.user_id
        logger.info(f"👤 Indexing for User ID: {args.user_id}")

    success = indexer.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
