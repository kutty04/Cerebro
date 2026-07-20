from fastapi import FastAPI, HTTPException, Query, status, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import supabase
import os
import re
from typing import Optional, List, Dict, Any
import logging
from dotenv import load_dotenv
import time
import git
import shutil
import tempfile
from pathlib import Path
import requests
from contextlib import asynccontextmanager

from indexer import CodeIndexer
from telemetry import (
    init_db,
    log_search,
    save_chat,
    get_analytics,
    get_chat_history,
    get_cached_query,
    set_cached_query,
)

load_dotenv(override=True)

# Global variables for database state
db = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    global db
    logger.info("🚀 Starting CodeRAG API initialization...")

    # 1. Init Telemetry DB
    try:
        init_db()
        logger.info("✅ Telemetry DB initialized")
    except Exception as e:
        logger.error(f"❌ Telemetry DB failed: {e}")

    # 2. Init Supabase
    try:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        if url and key:
            db = supabase.create_client(url, key)
            logger.info("✅ Supabase client initialized")
        else:
            logger.warning("⚠️ Supabase credentials missing!")
    except Exception as e:
        logger.error(f"❌ Supabase init failed: {e}")

    logger.info("✅ System ready (Using Serverless Embeddings)")
    yield
    logger.info("🛑 CodeRAG API shutting down...")


# Initialize FastAPI app with lifespan
try:
    app = FastAPI(title="CodeRAG API", version="1.0.0", lifespan=lifespan)
    logger.info("🚀 Starting CodeRAG API...")
except Exception as e:
    logger.error(f"💥 Failed to initialize FastAPI: {e}")
    raise


# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "https://cerebro-delta-silk.vercel.app",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Pydantic models with defensive bounds
class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="Natural language code search query")
    top_k: Optional[int] = Field(default=5, ge=1, le=50, description="Number of results to retrieve")
    repo_filter: Optional[str] = Field(default=None, max_length=200, description="Filter by repository name")
    history: Optional[List[Dict[str, Any]]] = Field(default_factory=list, description="Recent conversation turns")
    user_id: Optional[str] = Field(default=None, max_length=200, description="User identifier")


class IngestRequest(BaseModel):
    repo_url: str = Field(..., min_length=5, max_length=500, description="Repository URL to ingest")
    user_id: str = Field(..., min_length=1, max_length=200, description="User identifier")


class IndexRequest(BaseModel):
    repo_name: str = Field(..., min_length=1, max_length=200, description="Repository name")
    file_path: str = Field(..., min_length=1, max_length=500, description="File path relative to repo")
    language: str = Field(..., min_length=1, max_length=50, description="Source code language")
    code_content: str = Field(..., min_length=1, max_length=500000, description="Code snippet content")
    source_url: Optional[str] = Field(default=None, max_length=500, description="Web source URL")
    user_id: Optional[str] = Field(default=None, max_length=200, description="User identifier")


class SearchResponse(BaseModel):
    answer: str
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    query: str
    follow_ups: List[str] = Field(default_factory=list)
    confidence: int = 0


class HealthResponse(BaseModel):
    status: str
    embedder_ready: bool
    supabase_ready: bool
    hf_ready: bool
    mode: str


class ReadinessResponse(BaseModel):
    status: str
    database: str
    embeddings: str
    llm: str


# Helper function for serverless embeddings
def get_embedding(text: str) -> Optional[List[float]]:
    hf_token = os.getenv("HF_TOKEN")
    if not hf_token:
        logger.warning("⚠️ HF_TOKEN is missing for embeddings")
        return None

    model_id = "sentence-transformers/all-MiniLM-L6-v2"
    api_url = f"https://router.huggingface.co/hf-inference/models/{model_id}/pipeline/feature-extraction"
    headers = {"Authorization": f"Bearer {hf_token.strip()}"}

    try:
        response = requests.post(api_url, headers=headers, json={"inputs": [text]}, timeout=15)
        if response.status_code == 200:
            res = response.json()
            if isinstance(res, list) and len(res) > 0 and isinstance(res[0], list):
                return res[0]
            elif isinstance(res, list) and len(res) > 0:
                return res
            return res
        else:
            logger.error(f"HF Embedding API returned status {response.status_code}")
            return None
    except requests.exceptions.Timeout:
        logger.error("⏱️ HF Embedding API timed out")
        return None
    except Exception as e:
        logger.error(f"Embedding failed: {e}")
        return None


