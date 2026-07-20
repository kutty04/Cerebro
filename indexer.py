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

from ingestion_validator import DEFAULT_LIMITS, IngestionLimits

load_dotenv()


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

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

# Folders to skip
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
    "android",
    "ios",
    ".flutter-plugins-dependencies",
    "web",
}


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

    def should_skip_file(self, file_path: str) -> bool:
        """Check if file should be indexed"""
        if any(part.startswith(".") for part in Path(file_path).parts):
            return True
        return Path(file_path).suffix.lower() not in CODE_EXTENSIONS

    def chunk_code(self, code: str, file_path: str, max_lines: int = 40) -> List[Tuple[str, int]]:
        """
        Split code into chunks and intelligently inject metadata headers.
        Returns list of (chunk_text, start_line_number)
        """
        lines = code.split("\n")
        chunks = []

        import re
        def_pattern = re.compile(r'^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class|def)\s+([a-zA-Z0-9_]+)\b|const\s+([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[a-zA-Z0-9_]+)\s*=>')

        current_context = "Global Context"

        for i in range(0, len(lines), max_lines):
            chunk_lines = lines[i : i + max_lines]
            raw_text = "\n".join(chunk_lines)

            if not raw_text.strip():
                continue

            found_defs = []
            for line in chunk_lines:
                match = def_pattern.search(line)
                if match:
                    name = match.group(1) or match.group(2)
                    if name:
                        found_defs.append(name)
                        current_context = name

            context_label = ", ".join(found_defs) if found_defs else f"Continued from {current_context}"
            metadata_header = f"/* METADATA -> File: {file_path} | Implements: {context_label} */\n"

            chunks.append((metadata_header + raw_text, i + 1))

        return chunks if chunks else [(f"/* METADATA -> File: {file_path} */\n{code}", 1)]

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

                    code_bytes = len(code.encode("utf-8"))
                    if total_indexed_bytes + code_bytes > self.limits.MAX_TOTAL_INDEXED_BYTES:
                        raise HTTPException(
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail="Repository exceeds maximum total indexed content size limit.",
                        )

                    ext = file_path_obj.suffix.lower()
                    language = CODE_EXTENSIONS.get(ext, "text")

                    chunks = self.chunk_code(code, rel_file_path)

                    if total_chunks + len(chunks) > self.limits.MAX_TOTAL_CHUNKS:
                        raise HTTPException(
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail="Repository exceeds maximum chunk count limit.",
                        )

                    for chunk_text, start_line in chunks:
                        source_url = self.repo_url if self.repo_url else f"file://{file_path}"
                        if self.repo_url and "github.com" in self.repo_url:
                            base_url = self.repo_url.replace(".git", "")
                            source_url = f"{base_url}/blob/main/{rel_file_path}#L{start_line}"

                        snippet = {
                            "repo_name": repo_name,
                            "file_path": rel_file_path,
                            "language": language,
                            "code_content": chunk_text,
                            "source_url": source_url,
                            "start_line": start_line,
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

        logger.info(f"📊 Total snippets found: {len(snippets)}")
        return snippets

    def get_serverless_embedding(self, text: str) -> Optional[List[float]]:
        """Get embeddings from Hugging Face Inference API"""
        hf_token = os.getenv("HF_TOKEN")
        model_id = "sentence-transformers/all-MiniLM-L6-v2"
        api_url = f"https://router.huggingface.co/hf-inference/models/{model_id}/pipeline/feature-extraction"
        headers = {"Authorization": f"Bearer {hf_token}"}

        try:
            response = requests.post(api_url, headers=headers, json={"inputs": [text]}, timeout=30)
            if response.status_code == 200:
                res = response.json()
                if isinstance(res, list) and len(res) > 0 and isinstance(res[0], list):
                    return res[0]
                elif isinstance(res, list) and len(res) > 0:
                    return res
                return res
            else:
                logger.error(f"HF Embedding Error status={response.status_code}")
                return None
        except Exception as e:
            logger.error("Embedding exception [op=get_serverless_embedding, exc_type=%s]", type(e).__name__)
            return None

    def index_snippets(self, snippets: List[dict]) -> List[int]:
        """Generate embeddings and index snippets in Supabase. Returns list of inserted snippet IDs."""
        if not snippets:
            logger.warning("⚠️ No snippets to index")
            return []

        logger.info(f"🔄 Indexing {len(snippets)} snippets...")
        inserted_ids = []

        for i, snippet in enumerate(snippets, 1):
            try:
                embedding = self.get_serverless_embedding(snippet["code_content"])

                if not embedding:
                    logger.warning(f"⚠️ Skipping snippet {i}: Failed to generate embedding")
                    self.failed_count += 1
                    continue

                data = {
                    "repo_name": snippet["repo_name"],
                    "file_path": snippet["file_path"],
                    "language": snippet["language"],
                    "code_content": snippet["code_content"],
                    "embedding": embedding,
                    "source_url": snippet["source_url"],
                    "user_id": self.user_id,
                    "repository_id": self.repository_id,
                    "ingestion_job_id": self.ingestion_job_id,
                    "index_version": self.index_version,
                    "commit_sha": self.commit_sha,
                }

                result = self.db.table("code_snippets").insert(data).execute()
                if result.data and len(result.data) > 0:
                    inserted_ids.append(result.data[0].get("id"))
                self.indexed_count += 1

                if i % 10 == 0:
                    logger.info(f"📈 Progress: {i}/{len(snippets)} snippets indexed")

            except Exception as e:
                self.failed_count += 1
                logger.error("Failed to index snippet [op=index_snippets, exc_type=%s]", type(e).__name__)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to index code snippets into database.",
                )

        logger.info(f"✅ Indexing complete!")
        logger.info(f"📊 Successfully indexed: {self.indexed_count}")
        logger.info(f"❌ Failed: {self.failed_count}")

        if snippets and self.indexed_count == 0:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to index code snippets into database.",
            )

        return inserted_ids

    def run(self) -> bool:
        """Run the full indexing pipeline"""
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
