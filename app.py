from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import supabase
import os
from typing import Optional
import logging
from dotenv import load_dotenv
import time
import git
import shutil
import tempfile
from pathlib import Path
import re
from indexer import CodeIndexer
from telemetry import init_db, log_search, save_chat, get_analytics, get_chat_history, get_cached_query, set_cached_query

load_dotenv(override=True)

# Global variables for lazy loading
embedder = None
db = None

logging.basicConfig(level=logging.INFO)
logger = logger = logging.getLogger(__name__)

def get_authenticated_user(authorization: Optional[str] = Header(None)) -> str:
    """
    Strict fail-closed authentication dependency.
    Verifies Bearer token with Supabase Auth and returns the verified user UUID.
    Fails with HTTP 401 if missing, malformed, invalid, or verification fails.
    """
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Authentication required: missing Authorization header."
        )

    parts = authorization.strip().split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Authentication required: malformed Authorization header. Format: Bearer <token>"
        )

    token = parts[1].strip()
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication required: empty token."
        )

    if not db:
        raise HTTPException(
            status_code=401,
            detail="Authentication failed: database auth client not initialized."
        )

    try:
        user_res = db.auth.get_user(token)
        if user_res and getattr(user_res, "user", None) and getattr(user_res.user, "id", None):
            return str(user_res.user.id)
        raise HTTPException(status_code=401, detail="Invalid or expired authentication token.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"🔒 Token verification error: {str(e)}")
        raise HTTPException(status_code=401, detail="Authentication failed: invalid or expired token.")

# Initialize FastAPI app
try:
    app = FastAPI(title="CodeRAG API")
    logger.info("🚀 Starting CodeRAG API...")
except Exception as e:
    logger.error(f"💥 Failed to initialize FastAPI: {e}")
    raise

def get_hf_token() -> str:
    """Reads and safely trims HF_TOKEN without exposing secret content."""
    raw = os.getenv("HF_TOKEN") or ""
    return raw.strip()

@app.on_event("startup")
async def startup_event():
    global db
    logger.info("🚀 Starting CodeRAG API initialization...")
    
    # Safe startup observability for HF_TOKEN configuration
    hf_configured = bool(get_hf_token())
    logger.info(f"HF generation configured: {hf_configured}")

    # 1. Init Telemetry DB
    try:
        init_db()
        logger.info("✅ Telemetry DB initialized")
    except Exception as e:
        logger.error(f"❌ Telemetry DB failed: {e}")

    # 2. Init Supabase
    try:
        url = os.getenv("SUPABASE_URL", "https://mhpnecdueyhxyhzmpcwk.supabase.co")
        key = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1ocG5lY2R1ZXloeHloem1wY3drIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3ODM3NjkyMSwiZXhwIjoyMDkzOTUyOTIxfQ.0QTcRBFZvo3El3oVd1eDxKrV2lpxdtqMifq9g3sNUrs")
        if url and key:
            db = supabase.create_client(url, key)
            logger.info("✅ Supabase client initialized")
        else:
            logger.warning("⚠️ Supabase credentials missing!")
    except Exception as e:
        logger.error(f"❌ Supabase init failed: {e}")

    logger.info("✅ System ready (Using Serverless Embeddings)")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://cerebro-delta-silk.vercel.app"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models
class SearchRequest(BaseModel):
    query: str
    top_k: Optional[int] = 5
    repo_filter: Optional[str] = None  # Filter by specific repo name
    repository_id: Optional[str] = None  # Filter by specific repository_id (UUID)
    history: Optional[list] = []  # List of dicts for multi-turn chat context
    user_id: Optional[str] = None


class IngestRequest(BaseModel):
    repo_url: str
    user_id: str


class SearchResponse(BaseModel):
    answer: str
    sources: list
    query: str
    follow_ups: list = []
    confidence: int = 0
    repository_id: Optional[str] = None
    index_version: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    embedder_ready: bool
    supabase_ready: bool
    hf_ready: bool
    mode: str

# Health check endpoint
@app.get("/health", response_model=HealthResponse)
async def health_check():
    is_hf_ready = bool(get_hf_token())
    return {
        "status": "ok",
        "embedder_ready": is_hf_ready, # In serverless mode, if HF is ready, embedder is ready
        "supabase_ready": db is not None,
        "hf_ready": is_hf_ready,
        "mode": "serverless"
    }
import requests
def _fallback_encode(text: str) -> list:
    import hashlib
    import numpy as np
    seed_bytes = hashlib.sha256(text.encode('utf-8')).digest()
    int_array = np.frombuffer(seed_bytes * 48, dtype=np.uint8)[:384]
    vec = int_array.astype(np.float32) - 128.0
    norm = np.linalg.norm(vec)
    unit_vec = vec / (norm if norm > 0 else 1.0)
    return unit_vec.tolist()

def get_embedding(text: str) -> list:
    hf_token = get_hf_token()
    model_id = "sentence-transformers/all-MiniLM-L6-v2"
    api_url = f"https://router.huggingface.co/hf-inference/models/{model_id}/pipeline/feature-extraction"
    headers = {}
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"
    try:
        response = requests.post(api_url, headers=headers, json={"inputs": [text]}, timeout=10)
        if response.status_code == 200:
            res = response.json()
            if isinstance(res, list) and len(res) > 0 and isinstance(res[0], list):
                return res[0]
            elif isinstance(res, list) and len(res) > 0:
                return res
            return res
        return _fallback_encode(text)
    except Exception as e:
        logger.error(f"Embedding failed: {e}")
        return _fallback_encode(text)

# Search endpoint
def validate_repository_ownership(user_id: str, repository_id: Optional[str] = None, repo_name: Optional[str] = None):
    """
    Validates that the supplied repository_id or repo_name belongs strictly to user_id.
    Returns (verified_repo_id, verified_repo_name, verified_active_version).
    repository_id path: checks user_repositories then code_snippets (real UUID only).
    repo_name-only path: only resolves via user_repositories; legacy repos without a row
    must be reconciled via POST /repositories/reconcile-legacy before scoped search is allowed.
    Raises HTTPException 400 if the repository cannot be verified.
    """
    if not repository_id and not repo_name:
        return None, None, None

    if repository_id:
        import uuid
        try:
            uuid.UUID(str(repository_id))
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(status_code=400, detail="Invalid or unauthorized repository selection.")

        # 1. Check user_repositories first (authoritative source)
        try:
            res = db.table("user_repositories").select("id, repository_name, repo_name, active_index_version").eq("id", repository_id).eq("user_id", user_id).execute()
            if res.data and len(res.data) > 0:
                row = res.data[0]
                name = row.get("repository_name") or row.get("repo_name")
                ver = row.get("active_index_version") or "v1"
                return str(row["id"]), name, ver
        except Exception:
            pass

        # 2. Fall back to code_snippets for repos not yet in user_repositories but with real UUID
        try:
            res2 = db.table("code_snippets").select("repository_id, repo_name, index_version").eq("repository_id", repository_id).eq("user_id", user_id).limit(1).execute()
            if res2.data and len(res2.data) > 0:
                row = res2.data[0]
                return str(row.get("repository_id")), row.get("repo_name"), row.get("index_version") or "v1"
        except Exception:
            pass

    if repo_name:
        # Only resolve via user_repositories. Legacy repos with no real user_repositories row
        # must first be reconciled via POST /repositories/reconcile-legacy.
        try:
            res = db.table("user_repositories").select("id, repository_name, repo_name, active_index_version").eq("user_id", user_id).or_(f"repository_name.eq.{repo_name},repo_name.eq.{repo_name}").execute()
            if res.data and len(res.data) > 0:
                row = res.data[0]
                return str(row["id"]), repo_name, row.get("active_index_version") or "v1"
        except Exception:
            pass

    raise HTTPException(status_code=400, detail="Invalid or unauthorized repository selection.")



@app.post("/search", response_model=SearchResponse)
async def search_code(
    request: SearchRequest,
    authenticated_user_id: str = Depends(get_authenticated_user)
):
    """
    Semantic search across indexed code snippets.
    Supports user isolation, repository-scope isolation, and conversation history.
    """
    # 1. Request body mismatch guard: Reject 403 if request.user_id exists and differs from verified authenticated_user_id
    if request.user_id and request.user_id != authenticated_user_id:
        raise HTTPException(
            status_code=403,
            detail="User context mismatch: body user_id does not match authenticated identity."
        )

    global db
    if not db:
        url = os.getenv("SUPABASE_URL", "https://mhpnecdueyhxyhzmpcwk.supabase.co")
        key = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1ocG5lY2R1ZXloeHloem1wY3drIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3ODM3NjkyMSwiZXhwIjoyMDkzOTUyOTIxfQ.0QTcRBFZvo3El3oVd1eDxKrV2lpxdtqMifq9g3sNUrs")
        if url and key:
            db = supabase.create_client(url, key)
        else:
            raise HTTPException(status_code=500, detail="Database client not initialized")

    logger.info(f"🔍 Search request: '{request.query}' | repo_filter: '{request.repo_filter}' | repo_id: '{request.repository_id}' | auth_user: '{authenticated_user_id}'")

    start_time = time.time()
    
    # 3. Validate ownership & resolve verified repository scope using authenticated_user_id
    verified_repo_id, verified_repo_name, verified_active_version = validate_repository_ownership(
        user_id=authenticated_user_id,
        repository_id=request.repository_id,
        repo_name=request.repo_filter
    )

    # Reject HTTP 400 if user selected a repository filter but ownership validation failed or repo has no resolvable UUID
    if (request.repository_id or request.repo_filter) and not verified_repo_id:
        raise HTTPException(
            status_code=400,
            detail="Invalid or unauthorized repository selection: Selected repository has no resolvable repository_id."
        )

    repo_scope = verified_repo_id or verified_repo_name or "ALL"
    index_ver = verified_active_version or "v1"

    # 4. Check cache first using ONLY authenticated_user_id (never request.user_id)
    cached = get_cached_query(
        query=request.query, 
        user_id=authenticated_user_id, 
        repo_scope=repo_scope, 
        index_version=index_ver
    )
    if cached:
        logger.info("⚡ Returning cached answer")
        latency_ms = int((time.time() - start_time) * 1000)
        log_search(request.query, repo_scope, cached["confidence"], latency_ms, user_id=authenticated_user_id)
        return SearchResponse(
            answer=cached["answer"],
            sources=cached["sources"],
            query=request.query,
            follow_ups=["How does this connect to other files?", "Can you explain this in more detail?", "Where is this function called?"],
            confidence=cached["confidence"],
            repository_id=verified_repo_id,
            index_version=verified_active_version
        )

    try:
        # Step 0: Fetch valid user_repositories UUIDs for the authenticated user
        user_repos_res = db.table("user_repositories").select("id").eq("user_id", authenticated_user_id).execute()
        user_repo_ids = set(str(r["id"]) for r in (user_repos_res.data or []) if r.get("id"))

        # If user has zero registered repos, return empty search response
        if not user_repo_ids and not verified_repo_id:
            return SearchResponse(
                answer="No matching code snippets found. Try importing a repository first.",
                sources=[],
                query=request.query,
                follow_ups=[],
                confidence=0,
                repository_id=verified_repo_id,
                index_version=verified_active_version
            )

        # Step 1: Embed Query (Serverless)
        query_embedding = get_embedding(request.query)
        if not query_embedding:
            raise HTTPException(status_code=500, detail="Failed to generate embedding")

        # Step 2: Search pgvector (Semantic)
        logger.info("📚 Searching vector database...")

        rpc_params = {
            "query_embedding": query_embedding,
            "match_count": request.top_k,
            "p_user_id": authenticated_user_id
        }

        if verified_repo_id:
            rpc_params["p_repository_id"] = verified_repo_id
        if verified_active_version:
            rpc_params["p_index_version"] = verified_active_version

        search_rpc = db.rpc("search_code_snippets", rpc_params)
        res = search_rpc.execute()
        vector_results_data = res.data or []

        # Filter out orphan snippets for All Projects scope
        if not verified_repo_id:
            vector_results_data = [
                r for r in vector_results_data
                if r.get("repository_id") in user_repo_ids
            ]

        # Step 2.5: Keyword Search (Exact Match Fallback)
        import re
        stop_words = {"how", "do", "did", "i", "we", "you", "what", "is", "where", "can", "find", "the", "a", "an", "to", "for", "in", "of", "and", "or", "my", "code", "file", "project", "this", "app", "use", "make", "create", "show", "tell", "give", "me", "get", "please", "about"}
        keywords = [word for word in re.findall(r'\b\w+\b', request.query.lower()) if word not in stop_words and len(word) > 2]

        keywords.sort(key=len, reverse=True)

        keyword_results = []
        if keywords:
            top_keywords = keywords[:3]
            for kw in top_keywords:
                kw_search = db.table("code_snippets").select("id, repo_name, file_path, language, code_content, source_url, repository_id, index_version").eq("user_id", authenticated_user_id)
                if verified_repo_id:
                    kw_search = kw_search.eq("repository_id", verified_repo_id)
                elif verified_repo_name:
                    kw_search = kw_search.eq("repo_name", verified_repo_name)

                if verified_active_version:
                    kw_search = kw_search.eq("index_version", verified_active_version)

                kw_search = kw_search.ilike("code_content", f"%{kw}%")
                kw_res = kw_search.limit(request.top_k).execute()
                if kw_res.data:
                    kw_data = kw_res.data
                    if not verified_repo_id:
                        kw_data = [r for r in kw_data if r.get("repository_id") in user_repo_ids]
                    keyword_results.extend(kw_data)

        # Merge and deduplicate
        merged_data = []
        seen_ids = set()
        
        # Prioritize exact keyword matches
        for row in keyword_results:
            if row["id"] not in seen_ids:
                merged_data.append(row)
                seen_ids.add(row["id"])
                
        for row in vector_results_data:
            if row["id"] not in seen_ids:
                merged_data.append(row)
                seen_ids.add(row["id"])
                
        # Give the LLM a larger context window (up to 8 snippets)
        final_results = merged_data[:request.top_k * 2] 

        if not final_results:
            return SearchResponse(
                answer="No matching code snippets found. Try a different query.",
                sources=[],
                query=request.query,
                follow_ups=[],
                confidence=0,
                repository_id=verified_repo_id,
                index_version=verified_active_version
            )

        # Calculate Confidence Score
        confidence = 0
        max_sim = 0
        if vector_results_data:
            max_sim = max([float(row.get("similarity", 0)) for row in vector_results_data], default=0)
        
        # Scale similarity (usually 0.5 to 0.8) to a percentage
        base_conf = max_sim * 110
        if keyword_results:
            base_conf += 15 # Boost for exact text matches
            
        confidence = min(int(base_conf), 98)
        if confidence < 30:
            confidence = 65 # Base floor if math scales poorly

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
        
        system_prompt = f"""You are a master code expert connected to the Cerebro neural link. Answer the user's question using ONLY the provided code snippets.

REPOSITORIES RETRIEVED: Scope = {repo_scope} (Filter requested: {request.repo_filter or request.repository_id or 'ALL'})

CODE CONTEXT:
{context}

RULES:
1. Use ONLY the code context provided above. Do not use outside knowledge or hallucinate.
2. Grounding & Scope Analysis:
   - Base your understanding of the repository structure solely on the retrieved snippets.
   - When asked a broad question about a repository as a whole (e.g. "What is the concept of this project?"), analyze all retrieved code snippets across the repository.
   - If the retrieved evidence indicates a developer portfolio, multi-component workspace, or project repository containing sub-modules, describe it accurately as a portfolio/workspace showcasing those projects. Do NOT mistake a single sub-module or sub-project for the concept of the entire repository.
3. If the answer or a closely related concept is in the code, explain it and cite the exact file path and function name.
4. If the context is completely unrelated and contains no useful information, explicitly say "I couldn't find this in the retrieved codebase snippets."
5. Keep your answer concise and include a brief code example if relevant.
6. Provide exactly 3 short follow-up questions at the very end formatted EXACTLY like this:
FOLLOW_UPS:
- Question 1
- Question 2
- Question 3"""

        messages = [{"role": "system", "content": system_prompt}]
        
        # Append up to the last 4 chat turns for context memory
        if request.history:
            for msg in request.history[-4:]:
                messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
                
        # Append the current query
        messages.append({"role": "user", "content": request.query})

        import requests
        try:
            hf_token = get_hf_token()
            
            if not hf_token:
                logger.info("ℹ️ HF_TOKEN not set; constructing retrieval summary response.")
                retrieved_files = list(set([s.get("file", "code file") for s in sources]))
                file_summary = ", ".join(retrieved_files[:5]) if retrieved_files else "indexed snippets"
                final_answer = f"Cerebro link established! Retrieved {len(sources)} matching code snippets from {file_summary}.\n\nTo enable full AI response generation with Llama-3.1, configure HF_TOKEN in your environment settings."
            else:
                url = "https://router.huggingface.co/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {hf_token}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": "meta-llama/Llama-3.1-8B-Instruct",
                    "messages": messages,
                    "max_tokens": 500,
                    "temperature": 0.5
                }
                
                res = requests.post(url, headers=headers, json=payload, timeout=(5, 45))
                
                if res.status_code == 200:
                    final_answer = res.json()["choices"][0]["message"]["content"]
                else:
                    logger.error(f"HF Router Error: Status {res.status_code}")
                    final_answer = f"Cerebro link established! Retrieved {len(sources)} matching code snippets, but AI response generation is currently unavailable (Status {res.status_code})."
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout) as to_e:
            logger.error(f"HF Router Connection Timeout: {type(to_e).__name__}")
            final_answer = f"Cerebro link established! Retrieved {len(sources)} matching code snippets, but AI response generation timed out."
        except Exception as api_e:
            logger.error(f"HF Router Connection Error: {type(api_e).__name__}")
            final_answer = f"Cerebro link established! Retrieved {len(sources)} matching code snippets, but AI response generation is currently unavailable."

        # Parse out follow-up questions
        answer_text = final_answer.strip()
        follow_ups = []
        if "FOLLOW_UPS:" in answer_text:
            parts = answer_text.split("FOLLOW_UPS:")
            answer_text = parts[0].strip()
            follow_ups_text = parts[1].strip()
            
            for line in follow_ups_text.split('\n'):
                line = line.strip()
                if line.startswith('-'):
                    follow_ups.append(line.lstrip('- ').strip())

        # Log analytics and save history with authenticated_user_id
        latency_ms = (time.time() - start_time) * 1000
        log_search(request.query, repo_scope, confidence, latency_ms, user_id=authenticated_user_id)
        save_chat("default_thread", request.query, answer_text, sources, user_id=authenticated_user_id)
        set_cached_query(
            query=request.query,
            user_id=authenticated_user_id,
            repo_scope=repo_scope,
            answer=answer_text,
            sources=sources,
            confidence=confidence,
            index_version=index_ver
        )

        return SearchResponse(
            answer=answer_text,
            sources=sources,
            query=request.query,
            follow_ups=follow_ups[:3],
            confidence=confidence,
            repository_id=verified_repo_id,
            index_version=verified_active_version
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Search failed: {type(e).__name__} - {str(e)}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

@app.get("/analytics")
async def fetch_analytics(authenticated_user_id: str = Depends(get_authenticated_user)):
    return get_analytics(user_id=authenticated_user_id)

@app.get("/history")
async def fetch_history(authenticated_user_id: str = Depends(get_authenticated_user)):
    return get_chat_history(user_id=authenticated_user_id)


# Indexing endpoint (for manual uploads)
@app.post("/index")
async def index_snippet(
    repo_name: str,
    file_path: str,
    language: str,
    code_content: str,
    source_url: Optional[str] = None,
    user_id: Optional[str] = None,
    authenticated_user_id: str = Depends(get_authenticated_user)
):
    """
    Manually add a code snippet to the index for verified authenticated_user_id
    """
    if user_id and user_id != authenticated_user_id:
        raise HTTPException(status_code=403, detail="User context mismatch: user_id parameter does not match authenticated identity.")

    target_user_id = authenticated_user_id
    if not embedder or not db:
        raise HTTPException(status_code=500, detail="System not initialized")

    try:
        logger.info("📝 Indexing snippet for verified user")

        # Generate embedding
        embedding = embedder.encode(code_content).tolist()

        # Store in Supabase bound to target_user_id
        result = db.table("code_snippets").insert(
            {
                "user_id": target_user_id,
                "repo_name": repo_name,
                "file_path": file_path,
                "language": language,
                "code_content": code_content,
                "embedding": embedding,
                "source_url": source_url,
            }
        ).execute()

        logger.info("✅ Successfully indexed snippet")
        return {"status": "success", "snippet_id": result.data[0]["id"]}

    except Exception as e:
        logger.error(f"❌ Indexing failed: {type(e).__name__}")
        raise HTTPException(status_code=500, detail=f"Indexing failed: {type(e).__name__}")


@app.post("/ingest")
async def ingest_repo(
    request: IngestRequest,
    authenticated_user_id: str = Depends(get_authenticated_user)
):
    """
    Clone a GitHub repo, register a real UUID user_repositories record for authenticated_user_id,
    index all snippets using that real UUID as repository_id, and mark status as ready.
    Idempotent: if snippets already exist for this repository, finalizes status to ready without
    re-cloning or duplicate snippet indexing.
    """
    if request.user_id and request.user_id != authenticated_user_id:
        raise HTTPException(status_code=403, detail="User context mismatch: body user_id does not match authenticated identity.")

    target_user_id = authenticated_user_id

    # 1. Derive repository metadata from submitted repo_url BEFORE indexing
    provider = "github"
    repo_name = request.repo_url.rstrip("/").split("/")[-1].replace(".git", "")

    m = re.search(r'github\.com[:/]([^/]+)/([^/#\?]+)', request.repo_url)
    if m:
        repo_owner = m.group(1)
        clean_repo = m.group(2).replace('.git', '')
        canonical_url = f"https://github.com/{repo_owner}/{clean_repo}"
    else:
        raise HTTPException(status_code=400, detail="Invalid or unparseable GitHub repository URL.")

    if not db:
        raise HTTPException(status_code=500, detail="Database not connected")

    # 2. Register/Create or retrieve existing user_repositories record
    try:
        existing = db.table("user_repositories").select("id, status").eq("user_id", target_user_id).or_(f"repository_name.eq.{repo_name},repo_name.eq.{repo_name}").execute()
        if not existing.data:
            ins_res = db.table("user_repositories").insert({
                "user_id": target_user_id,
                "provider": provider,
                "repository_owner": repo_owner,
                "repository_name": repo_name,
                "repo_name": repo_name,
                "canonical_url": canonical_url,
                "status": "indexing",
                "active_index_version": "v1"
            }).execute()

            if not ins_res.data or len(ins_res.data) == 0:
                raise HTTPException(status_code=500, detail="Failed to create repository record in user_repositories.")
            real_repo_id = str(ins_res.data[0]["id"])
        else:
            real_repo_id = str(existing.data[0]["id"])
            db.table("user_repositories").update({
                "provider": provider,
                "repository_owner": repo_owner,
                "canonical_url": canonical_url
            }).eq("id", real_repo_id).execute()

        # 3. Check for existing indexed snippets (Idempotency / Stuck Repository Resume)
        # Strictly query by user_id and real_repo_id UUID only. Never adopt or backfill by repo_name.
        res_snippets = db.table("code_snippets").select("id").eq("user_id", target_user_id).eq("repository_id", real_repo_id).execute()
        existing_snippets = res_snippets.data or []

        if existing_snippets and len(existing_snippets) > 0:
            logger.info(f"ℹ️ Found {len(existing_snippets)} existing snippets for repository_id '{real_repo_id}'. Finalizing repository status to ready.")

            # Mark status = ready
            try:
                db.table("user_repositories").update({
                    "status": "ready"
                }).eq("id", real_repo_id).execute()
            except Exception as final_err:
                logger.error(f"❌ Finalization update failed: {type(final_err).__name__}")
                raise HTTPException(status_code=500, detail=f"Ingestion finalization failed: {type(final_err).__name__}")

            return {
                "status": "success",
                "message": f"Successfully finalized {len(existing_snippets)} existing snippets",
                "indexed_count": len(existing_snippets),
                "repository_id": real_repo_id
            }

    except HTTPException:
        raise
    except Exception as repo_err:
        logger.error(f"❌ Failed to register repository for ingestion: {type(repo_err).__name__}")
        raise HTTPException(status_code=500, detail=f"Repository registration failed: {type(repo_err).__name__}")

    # Set status = indexing before cloning & indexing
    try:
        db.table("user_repositories").update({"status": "indexing"}).eq("id", real_repo_id).execute()
    except Exception:
        pass

    temp_dir = tempfile.mkdtemp()
    try:
        logger.info(f"🚀 Ingesting repo '{repo_name}' with UUID '{real_repo_id}' for verified user")

        # 4. Clone the repo
        git.Repo.clone_from(request.repo_url, temp_dir, depth=1)

        # 5. Initialize Indexer
        indexer = CodeIndexer(repos_path=temp_dir, repo_url=request.repo_url, repo_name=repo_name)
        if not indexer.initialize():
            try:
                db.table("user_repositories").update({"status": "failed"}).eq("id", real_repo_id).execute()
            except Exception:
                pass
            raise HTTPException(status_code=500, detail="Indexer initialization failed")

        indexer.user_id = target_user_id
        indexer.repository_id = real_repo_id

        # 6. Run Indexing
        snippets = indexer.scan_repos()
        if not snippets:
            db.table("user_repositories").update({"status": "ready"}).eq("id", real_repo_id).execute()
            return {"status": "success", "message": "No indexable code found in repo", "repository_id": real_repo_id}

        for snippet in snippets:
            snippet["repository_id"] = real_repo_id

        indexer.index_snippets(snippets)

        # 7. Mark status = ready ONLY after indexing succeeds
        try:
            db.table("user_repositories").update({
                "status": "ready"
            }).eq("id", real_repo_id).execute()
        except Exception as final_patch_err:
            logger.error(f"❌ Ingestion finalization status update failed: {type(final_patch_err).__name__}")
            raise HTTPException(status_code=500, detail=f"Ingestion finalization failed: {type(final_patch_err).__name__}")

        return {
            "status": "success",
            "message": f"Successfully indexed {len(snippets)} snippets",
            "indexed_count": len(snippets),
            "repository_id": real_repo_id
        }

    except HTTPException:
        try:
            db.table("user_repositories").update({"status": "failed"}).eq("id", real_repo_id).execute()
        except Exception:
            pass
        raise
    except Exception as e:
        logger.error(f"❌ Ingestion failed: {type(e).__name__}")
        try:
            db.table("user_repositories").update({"status": "failed"}).eq("id", real_repo_id).execute()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {type(e).__name__}")
    finally:
        # Cleanup temp directory
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
            logger.warning(f"⚠️ Cleanup failed for {temp_dir}: {type(cleanup_e).__name__}")


@app.get("/user-repos")
async def get_user_repos(
    user_id: Optional[str] = None,
    authenticated_user_id: str = Depends(get_authenticated_user)
):
    """
    Read-only. Fetches only repository records from user_repositories belonging
    strictly to the authenticated user. Performs zero merging of code_snippets and zero writes.
    """
    if user_id and user_id != authenticated_user_id:
        raise HTTPException(status_code=403, detail="User context mismatch: user_id parameter does not match authenticated identity.")

    target_user_id = authenticated_user_id
    if not db:
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        res_repos = db.table("user_repositories").select(
            "id, repository_name, repo_name, repository_owner, canonical_url, active_index_version, status"
        ).eq("user_id", target_user_id).execute()
        repos_data = res_repos.data or []

        all_objs = []
        for r in repos_data:
            r_id = str(r.get("id")) if r.get("id") else None
            name = r.get("repository_name") or r.get("repo_name")
            if not name or not r_id:
                continue
            all_objs.append({
                "id": r_id,
                "repository_name": name,
                "repo_name": name,
                "repository_owner": r.get("repository_owner", ""),
                "canonical_url": r.get("canonical_url", ""),
                "active_index_version": r.get("active_index_version", "v1"),
                "status": r.get("status", "active"),
                "legacy": False
            })

        all_names = sorted(list(set(r["repo_name"] for r in all_objs)))
        return {"repos": all_names, "repositories": all_objs}
    except Exception as e:
        logger.error(f"❌ Failed to fetch user repos: {type(e).__name__}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch user repos: {type(e).__name__}")


@app.post("/delete-repo")
async def delete_repo(
    repository_id: Optional[str] = None,
    repo_name: Optional[str] = None, 
    user_id: Optional[str] = None,
    authenticated_user_id: str = Depends(get_authenticated_user)
):
    """
    Delete a repository strictly by authenticated target_user_id and valid repository_id UUID.
    No repo-name-based lookups or deletes are performed.
    """
    if user_id and user_id != authenticated_user_id:
        raise HTTPException(status_code=403, detail="User context mismatch: body user_id does not match authenticated identity.")

    target_user_id = authenticated_user_id

    # 1. Require a valid UUID for repository_id
    if not repository_id:
        raise HTTPException(status_code=400, detail="Missing required repository_id UUID parameter.")

    import uuid
    try:
        uuid.UUID(str(repository_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid repository_id UUID format.")

    # 2. Verify ownership in user_repositories
    if not db:
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        user_repo_rows = db.table("user_repositories").select("id, repository_name, repo_name").eq("id", repository_id).eq("user_id", target_user_id).execute()
        if not user_repo_rows.data or len(user_repo_rows.data) == 0:
            raise HTTPException(status_code=404, detail="Repository not found or unauthorized deletion.")

        # 3. Delete strictly by (user_id, repository_id)
        db.table("code_snippets").delete().eq("user_id", target_user_id).eq("repository_id", repository_id).execute()
        db.table("user_repositories").delete().eq("user_id", target_user_id).eq("id", repository_id).execute()

        display_name = repo_name or user_repo_rows.data[0].get("repository_name") or user_repo_rows.data[0].get("repo_name") or "Repository"
        return {"status": "success", "message": f"Repository {display_name} deleted", "repository_id": repository_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Failed to delete repo: {type(e).__name__}")
        raise HTTPException(status_code=500, detail=f"Failed to delete repo: {type(e).__name__}")


@app.get("/graph-data")
async def get_graph_data(
    user_id: Optional[str] = None, 
    repo_name: Optional[str] = None, 
    repository_id: Optional[str] = None,
    authenticated_user_id: str = Depends(get_authenticated_user)
):
    """
    Generate graph nodes and links for the user's codebase, strictly scoped to verified repository when requested.
    """
    if user_id and user_id != authenticated_user_id:
        raise HTTPException(status_code=403, detail="User context mismatch: user_id parameter does not match authenticated identity.")

    target_user_id = authenticated_user_id
    try:
        verified_repo_id, verified_repo_name, verified_ver = validate_repository_ownership(
            user_id=target_user_id,
            repository_id=repository_id,
            repo_name=repo_name
        )

        user_repos_res = db.table("user_repositories").select("id").eq("user_id", target_user_id).execute()
        user_repo_ids = set(str(r["id"]) for r in (user_repos_res.data or []) if r.get("id"))

        if not verified_repo_id and not verified_repo_name:
            if not user_repo_ids:
                return {"nodes": [], "links": []}

        query = db.table("code_snippets").select("repo_name, file_path, repository_id").eq("user_id", target_user_id)
        if verified_repo_id:
            try:
                res_check = db.table("code_snippets").select("id").eq("user_id", target_user_id).eq("repository_id", verified_repo_id).limit(1).execute()
                if res_check.data and len(res_check.data) > 0:
                    query = query.eq("repository_id", verified_repo_id)
                elif verified_repo_name:
                    query = query.eq("repo_name", verified_repo_name)
                else:
                    query = query.eq("repository_id", verified_repo_id)
            except Exception:
                if verified_repo_name:
                    query = query.eq("repo_name", verified_repo_name)
                else:
                    query = query.eq("repository_id", verified_repo_id)
        elif verified_repo_name:
            query = query.eq("repo_name", verified_repo_name)

        result = query.execute()

        nodes = []
        links = []
        seen_repos = set()
        seen_files = set()

        # Central Node (The User)
        nodes.append({"id": "ME", "name": "Neural Core", "val": 15, "color": "#38bdf8"})

        for item in (result.data or []):
            # Exclude orphan snippets in All Projects scope
            if not verified_repo_id and not verified_repo_name:
                if str(item.get("repository_id")) not in user_repo_ids:
                    continue

            repo = item.get("repo_name")
            file = item.get("file_path")
            if not repo or not file:
                continue

            # Repo Node
            if repo not in seen_repos:
                nodes.append({"id": repo, "name": repo, "val": 10, "color": "#818cf8"})
                links.append({"source": "ME", "target": repo})
                seen_repos.add(repo)

            # File Node
            file_id = f"{repo}/{file}"
            if file_id not in seen_files:
                nodes.append({"id": file_id, "name": file, "val": 5, "color": "#34d399"})
                links.append({"source": repo, "target": file_id})
                seen_files.add(file_id)

        return {"nodes": nodes, "links": links}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Failed to generate graph: {str(e)}")
        return {"nodes": [], "links": []}


# Root endpoint
@app.get("/")
async def root():
    return {
        "name": "CodeRAG API",
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "search": "/search (POST)",
            "index": "/index (POST)",
        },
    }


if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 7860))
    uvicorn.run(app, host="0.0.0.0", port=port)
