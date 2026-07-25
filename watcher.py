import time
import os
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from indexer import CodeIndexer

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

CODE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".md": "markdown",
    ".json": "json",
    ".sql": "sql",
}

class LiveIndexerHandler(FileSystemEventHandler):
    def __init__(self, repos_path):
        super().__init__()
        self.repos_path = repos_path
        self.indexer = CodeIndexer()
        if not self.indexer.initialize():
            logger.error("Failed to connect to Supabase/Embedder for live watching.")

    def on_modified(self, event):
        if not event.is_directory:
            self.process_file(event.src_path)

    def on_created(self, event):
        if not event.is_directory:
            self.process_file(event.src_path)

    def process_file(self, file_path):
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in CODE_EXTENSIONS:
            return

        # Simple debounce to avoid reading while file is still writing
        time.sleep(0.5)

        try:
            rel_file_path = os.path.relpath(file_path, self.repos_path)
            parts = rel_file_path.split(os.sep)
            if len(parts) < 2:
                return # Must be inside a repo folder
            repo_name = parts[0]
            language = CODE_EXTENSIONS.get(ext, "text")

            # 1. Delete old snippets for this file to avoid duplicates
            if self.indexer.db:
                self.indexer.db.table("code_snippets").delete().eq("file_path", rel_file_path).execute()
                logger.info(f"🧹 Cleared old index for: {rel_file_path}")

            # 2. Read new content
            with open(file_path, 'r', encoding='utf-8') as f:
                code = f.read()

            # 3. Chunk
            chunks = self.indexer.chunk_code(code, file_path)
            snippets = []
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

            # 4. Re-index
            if snippets:
                self.indexer.index_snippets(snippets)
                logger.info(f"⚡ Live Indexed: {rel_file_path} ({len(snippets)} chunks)")

        except Exception as e:
            logger.error(f"Error live indexing {file_path}: {str(e)}")


def start_watcher(path_to_watch):
    logger.info(f"👁️ Cerebro Watcher started on: {path_to_watch}")
    logger.info("Listening for file saves... Any changes will be instantly synced to Supabase.")
    event_handler = LiveIndexerHandler(path_to_watch)
    observer = Observer()
    observer.schedule(event_handler, path_to_watch, recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == "__main__":
    desktop_path = os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop")
    repos_path = os.path.join(desktop_path, "coderag-data")
    
    if os.path.exists(repos_path):
        start_watcher(repos_path)
    else:
        logger.error(f"Path not found: {repos_path}")
