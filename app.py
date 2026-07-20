from fastapi import FastAPI, HTTPException, Query, status, Response, Depends, Request
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
    get_analytics,
    get_chat_history,
    get_cached_query,
    set_cached_query,
    invalidate_user_repo_cache,
    create_conversation,
    verify_and_get_conversation,
    add_message_to_conversation,
    get_conversation_messages,
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

db = None
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    global db
    logger.info("🚀 Starting CodeRAG API initialization...")
    try:
        init_db()
        logger.info("✅ Telemetry DB initialized")
    except Exception as e:
        logger.error("Telemetry DB initialization failed [op=lifespan_init, exc_type=%s]", type(e).__name__)

    try:
        url = os.getenv("SUPABASE_URL")
        service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        legacy_key = os.getenv("SUPABASE_KEY")

        key = service_role_key or legacy_key

        if service_role_key and legacy_key and service_role_key.strip() != legacy_key.strip():
            logger.error("Database configuration mismatch: SUPABASE_SERVICE_ROLE_KEY and legacy SUPABASE_KEY disagree.")
            db = None
        elif url and key:
            db = supabase.create_client(url, key)
            logger.info("✅ Supabase client initialized")
        else:
            logger.warning("⚠️ Supabase credentials missing!")
    except Exception as e:
        logger.error("Supabase client initialization failed [op=lifespan_init, exc_type=%s]", type(e).__name__)

    logger.info("✅ System ready (Using Serverless Embeddings)")
    yield
    logger.info("🛑 CodeRAG API shutting down...")


app = FastAPI(title="CodeRAG API", version="1.0.0", lifespan=lifespan)

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


from db_adapter import DatabaseAdapter

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="Natural language code search query")
    top_k: Optional[int] = Field(default=5, ge=1, le=50, description="Number of results to retrieve")
    repo_filter: Optional[str] = Field(default=None, max_length=200, description="Filter by repository name")
    conversation_id: Optional[str] = Field(default=None, max_length=100, description="Server-assigned conversation UUID")
    history: Optional[List[Dict[str, Any]]] = Field(default_factory=list, description="Recent conversation turns")
    user_id: Optional[str] = Field(default=None, max_length=200, description="Deprecated user identifier")
    repository_id: Optional[str] = Field(default=None, max_length=100, description="Optional target repository UUID")


class IngestRequest(BaseModel):
    repo_url: str = Field(..., min_length=5, max_length=500, description="Repository URL to ingest")
    user_id: Optional[str] = Field(default=None, max_length=200, description="Deprecated user identifier")
    repository_id: Optional[str] = Field(default=None, max_length=100, description="Optional target repository UUID")


class IndexRequest(BaseModel):
    repo_name: str = Field(..., min_length=1, max_length=200, description="Repository name")
    file_path: str = Field(..., min_length=1, max_length=500, description="File path relative to repo")
    language: str = Field(..., min_length=1, max_length=50, description="Source code language")
    code_content: str = Field(..., min_length=1, max_length=500000, description="Code snippet content")
    source_url: Optional[str] = Field(default=None, max_length=500, description="Web source URL")
    user_id: Optional[str] = Field(default=None, max_length=200, description="Deprecated user identifier")
    repository_id: Optional[str] = Field(default=None, max_length=100, description="Optional target repository UUID")
    ingestion_job_id: Optional[str] = Field(default=None, max_length=100, description="Optional ingestion job UUID")
    index_version: Optional[str] = Field(default="v1", max_length=50, description="Optional index version string")
    commit_sha: Optional[str] = Field(default=None, max_length=100, description="Optional target commit SHA")



class SearchResponse(BaseModel):
    answer: str
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    query: str
    conversation_id: str
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


