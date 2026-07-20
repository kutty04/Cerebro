from fastapi import FastAPI, HTTPException, Query, status, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import supabase
import os
import re
import subprocess
from typing import Optional, List, Dict, Any
import logging
from dotenv import load_dotenv
import time
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
    invalidate_user_repo_cache,
)
from ingestion_validator import (
    DEFAULT_LIMITS,
    concurrency_manager,
    rate_limiter,
    validate_and_normalize_github_url,
    validate_dns_ip_safety,
    get_dir_size_bytes,
    run_safe_git_clone,
)
from security.auth import AuthenticatedUser, get_current_user, verify_identity_match

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
        logger.error("Telemetry DB initialization failed [op=lifespan_init, exc_type=%s]", type(e).__name__)

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
        logger.error("Supabase client initialization failed [op=lifespan_init, exc_type=%s]", type(e).__name__)

    logger.info("✅ System ready (Using Serverless Embeddings)")
    yield
    logger.info("🛑 CodeRAG API shutting down...")


# Initialize FastAPI app with lifespan
try:
    app = FastAPI(title="CodeRAG API", version="1.0.0", lifespan=lifespan)
    logger.info("🚀 Starting CodeRAG API...")
except Exception as e:
    logger.error("FastAPI initialization failed [op=app_init, exc_type=%s]", type(e).__name__)
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
    user_id: Optional[str] = Field(default=None, max_length=200, description="Deprecated user identifier")


class IngestRequest(BaseModel):
    repo_url: str = Field(..., min_length=5, max_length=500, description="Repository URL to ingest")
    user_id: Optional[str] = Field(default=None, max_length=200, description="Deprecated user identifier")


class IndexRequest(BaseModel):
    repo_name: str = Field(..., min_length=1, max_length=200, description="Repository name")
    file_path: str = Field(..., min_length=1, max_length=500, description="File path relative to repo")
    language: str = Field(..., min_length=1, max_length=50, description="Source code language")
    code_content: str = Field(..., min_length=1, max_length=500000, description="Code snippet content")
    source_url: Optional[str] = Field(default=None, max_length=500, description="Web source URL")
    user_id: Optional[str] = Field(default=None, max_length=200, description="Deprecated user identifier")


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
            logger.error("HF Embedding API returned non-200 status [op=get_embedding, status_code=%s]", response.status_code)
            return None
    except requests.exceptions.Timeout:
        logger.error("HF Embedding API timed out [op=get_embedding, exc_type=Timeout]")
        return None
    except Exception as e:
        logger.error("Embedding request failed [op=get_embedding, exc_type=%s]", type(e).__name__)
        return None


# Unprotected Public Endpoints
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


# Protected Endpoint: Search
@app.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest, current_user: AuthenticatedUser = Depends(get_current_user)):
    """
    Search codebases using vector similarity + LLM generation.
    Strictly isolated to authenticated current_user.
    """
    if not db:
        raise HTTPException(status_code=500, detail="Database client is not initialized")

    # Enforce identity matching if client sends legacy user_id
    verify_identity_match(current_user.id, request.user_id)
    user_id = current_user.id

    start_time = time.time()

    # Check SQLite Cache (User-scoped SHA-256 cache lookup)
    try:
        cached = get_cached_query(request.query, request.repo_filter, user_id)
        if cached:
            logger.info("🟢 Cache hit! Returning instant response (0 tokens)")
            latency_ms = (time.time() - start_time) * 1000
            log_search(request.query, request.repo_filter, cached["confidence"], latency_ms, user_id=user_id)
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
        logger.warning("Cache check failed silently [op=cache_check, exc_type=%s]", type(cache_err).__name__)

    try:
        # Step 1: Embed Query (Serverless)
        query_embedding = get_embedding(request.query)
        if not query_embedding:
            raise HTTPException(status_code=502, detail="Embedding service unavailable")

        # Step 2: Search pgvector (Semantic RPC strictly scoped by verified user_id)
        logger.info("📚 Searching vector database...")
        vector_results_data = []

        try:
            search_rpc = db.rpc(
                "search_code_snippets",
                {
                    "query_embedding": query_embedding,
                    "match_count": request.top_k,
                    "p_user_id": user_id,
                },
            )
            if request.repo_filter:
                search_rpc = search_rpc.eq("repo_name", request.repo_filter)
            res = search_rpc.execute()
            vector_results_data = res.data or []
        except Exception as rpc_e:
            logger.error("User-scoped vector search RPC failed [op=search_user_rpc, exc_type=%s]", type(rpc_e).__name__)
            raise HTTPException(
                status_code=500,
                detail="Search service error: user isolation query could not be executed safely.",
            )

        # Step 2.5: Keyword Search (Exact Match Fallback - strictly user-scoped)
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
                    ).eq("user_id", user_id)

                    if request.repo_filter:
                        kw_search = kw_search.eq("repo_name", request.repo_filter)

                    kw_search = kw_search.ilike("code_content", f"%{kw}%")
                    kw_res = kw_search.limit(request.top_k).execute()
                    if kw_res.data:
                        keyword_results.extend(kw_res.data)
                except Exception as kw_e:
                    logger.warning("Keyword search failed [op=keyword_search, exc_type=%s]", type(kw_e).__name__)

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
                logger.error("HF Router Error non-200 status [op=chat_completion, status_code=%s]", res.status_code)
                final_answer = f"Cerebro retrieved relevant snippets, but the AI router service was unavailable (Status {res.status_code})."
        except requests.exceptions.Timeout:
            logger.error("HF Router API timed out [op=chat_completion, exc_type=Timeout]")
            final_answer = "Cerebro retrieved relevant snippets, but the AI router service timed out."
        except Exception as api_e:
            logger.error("HF Router connection failed [op=chat_completion, exc_type=%s]", type(api_e).__name__)
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

        # Log analytics and save history strictly for current_user
        latency_ms = (time.time() - start_time) * 1000
        try:
            log_search(request.query, request.repo_filter, confidence, latency_ms, user_id=user_id)
            save_chat(user_id, request.query, answer_text, sources, user_id=user_id)
            set_cached_query(request.query, request.repo_filter, answer_text, sources, confidence, user_id=user_id)
        except Exception as log_e:
            logger.warning("Logging failed [op=post_search_logging, exc_type=%s]", type(log_e).__name__)

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
        logger.error("Search execution failed unexpectedly [op=search, exc_type=%s]", type(e).__name__)
        raise HTTPException(
            status_code=500, detail="Search failed due to an internal server error."
        )