# Health check endpoint (Liveness probe)
@app.get("/health", response_model=HealthResponse)
async def health_check():
    is_hf_ready = os.getenv("HF_TOKEN") is not None
    return {
        "status": "ok",
        "embedder_ready": is_hf_ready,
        "supabase_ready": db is not None,
        "hf_ready": is_hf_ready,
        "mode": "serverless",
    }


# Readiness probe endpoint (Orchestration readiness check: HTTP 200 if ready, HTTP 503 if degraded)
@app.get("/readiness", response_model=ReadinessResponse)
async def readiness_check(response: Response):
    is_hf_ready = os.getenv("HF_TOKEN") is not None
    is_db_ready = db is not None

    if is_db_ready and is_hf_ready:
        response.status_code = status.HTTP_200_OK
        return {
            "status": "ready",
            "database": "connected",
            "embeddings": "ready",
            "llm": "ready",
        }
    else:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "degraded",
            "database": "connected" if is_db_ready else "disconnected",
            "embeddings": "ready" if is_hf_ready else "unconfigured",
            "llm": "ready" if is_hf_ready else "unconfigured",
        }


# Search endpoint
@app.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    """
    Search codebases using vector similarity + LLM generation.
    Enforces strict user isolation without silent fallback to unscoped retrieval.
    """
    if not db:
        raise HTTPException(status_code=500, detail="Database client is not initialized")

    start_time = time.time()

    # Check SQLite Cache (User-scoped cache lookup)
    try:
        cached = get_cached_query(request.query, request.repo_filter, request.user_id)
        if cached:
            logger.info("🟢 Cache hit! Returning instant response (0 tokens)")
            latency_ms = (time.time() - start_time) * 1000
            log_search(request.query, request.repo_filter, cached["confidence"], latency_ms)
            return SearchResponse(
                answer=cached["answer"],
                sources=cached["sources"],
                query=request.query,
                follow_ups=[
                    "How does this connect to other files?",
                    "Can you explain this in more detail?",
                    "Where is this function called?",
                ],
                confidence=cached["confidence"],
            )
    except Exception as cache_err:
        logger.warning(f"Cache check failed silently: {cache_err}")

    try:
        # Step 1: Embed Query (Serverless)
        query_embedding = get_embedding(request.query)
        if not query_embedding:
            raise HTTPException(status_code=502, detail="Embedding service unavailable")

        # Step 2: Search pgvector (Semantic)
        logger.info("📚 Searching vector database...")

        vector_results_data = []

        if request.user_id:
            # User identity is present: MUST execute user-scoped RPC. FAIL CLOSED if RPC fails.
            try:
                search_rpc = db.rpc(
                    "search_code_snippets",
                    {
                        "query_embedding": query_embedding,
                        "match_count": request.top_k,
                        "p_user_id": request.user_id,
                    },
                )
                if request.repo_filter:
                    search_rpc = search_rpc.eq("repo_name", request.repo_filter)
                res = search_rpc.execute()
                vector_results_data = res.data or []
            except Exception as rpc_e:
                logger.error(f"User-scoped vector search RPC failed: {rpc_e}")
                # FAIL CLOSED: Do not perform an unscoped retry!
                raise HTTPException(
                    status_code=500,
                    detail="Search service error: user isolation query could not be executed safely.",
                )
        else:
            # Anonymous / global search (no user_id provided)
            try:
                search_rpc = db.rpc(
                    "search_code_snippets",
                    {
                        "query_embedding": query_embedding,
                        "match_count": request.top_k,
                    },
                )
                if request.repo_filter:
                    search_rpc = search_rpc.eq("repo_name", request.repo_filter)
                res = search_rpc.execute()
                vector_results_data = res.data or []
            except Exception as rpc_e:
                logger.error(f"Vector search RPC failed: {rpc_e}")
                vector_results_data = []

        # Step 2.5: Keyword Search (Exact Match Fallback - strictly user-scoped when user_id is present)
        stop_words = {
            "how", "do", "did", "i", "we", "you", "what", "is", "where", "can",
            "find", "the", "a", "an", "to", "for", "in", "of", "and", "or", "my",
            "code", "file", "project", "this", "app", "use", "make", "create",
            "show", "tell", "give", "me", "get", "please", "about"
        }
        keywords = [
            word for word in re.findall(r"\b\w+\b", request.query.lower())
            if word not in stop_words and len(word) > 2
        ]
        keywords.sort(key=len, reverse=True)

        keyword_results = []
        if keywords:
            top_keywords = keywords[:3]
            for kw in top_keywords:
                try:
                    kw_search = db.table("code_snippets").select(
                        "id, repo_name, file_path, language, code_content, source_url"
                    )
                    if request.user_id:
                        kw_search = kw_search.eq("user_id", request.user_id)
                    if request.repo_filter:
                        kw_search = kw_search.eq("repo_name", request.repo_filter)

                    kw_search = kw_search.ilike("code_content", f"%{kw}%")
                    kw_res = kw_search.limit(request.top_k).execute()
                    if kw_res.data:
                        keyword_results.extend(kw_res.data)
                except Exception as kw_e:
                    logger.warning(f"Keyword search failed for key '{kw}': {kw_e}")

        # Merge and deduplicate
        merged_data = []
        seen_ids = set()

        for row in keyword_results:
            if row.get("id") not in seen_ids:
                merged_data.append(row)
                seen_ids.add(row.get("id"))

        for row in vector_results_data:
            if row.get("id") not in seen_ids:
                merged_data.append(row)
                seen_ids.add(row.get("id"))

        final_results = merged_data[: request.top_k * 2]

        if not final_results:
            return SearchResponse(
                answer="No matching code snippets found in the indexed codebase. Try broadening your query or selecting 'All Projects'.",
                sources=[],
                query=request.query,
                follow_ups=[],
                confidence=0,
            )

        # Calculate Confidence Score
        max_sim = 0.0
        if vector_results_data:
            max_sim = max([float(row.get("similarity", 0)) for row in vector_results_data], default=0.0)

        base_conf = max_sim * 100
        if keyword_results:
            base_conf += 10

        confidence = min(int(base_conf), 98)
        if confidence < 30 and len(final_results) > 0:
            confidence = 50

        logger.info(f"✅ Found {len(final_results)} matching snippets (Hybrid) - Confidence: {confidence}%")

        # Step 3: Build context from results
        context_parts = []
        sources = []

        for i, result in enumerate(final_results, 1):
            snippet = {
                "rank": i,
                "repo": result.get("repo_name", "unknown"),
                "file": result.get("file_path", "unknown"),
                "language": result.get("language", "text"),
                "code": result.get("code_content", ""),
                "url": result.get("source_url", ""),
            }
            sources.append(snippet)

            context_parts.append(
                f"[Snippet {i}] From {snippet['repo']}/{snippet['file']} ({snippet['language']}):\n"
                f"```{snippet['language']}\n{snippet['code']}\n```"
            )

        context = "\n\n".join(context_parts)

        # Step 4: Generate response with LLM
        logger.info("🤖 Generating response with LLM...")

        system_prompt = f"""You are a master code expert connected to Cerebro AI. Answer the user's question using ONLY the provided code snippets.

CODE CONTEXT:
{context}

RULES:
1. Use ONLY the code context provided above. Do not use outside knowledge.
2. Explain clearly and cite the exact file path and code logic.
3. If the context contains no relevant information, say "I couldn't find this in the retrieved codebase snippets."
4. Never hallucinate APIs or functions not present in context.
5. Provide exactly 3 short follow-up questions formatted at the end like:
FOLLOW_UPS:
- Question 1
- Question 2
- Question 3"""

        messages = [{"role": "system", "content": system_prompt}]

        if request.history:
            for msg in request.history[-4:]:
                if isinstance(msg, dict) and "role" in msg and "content" in msg:
                    messages.append({"role": str(msg["role"]), "content": str(msg["content"])})

        messages.append({"role": "user", "content": request.query})

        hf_env_token = os.getenv("HF_TOKEN")
        current_key = hf_env_token.strip() if hf_env_token else ""

        if not current_key:
            logger.error("❌ HF_TOKEN is missing from environment variables!")
            return SearchResponse(
                answer="Error: AI Brain (HF_TOKEN) is not configured on the server.",
                confidence=0,
                sources=sources,
                query=request.query,
            )

        url = "https://router.huggingface.co/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {current_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "meta-llama/Llama-3.1-8B-Instruct",
            "messages": messages,
            "max_tokens": 500,
            "temperature": 0.5,
        }

        try:
            res = requests.post(url, headers=headers, json=payload, timeout=15)
            if res.status_code == 200:
                final_answer = res.json()["choices"][0]["message"]["content"]
            else:
                logger.error(f"HF Router Error (Status {res.status_code})")
                final_answer = f"Cerebro retrieved relevant snippets, but the AI router service was unavailable (Status {res.status_code})."
        except requests.exceptions.Timeout:
            logger.error("HF Router API timed out")
            final_answer = "Cerebro retrieved relevant snippets, but the AI router service timed out."
        except Exception as api_e:
            logger.error(f"HF Router Connection Error: {api_e}")
            final_answer = "Cerebro retrieved relevant snippets, but could not connect to the AI router."

        # Parse out follow-up questions
        answer_text = final_answer.strip()
        follow_ups = []
        if "FOLLOW_UPS:" in answer_text:
            parts = answer_text.split("FOLLOW_UPS:")
            answer_text = parts[0].strip()
            follow_ups_text = parts[1].strip()

            for line in follow_ups_text.split("\n"):
                line = line.strip()
                if line.startswith("-"):
                    follow_ups.append(line.lstrip("- ").strip())

        # Log analytics and save history
        latency_ms = (time.time() - start_time) * 1000
        try:
            log_search(request.query, request.repo_filter, confidence, latency_ms)
            save_chat(request.user_id or "default_thread", request.query, answer_text, sources)
            set_cached_query(request.query, request.repo_filter, answer_text, sources, confidence, request.user_id)
        except Exception as log_e:
            logger.warning(f"Logging failed: {log_e}")

        return SearchResponse(
            answer=answer_text,
            sources=sources,
            query=request.query,
            follow_ups=follow_ups[:3],
            confidence=confidence,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Search execution failed unexpectedly")
        raise HTTPException(
            status_code=500, detail="Search failed due to an internal server error."
        )


@app.get("/analytics")
async def fetch_analytics():
    try:
        return get_analytics()
    except Exception as e:
        logger.exception("Analytics fetch failed")
        return {"total_searches": 0, "avg_latency_ms": 0.0, "avg_confidence": 0.0, "recent_queries": []}


@app.get("/history")
async def fetch_history():
    try:
        return get_chat_history()
    except Exception as e:
        logger.exception("History fetch failed")
        return []


# Safe Indexing Endpoint (uses serverless embedding)
@app.post("/index")
async def index_snippet(request: IndexRequest):
    """
    Add a code snippet to the index using serverless embeddings.
    """
    if not db:
        raise HTTPException(status_code=500, detail="Database client not initialized")

    try:
        logger.info(f"📝 Indexing snippet for {request.repo_name}/{request.file_path}")

        # Generate embedding via serverless helper
        embedding = get_embedding(request.code_content)
        if not embedding:
            raise HTTPException(
                status_code=502, detail="Failed to generate embedding from serverless provider"
            )

        # Store in Supabase
        data = {
            "repo_name": request.repo_name,
            "file_path": request.file_path,
            "language": request.language,
            "code_content": request.code_content,
            "embedding": embedding,
            "source_url": request.source_url,
            "user_id": request.user_id,
        }

        result = db.table("code_snippets").insert(data).execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Database insertion failed")

        logger.info(f"✅ Successfully indexed {request.file_path}")
        return {"status": "success", "snippet_id": result.data[0].get("id")}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Indexing failed unexpectedly")
        raise HTTPException(status_code=500, detail="Indexing failed due to an internal error.")


@app.post("/ingest")
async def ingest_repo(request: IngestRequest):
    """
    Clone a GitHub repo, index it for a specific user, and clean up.
    """
    temp_dir = tempfile.mkdtemp()
    try:
        logger.info(f"🚀 Ingesting repo: {request.repo_url} for user: {request.user_id}")

        # Basic URL check
        if not request.repo_url.startswith("https://"):
            raise HTTPException(status_code=400, detail="Only HTTPS repository URLs are supported.")

        git.Repo.clone_from(request.repo_url, temp_dir, depth=1)

        repo_name = request.repo_url.rstrip("/").split("/")[-1].replace(".git", "")

        indexer = CodeIndexer(repos_path=temp_dir, repo_url=request.repo_url, repo_name=repo_name)
        if not indexer.initialize():
            raise HTTPException(status_code=500, detail="Indexer initialization failed")

        indexer.user_id = request.user_id

        snippets = indexer.scan_repos()
        if not snippets:
            return {"status": "success", "message": "No indexable code found in repository", "indexed_count": 0}

        indexer.index_snippets(snippets)

        return {
            "status": "success",
            "message": f"Successfully indexed {len(snippets)} snippets from {request.repo_url}",
            "indexed_count": len(snippets),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ingestion failed")
        raise HTTPException(status_code=500, detail="Ingestion failed due to an internal error.")
    finally:
        def onerror(func, path, exc_info):
            import stat
            if not os.access(path, os.W_OK):
                os.chmod(path, stat.S_IWUSR)
                func(path)
            else:
                raise

        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, onerror=onerror)
        except Exception as cleanup_e:
            logger.warning(f"⚠️ Cleanup failed for {temp_dir}: {cleanup_e}")


@app.get("/user-repos")
async def get_user_repos(user_id: str = Query(..., min_length=1, max_length=200)):
    """
    Fetch unique repository names indexed for this user.
    """
    if not db:
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        result = db.table("code_snippets").select("repo_name").eq("user_id", user_id).execute()
        repos = sorted(list(set([r["repo_name"] for r in (result.data or []) if "repo_name" in r])))
        return {"repos": repos}
    except Exception as e:
        logger.exception("Failed to fetch user repos")
        raise HTTPException(status_code=500, detail="Failed to fetch repositories.")


@app.post("/delete-repo")
async def delete_repo(
    repo_name: str = Query(..., min_length=1, max_length=200),
    user_id: str = Query(..., min_length=1, max_length=200),
):
    """
    Delete all snippets associated with a repository for a user.
    """
    if not db:
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        db.table("code_snippets").delete().eq("repo_name", repo_name).eq("user_id", user_id).execute()
        return {"status": "success", "message": f"Repository {repo_name} deleted"}
    except Exception as e:
        logger.exception("Failed to delete repo")
        raise HTTPException(status_code=500, detail="Failed to delete repository.")


@app.get("/graph-data")
async def get_graph_data(user_id: str = Query(..., min_length=1, max_length=200)):
    """
    Generate graph nodes and links for the user's codebase.
    """
    if not db:
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        result = db.table("code_snippets").select("repo_name, file_path").eq("user_id", user_id).execute()

        nodes = []
        links = []
        seen_repos = set()
        seen_files = set()

        nodes.append({"id": "ME", "name": "Neural Core", "val": 15, "color": "#38bdf8"})

        for item in (result.data or []):
            repo = item.get("repo_name")
            file = item.get("file_path")
            if not repo or not file:
                continue

            if repo not in seen_repos:
                nodes.append({"id": repo, "name": repo, "val": 10, "color": "#818cf8"})
                links.append({"source": "ME", "target": repo})
                seen_repos.add(repo)

            file_id = f"{repo}:{file}"
            if file_id not in seen_files:
                nodes.append({"id": file_id, "name": file.split("/")[-1], "val": 4, "color": "#94a3b8"})
                links.append({"source": repo, "target": file_id})
                seen_files.add(file_id)

        return {"nodes": nodes, "links": links}
    except Exception as e:
        logger.exception("Failed to generate graph")
        raise HTTPException(status_code=500, detail="Failed to generate graph visualization.")


# Root endpoint
@app.get("/")
async def root():
    return {
        "name": "CodeRAG API",
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "readiness": "/readiness",
            "search": "/search (POST)",
            "index": "/index (POST)",
            "ingest": "/ingest (POST)",
        },
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 7860))
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port)