@app.post("/search", response_model=SearchResponse)
async def search(search_req: SearchRequest, current_user: AuthenticatedUser = Depends(get_current_user)):
    if not db:
        raise HTTPException(status_code=500, detail="Database client is not initialized")

    verify_identity_match(current_user.id, search_req.user_id)
    user_id = current_user.id

    if search_req.conversation_id:
        conv = verify_and_get_conversation(search_req.conversation_id, user_id)
        if not conv:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation thread not found or inaccessible.",
            )
        conv_id = search_req.conversation_id
    else:
        conv_id = create_conversation(user_id, search_req.repo_filter)

    top_k = search_req.top_k or 5
    start_time = time.time()

    try:
        cached = get_cached_query(
            query=search_req.query,
            user_id=user_id,
            repo_filter=search_req.repo_filter,
            top_k=top_k,
            history=search_req.history,
        )
        if cached:
            logger.info("🟢 Cache hit! Returning instant response")
            latency_ms = (time.time() - start_time) * 1000
            log_search(search_req.query, search_req.repo_filter, cached["confidence"], latency_ms, user_id=user_id)
            add_message_to_conversation(conv_id, user_id, "user", search_req.query)
            add_message_to_conversation(conv_id, user_id, "assistant", cached["answer"], cached["sources"])
            return SearchResponse(
                answer=cached["answer"],
                sources=cached["sources"],
                query=search_req.query,
                conversation_id=conv_id,
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
        query_embedding = get_embedding(search_req.query)
        if not query_embedding:
            raise HTTPException(status_code=502, detail="Embedding service unavailable")

        logger.info("📚 Searching vector database...")
        vector_results_data = []

        repo_id = None
        active_version = None

        if search_req.repository_id:
            repo = DatabaseAdapter.get_owned_repo(db, user_id, search_req.repository_id)
            repo_id = repo["id"]
            active_version = repo.get("active_index_version", "v1")
        elif search_req.repo_filter and search_req.repo_filter != "ALL":
            try:
                repo = DatabaseAdapter.get_repo_by_name(db, user_id, search_req.repo_filter)
                repo_id = repo["id"]
                active_version = repo.get("active_index_version", "v1")
            except HTTPException as he:
                if he.status_code == status.HTTP_409_CONFLICT:
                    raise
                repo_id = None
                active_version = None

        try:
            rpc_params = {
                "query_embedding": query_embedding,
                "match_count": top_k,
                "p_user_id": user_id,
            }
            if repo_id:
                rpc_params["p_repository_id"] = repo_id
            if active_version:
                rpc_params["p_index_version"] = active_version

            search_rpc = db.rpc("search_code_snippets", rpc_params)
            res = search_rpc.execute()
            vector_results_data = res.data or []
        except Exception as rpc_e:
            logger.error("User-scoped vector search RPC failed [op=search_user_rpc, exc_type=%s]", type(rpc_e).__name__)
            raise HTTPException(
                status_code=500,
                detail="Search service error: user isolation query could not be executed safely.",
            )

        stop_words = {
            "how", "do", "did", "i", "we", "you", "what", "is", "where", "can",
            "find", "the", "a", "an", "to", "for", "in", "of", "and", "or", "my",
            "code", "file", "project", "this", "app", "use", "make", "create",
            "show", "tell", "give", "me", "get", "please", "about"
        }
        keywords = [
            word for word in re.findall(r"\b\w+\b", search_req.query.lower())
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

                    if repo_id:
                        kw_search = kw_search.eq("repository_id", repo_id)
                        if active_version:
                            kw_search = kw_search.eq("index_version", active_version)
                    elif search_req.repo_filter and search_req.repo_filter != "ALL":
                        kw_search = kw_search.eq("repo_name", search_req.repo_filter)

                    kw_search = kw_search.ilike("code_content", f"%{kw}%")
                    kw_res = kw_search.limit(top_k).execute()
                    if kw_res.data:
                        keyword_results.extend(kw_res.data)
                except Exception as kw_e:
                    logger.warning("Keyword search failed [op=keyword_search, exc_type=%s]", type(kw_e).__name__)

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

        final_results = merged_data[: top_k * 2]

        if not final_results:
            return SearchResponse(
                answer="No matching code snippets found in the indexed codebase. Try broadening your query or selecting 'All Projects'.",
                sources=[],
                query=search_req.query,
                conversation_id=conv_id,
                follow_ups=[],
                confidence=0,
            )

        max_sim = 0.0
        if vector_results_data:
            max_sim = max([float(row.get("similarity", 0)) for row in vector_results_data], default=0.0)

        base_conf = max_sim * 100
        if keyword_results:
            base_conf += 10

        confidence = min(int(base_conf), 98)
        if confidence < 30 and len(final_results) > 0:
            confidence = 50

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

        history_messages = get_conversation_messages(conv_id, user_id, limit=12)
        if history_messages:
            for msg in history_messages:
                messages.append({"role": msg["role"], "content": msg["content"]})
        elif search_req.history:
            for msg in search_req.history[-6:]:
                if isinstance(msg, dict) and "role" in msg and "content" in msg:
                    messages.append({"role": str(msg["role"]), "content": str(msg["content"])})

        messages.append({"role": "user", "content": search_req.query})

        hf_env_token = os.getenv("HF_TOKEN")
        current_key = hf_env_token.strip() if hf_env_token else ""

        if not current_key:
            logger.error("❌ HF_TOKEN is missing from environment variables!")
            return SearchResponse(
                answer="Error: AI Brain (HF_TOKEN) is not configured on the server.",
                confidence=0,
                sources=sources,
                query=search_req.query,
                conversation_id=conv_id,
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

        latency_ms = (time.time() - start_time) * 1000
        try:
            log_search(search_req.query, search_req.repo_filter, confidence, latency_ms, user_id=user_id)
            add_message_to_conversation(conv_id, user_id, "user", search_req.query)
            add_message_to_conversation(conv_id, user_id, "assistant", answer_text, sources)
            set_cached_query(
                query=search_req.query,
                user_id=user_id,
                answer=answer_text,
                sources=sources,
                confidence=confidence,
                repo_filter=search_req.repo_filter,
                top_k=top_k,
                history=search_req.history,
            )
        except Exception as log_e:
            logger.warning("Logging failed [op=post_search_logging, exc_type=%s]", type(log_e).__name__)

        return SearchResponse(
            answer=answer_text,
            sources=sources,
            query=search_req.query,
            conversation_id=conv_id,
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


@app.get("/analytics")
async def fetch_analytics(current_user: AuthenticatedUser = Depends(get_current_user)):
    try:
        return get_analytics(user_id=current_user.id)
    except Exception as e:
        logger.error("Analytics fetch failed [op=fetch_analytics, exc_type=%s]", type(e).__name__)
        return {"total_searches": 0, "avg_latency_ms": 0.0, "avg_confidence": 0.0, "recent_queries": []}


@app.get("/history")
async def fetch_history(current_user: AuthenticatedUser = Depends(get_current_user)):
    try:
        return get_chat_history(user_id=current_user.id)
    except Exception as e:
        logger.error("History fetch failed [op=fetch_history, exc_type=%s]", type(e).__name__)
        return []


@app.post("/index")
async def index_snippet(index_req: IndexRequest, current_user: AuthenticatedUser = Depends(get_current_user)):
    if not db:
        raise HTTPException(status_code=500, detail="Database client not initialized")

    verify_identity_match(current_user.id, index_req.user_id)
    user_id = current_user.id

    try:
        logger.info(f"📝 Indexing snippet for {index_req.repo_name}/{index_req.file_path}")

        if index_req.repository_id:
            repo = DatabaseAdapter.get_owned_repo(db, user_id, index_req.repository_id)
            repo_id = repo["id"]
        else:
            repo = DatabaseAdapter.resolve_user_repo(db, user_id, f"https://github.com/unknown/{index_req.repo_name}")
            repo_id = repo["id"]

        embedding = get_embedding(index_req.code_content)
        if not embedding:
            raise HTTPException(
                status_code=502, detail="Failed to generate embedding from serverless provider"
            )

        data = {
            "repo_name": index_req.repo_name,
            "file_path": index_req.file_path,
            "language": index_req.language,
            "code_content": index_req.code_content,
            "embedding": embedding,
            "source_url": index_req.source_url,
            "user_id": user_id,
            "repository_id": repo_id,
            "ingestion_job_id": index_req.ingestion_job_id,
            "index_version": index_req.index_version or "v1",
            "commit_sha": index_req.commit_sha,
        }

        result = db.table("code_snippets").insert(data).execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Database insertion failed")

        invalidate_user_repo_cache(user_id, index_req.repo_name)
        logger.info(f"✅ Successfully indexed {index_req.file_path}")
        return {"status": "success", "snippet_id": result.data[0].get("id")}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Indexing failed unexpectedly [op=index_snippet, exc_type=%s]", type(e).__name__)
        raise HTTPException(status_code=500, detail="Indexing failed due to an internal error.")


@app.post("/ingest")
async def ingest_repo(ingest_req: IngestRequest, current_user: AuthenticatedUser = Depends(get_current_user)):
    if not db:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database client is not initialized.")

    verify_identity_match(current_user.id, ingest_req.user_id)
    user_id = current_user.id

    canonical_url = validate_and_normalize_github_url(ingest_req.repo_url)
    rate_limiter.check_and_record(user_id)

    if not validate_dns_ip_safety("github.com"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository network destination is unreachable or restricted.",
        )

    concurrency_manager.acquire(user_id, canonical_url)

    try:
        # 1. Resolve repository record
        repo = DatabaseAdapter.resolve_user_repo(db, user_id, canonical_url)
        repo_id = repo["id"]
        repo_name = repo["repository_name"]

        curr_ver = repo.get("active_index_version", "v1")
        new_ver = "v2" if curr_ver == "v1" else "v1"

        # 2. Create ingestion job
        job_id = DatabaseAdapter.create_ingestion_job(db, user_id, repo_id, index_version=new_ver)
        DatabaseAdapter.update_repo_status(db, user_id, repo_id, "cloning")
        DatabaseAdapter.update_job_status(db, user_id, job_id, "cloning")

        temp_dir = tempfile.mkdtemp(prefix="cerebro_ingest_")

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

            DatabaseAdapter.update_repo_status(db, user_id, repo_id, "indexing")
            DatabaseAdapter.update_job_status(db, user_id, job_id, "indexing")

            indexer = CodeIndexer(
                repos_path=temp_dir,
                repo_url=canonical_url,
                repo_name=repo_name,
                limits=DEFAULT_LIMITS,
                repository_id=repo_id,
                ingestion_job_id=job_id,
                index_version=new_ver,
            )
            indexer.db = db
            indexer.user_id = user_id

            snippets = indexer.scan_repos()
            if not snippets:
                DatabaseAdapter.update_job_status(db, user_id, job_id, "completed", inserted_chunk_count=0)
                DatabaseAdapter.update_repo_status(db, user_id, repo_id, "ready")
                return {
                    "status": "success",
                    "message": "No indexable code found in repository",
                    "indexed_count": 0,
                }

            inserted_snippet_ids = indexer.index_snippets(snippets)
            
            # 3. Promote index version
            DatabaseAdapter.promote_index_version(db, user_id, repo_id, job_id, new_version=new_ver)
            invalidate_user_repo_cache(user_id, repo_name)

            return {
                "status": "success",
                "message": f"Successfully indexed {len(inserted_snippet_ids)} snippets from {canonical_url}",
                "indexed_count": len(inserted_snippet_ids),
                "repository_id": repo_id,
                "ingestion_job_id": job_id,
                "index_version": new_ver,
                "indexed_commit_sha": indexer.detected_commit_sha or indexer.commit_sha,
                "default_branch": indexer.detected_branch,
                "files_considered": indexer.metrics.get("files_considered", 0),
                "files_indexed": indexer.metrics.get("files_indexed", 0),
                "chunks_generated": indexer.metrics.get("chunks_generated", 0),
                "chunks_deduplicated": indexer.metrics.get("chunks_deduplicated", 0),
                "indexing_duration_ms": indexer.metrics.get("total_indexing_duration_ms", 0.0),
            }

        except HTTPException as he:
            DatabaseAdapter.fail_and_cleanup_job(db, user_id, repo_id, job_id, failure_category="HTTP_" + str(he.status_code))
            raise
        except Exception as e:
            logger.error("Ingestion failed unexpectedly [op=ingest_repo, exc_type=%s]", type(e).__name__)
            DatabaseAdapter.fail_and_cleanup_job(db, user_id, repo_id, job_id, failure_category=type(e).__name__)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Ingestion failed due to an internal error.",
            )
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
                logger.warning("Cleanup failed [op=ingest_cleanup, exc_type=%s]", type(cleanup_e).__name__)
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
        repos = DatabaseAdapter.list_owned_repos(db, target_user_id)
        repo_names = sorted(list(set([r["repository_name"] for r in repos])))
        return {"repos": repo_names, "repositories": repos}
    except Exception as e:
        logger.error("Failed to fetch user repos [op=get_user_repos, exc_type=%s]", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to fetch repositories.")


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
        repo = DatabaseAdapter.get_repo_by_name(db, target_user_id, repo_name)
        DatabaseAdapter.delete_owned_repo(db, target_user_id, repo["id"])
        invalidate_user_repo_cache(target_user_id, repo_name)
        return {"status": "success", "message": f"Repository {repo_name} deleted"}
    except Exception as e:
        logger.error("Failed to delete repo [op=delete_repo, exc_type=%s]", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to delete repository.")


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