# Protected Endpoint: Analytics
@app.get("/analytics")
async def fetch_analytics(current_user: AuthenticatedUser = Depends(get_current_user)):
    try:
        return get_analytics(user_id=current_user.id)
    except Exception as e:
        logger.error("Analytics fetch failed [op=fetch_analytics, exc_type=%s]", type(e).__name__)
        return {"total_searches": 0, "avg_latency_ms": 0.0, "avg_confidence": 0.0, "recent_queries": []}


# Protected Endpoint: History
@app.get("/history")
async def fetch_history(current_user: AuthenticatedUser = Depends(get_current_user)):
    try:
        return get_chat_history(user_id=current_user.id)
    except Exception as e:
        logger.error("History fetch failed [op=fetch_history, exc_type=%s]", type(e).__name__)
        return []


# Protected Endpoint: Index Snippet
@app.post("/index")
async def index_snippet(request: IndexRequest, current_user: AuthenticatedUser = Depends(get_current_user)):
    if not db:
        raise HTTPException(status_code=500, detail="Database client not initialized")

    verify_identity_match(current_user.id, request.user_id)
    user_id = current_user.id

    try:
        logger.info(f"📝 Indexing snippet for {request.repo_name}/{request.file_path}")

        embedding = get_embedding(request.code_content)
        if not embedding:
            raise HTTPException(
                status_code=502, detail="Failed to generate embedding from serverless provider"
            )

        data = {
            "repo_name": request.repo_name,
            "file_path": request.file_path,
            "language": request.language,
            "code_content": request.code_content,
            "embedding": embedding,
            "source_url": request.source_url,
            "user_id": user_id,
        }

        result = db.table("code_snippets").insert(data).execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Database insertion failed")

        invalidate_user_repo_cache(user_id, request.repo_name)
        logger.info(f"✅ Successfully indexed {request.file_path}")
        return {"status": "success", "snippet_id": result.data[0].get("id")}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Indexing failed unexpectedly [op=index_snippet, exc_type=%s]", type(e).__name__)
        raise HTTPException(status_code=500, detail="Indexing failed due to an internal error.")


