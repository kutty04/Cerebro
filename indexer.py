import os
import sys
from pathlib import Path
import supabase
import logging
from typing import List, Tuple
import json
import requests
from dotenv import load_dotenv

load_dotenv()


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
REPOS_PATH = os.getenv("REPOS_PATH", "./coderag-data")  # Local folder with repos

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


class CodeIndexer:
    def __init__(self, repos_path: str = None):
        self.embedder = None
        self.db = None
        self.indexed_count = 0
        self.failed_count = 0
        self.snippets_to_index = []
        self.user_id = None
        self.repos_path = repos_path or REPOS_PATH

    def initialize(self) -> bool:
        """Initialize embedder and database connection"""
        logger.info("🚀 Initializing CodeIndexer...")

        try:
            # Check HF Token
            if not os.getenv("HF_TOKEN"):
                logger.error("❌ Missing HF_TOKEN environment variable for serverless embeddings")
                return False
            logger.info("✅ Embedder configured (Serverless)")
        except Exception as e:
            logger.error(f"❌ Failed to configure serverless embedder: {e}")
            return False

        try:
            # Initialize Supabase
            if not SUPABASE_URL or not SUPABASE_KEY:
                logger.error("❌ Missing SUPABASE_URL or SUPABASE_KEY environment variables")
                return False

            self.db = supabase.create_client(SUPABASE_URL, SUPABASE_KEY)
            logger.info("✅ Supabase connected")
        except Exception as e:
            logger.error(f"❌ Failed to connect to Supabase: {e}")
            return False

        return True

    def should_skip_folder(self, folder_name: str) -> bool:
        """Check if folder should be skipped"""
        return folder_name in SKIP_FOLDERS or folder_name.startswith(".")

    def should_skip_file(self, file_path: str) -> bool:
        """Check if file should be indexed"""
        # Skip hidden files
        if any(part.startswith(".") for part in Path(file_path).parts):
            return True
        # Skip if extension not in CODE_EXTENSIONS
        return Path(file_path).suffix.lower() not in CODE_EXTENSIONS

    def chunk_code(self, code: str, file_path: str, max_lines: int = 40) -> List[Tuple[str, int]]:
        """
        Split code into chunks and intelligently inject metadata headers.
        Returns list of (chunk_text, start_line_number)
        """
        lines = code.split("\n")
        chunks = []
        
        import re
        # Regex to detect JS/Python functions, classes, and arrow functions
        def_pattern = re.compile(r'^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class|def)\s+([a-zA-Z0-9_]+)\b|const\s+([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[a-zA-Z0-9_]+)\s*=>')
        
        current_context = "Global Context"

        for i in range(0, len(lines), max_lines):
            chunk_lines = lines[i : i + max_lines]
            raw_text = "\n".join(chunk_lines)

            if not raw_text.strip():
                continue
                
            # Scan this chunk for any function/class signatures
            found_defs = []
            for line in chunk_lines:
                match = def_pattern.search(line)
                if match:
                    name = match.group(1) or match.group(2)
                    if name:
                        found_defs.append(name)
                        current_context = name # Update running context for the next chunk if it overflows
            
            # Inject intelligent metadata directly into the text so the embedder learns it!
            context_label = ", ".join(found_defs) if found_defs else f"Continued from {current_context}"
            metadata_header = f"/* METADATA -> File: {file_path} | Implements: {context_label} */\n"
            
            # The vector DB will now semantically link the raw code to these function names
            chunks.append((metadata_header + raw_text, i + 1))

        return chunks if chunks else [(f"/* METADATA -> File: {file_path} */\n{code}", 1)]

    def scan_repos(self) -> List[dict]:
        """Scan all repos and collect code snippets"""
        logger.info(f"📂 Scanning repos from: {self.repos_path}")

        if not os.path.exists(self.repos_path):
            logger.error(f"❌ Directory not found: {self.repos_path}")
            return []

        snippets = []

        # Walk through all repos
        for root, dirs, files in os.walk(self.repos_path):
            # Remove skipped folders from dirs in-place (prevents walking into them)
            dirs[:] = [d for d in dirs if not self.should_skip_folder(d)]

            # Get repo name (first folder under self.repos_path)
            repo_path = Path(root).relative_to(self.repos_path)
            repo_name = str(repo_path).split(os.sep)[0] if str(repo_path) != "." else "unknown"

            for file in files:
                file_path = os.path.join(root, file)

                # Skip if shouldn't index
                if self.should_skip_file(file_path):
                    continue

                try:
                    # Read file
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        code = f.read()

                    if not code.strip():
                        continue

                    # Get language
                    ext = Path(file_path).suffix.lower()
                    language = CODE_EXTENSIONS.get(ext, "text")

                    # Get relative file path
                    rel_file_path = os.path.relpath(file_path, self.repos_path)

                    # Chunk the code
                    chunks = self.chunk_code(code, file_path)

                    for chunk_text, start_line in chunks:
                        snippet = {
                            "repo_name": repo_name,
                            "file_path": rel_file_path,
                            "language": language,
                            "code_content": chunk_text,
                            "source_url": f"file://{file_path}",
                            "start_line": start_line,
                        }
                        snippets.append(snippet)

                    logger.info(f"✅ Scanned {rel_file_path} ({language}) - {len(chunks)} chunks")

                except Exception as e:
                    logger.warning(f"⚠️ Failed to read {file_path}: {e}")
                    continue

        logger.info(f"📊 Total snippets found: {len(snippets)}")
        return snippets

    def get_serverless_embedding(self, text: str) -> List[float]:
        """Get embeddings from Hugging Face Inference API"""
        hf_token = os.getenv("HF_TOKEN")
        model_id = "sentence-transformers/all-MiniLM-L6-v2"
        api_url = f"https://api-inference.huggingface.co/models/{model_id}"
        headers = {"Authorization": f"Bearer {hf_token}"}
        
        try:
            response = requests.post(api_url, headers=headers, json={"inputs": text}, timeout=15)
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"HF Embedding Error: {response.text}")
                return None
        except Exception as e:
            logger.error(f"Embedding Exception: {e}")
            return None

    def index_snippets(self, snippets: List[dict]) -> None:
        """Generate embeddings and index snippets in Supabase"""
        if not snippets:
            logger.warning("⚠️ No snippets to index")
            return

        logger.info(f"🔄 Indexing {len(snippets)} snippets...")

        for i, snippet in enumerate(snippets, 1):
            try:
                # Generate embedding
                embedding = self.get_serverless_embedding(snippet["code_content"])
                
                if not embedding:
                    logger.warning(f"⚠️ Skipping snippet {i}: Failed to generate embedding")
                    self.failed_count += 1
                    continue

                # Prepare data for Supabase
                data = {
                    "repo_name": snippet["repo_name"],
                    "file_path": snippet["file_path"],
                    "language": snippet["language"],
                    "code_content": snippet["code_content"],
                    "embedding": embedding,
                    "source_url": snippet["source_url"],
                    "user_id": self.user_id
                }

                # Insert into Supabase
                result = self.db.table("code_snippets").insert(data).execute()

                self.indexed_count += 1
                
                # Progress indicator
                if i % 10 == 0:
                    logger.info(f"📈 Progress: {i}/{len(snippets)} snippets indexed")

            except Exception as e:
                self.failed_count += 1
                logger.error(f"❌ Failed to index snippet {i}: {e}")

        logger.info(f"✅ Indexing complete!")
        logger.info(f"📊 Successfully indexed: {self.indexed_count}")
        logger.info(f"❌ Failed: {self.failed_count}")

    def run(self) -> bool:
        """Run the full indexing pipeline"""
        logger.info("=" * 60)
        logger.info("CodeRAG Indexer Started")
        logger.info("=" * 60)

        # Initialize
        if not self.initialize():
            return False

        # Scan repos
        snippets = self.scan_repos()
        if not snippets:
            logger.error("❌ No snippets found to index")
            return False

        # Index snippets
        self.index_snippets(snippets)

        logger.info("=" * 60)
        logger.info("CodeRAG Indexer Finished")
        logger.info("=" * 60)

        return self.indexed_count > 0


def main():
    # Check environment variables
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("❌ Missing environment variables:")
        logger.error("   - SUPABASE_URL")
        logger.error("   - SUPABASE_KEY")
        logger.error("\nSet them using:")
        logger.error("   export SUPABASE_URL='your_url'")
        logger.error("   export SUPABASE_KEY='your_key'")
        sys.exit(1)

    import argparse
    parser = argparse.ArgumentParser(description="CodeRAG Indexer")
    parser.add_argument("--user_id", type=str, help="Supabase User ID to associate code with")
    args = parser.parse_args()

    # Run indexer
    indexer = CodeIndexer()
    if args.user_id:
        indexer.user_id = args.user_id
        logger.info(f"👤 Indexing for User ID: {args.user_id}")
    
    success = indexer.run()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