# Protected Endpoint: Ingest Repository
@app.post("/ingest")
async def ingest_repo(request: IngestRequest, current_user: AuthenticatedUser = Depends(get_current_user)):
    if not db:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database client is not initialized.")

    verify_identity_match(current_user.id, request.user_id)
    user_id = current_user.id

    canonical_url = validate_and_normalize_github_url(request.repo_url)
    rate_limiter.check_and_record(user_id)

    if not validate_dns_ip_safety("github.com"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository network destination is unreachable or restricted.",
        )

    concurrency_manager.acquire(user_id, canonical_url)

    temp_dir = tempfile.mkdtemp(prefix="cerebro_ingest_")
    inserted_snippet_ids: List[int] = []
    repo_name = canonical_url.split("/")[-1]

    try:
        logger.info(f"🚀 Ingesting repo: {canonical_url} for user: {user_id}")

        try:
            returncode = run_safe_git_clone(canonical_url, temp_dir, DEFAULT_LIMITS.MAX_REPO_CLONE_TIMEOUT_SEC)
            if returncode != 0:
                logger.error("Git clone returned non-zero exit code [op=ingest_clone, code=%d]", returncode)
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Repository inaccessible, private, or not found.",
                )
        except subprocess.TimeoutExpired:
            logger.error("Git clone process timed out [op=ingest_clone]")
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Repository clone timed out.",
            )

        try:
            total_disk_bytes = get_dir_size_bytes(temp_dir)
            if total_disk_bytes > DEFAULT_LIMITS.MAX_REPO_DISK_SIZE_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Repository exceeds maximum disk size limit (50MB). Consider narrowing scope.",
                )
        except HTTPException:
            raise
        except Exception as disk_e:
            logger.error("Disk size check failed [op=ingest_disk_scan, exc_type=%s]", type(disk_e).__name__)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to inspect repository filesystem.",
            )

        indexer = CodeIndexer(repos_path=temp_dir, repo_url=canonical_url, repo_name=repo_name, limits=DEFAULT_LIMITS)
        indexer.db = db
        indexer.user_id = user_id

        snippets = indexer.scan_repos()
        if not snippets:
            return {
                "status": "success",
                "message": "No indexable code found in repository",
                "indexed_count": 0,
            }

        inserted_snippet_ids = indexer.index_snippets(snippets)
        invalidate_user_repo_cache(user_id, repo_name)

        return {
            "status": "success",
            "message": f"Successfully indexed {len(inserted_snippet_ids)} snippets from {canonical_url}",
            "indexed_count": len(inserted_snippet_ids),
        }

    except HTTPException:
        if inserted_snippet_ids and db:
            try:
                db.table("code_snippets").delete().in_("id", inserted_snippet_ids).execute()
                logger.info("Rolled back partially inserted snippet records [op=ingest_rollback, count=%d]", len(inserted_snippet_ids))
            except Exception as rb_e:
                logger.error("Database rollback failed [op=ingest_rollback, exc_type=%s]", type(rb_e).__name__)
        raise
    except Exception as e:
        logger.error("Ingestion failed unexpectedly [op=ingest_repo, exc_type=%s]", type(e).__name__)
        if inserted_snippet_ids and db:
            try:
                db.table("code_snippets").delete().in_("id", inserted_snippet_ids).execute()
            except Exception:
                pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ingestion failed due to an internal error.",
        )
    finally:
        concurrency_manager.release(user_id, canonical_url)

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
            logger.warning("Cleanup failed [op=ingest_cleanup, exc_type=%s]", type(cleanup_e).__name__)


# Protected Endpoint: User Repositories
@app.get("/user-repos")
async def get_user_repos(
    user_id: Optional[str] = Query(None, min_length=1, max_length=200),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    if not db:
        raise HTTPException(status_code=500, detail="Database not connected")

    verify_identity_match(current_user.id, user_id)
    target_user_id = current_user.id

    try:
        result = db.table("code_snippets").select("repo_name").eq("user_id", target_user_id).execute()
        repos = sorted(list(set([r["repo_name"] for r in (result.data or []) if "repo_name" in r])))
        return {"repos": repos}
    except Exception as e:
        logger.error("Failed to fetch user repos [op=get_user_repos, exc_type=%s]", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to fetch repositories.")


# Protected Endpoint: Delete Repository
@app.post("/delete-repo")
async def delete_repo(
    repo_name: str = Query(..., min_length=1, max_length=200),
    user_id: Optional[str] = Query(None, min_length=1, max_length=200),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    if not db:
        raise HTTPException(status_code=500, detail="Database not connected")

    verify_identity_match(current_user.id, user_id)
    target_user_id = current_user.id

    try:
        db.table("code_snippets").delete().eq("repo_name", repo_name).eq("user_id", target_user_id).execute()
        invalidate_user_repo_cache(target_user_id, repo_name)
        return {"status": "success", "message": f"Repository {repo_name} deleted"}
    except Exception as e:
        logger.error("Failed to delete repo [op=delete_repo, exc_type=%s]", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to delete repository.")


# Protected Endpoint: Graph Data
@app.get("/graph-data")
async def get_graph_data(
    user_id: Optional[str] = Query(None, min_length=1, max_length=200),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    if not db:
        raise HTTPException(status_code=500, detail="Database not connected")

    verify_identity_match(current_user.id, user_id)
    target_user_id = current_user.id

    try:
        result = db.table("code_snippets").select("repo_name, file_path").eq("user_id", target_user_id).execute()

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
        logger.error("Failed to generate graph [op=get_graph_data, exc_type=%s]", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to generate graph visualization.")


# Root endpoint (Public)
@app.get("/")
async def root():
    return {
        "name": "CodeRAG API",
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "readiness": "/readiness",
            "search": "/search (POST - Auth Required)",
            "index": "/index (POST - Auth Required)",
            "ingest": "/ingest (POST - Auth Required)",
        },
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 7860))
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port)
