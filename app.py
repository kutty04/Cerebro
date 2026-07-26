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

@app.on_event("startup")
async def startup_event():
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
    is_hf_ready = os.getenv("HF_TOKEN") is not None
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
    hf_token = os.getenv("HF_TOKEN")
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
    If invalid or cross-user, raises HTTPException 400.
    """
    if not repository_id and not repo_name:
        return None, None, None

    if repository_id:
        # Check user_repositories by ID & user_id
        res = db.table("user_repositories").select("id, repository_name, repo_name, active_index_version").eq("id", repository_id).eq("user_id", user_id).execute()
        if res.data and len(res.data) > 0:
            row = res.data[0]
            name = row.get("repository_name") or row.get("repo_name")
            ver = row.get("active_index_version") or "v1"
            return str(row["id"]), name, ver
        
        # Check code_snippets by repository_id & user_id
        res2 = db.table("code_snippets").select("repository_id, repo_name, index_version").eq("repository_id", repository_id).eq("user_id", user_id).limit(1).execute()
        if res2.data and len(res2.data) > 0:
            row = res2.data[0]
            return str(row.get("repository_id")), row.get("repo_name"), row.get("index_version") or "v1"
            
        raise HTTPException(status_code=400, detail="Invalid or unauthorized repository selection.")

    if repo_name:
        # Check user_repositories by name & user_id
        res = db.table("user_repositories").select("id, repository_name, repo_name, active_index_version").eq("user_id", user_id).or_(f"repository_name.eq.{repo_name},repo_name.eq.{repo_name}").execute()
        if res.data and len(res.data) > 0:
            row = res.data[0]
            return str(row["id"]), repo_name, row.get("active_index_version") or "v1"

        res2 = db.table("code_snippets").select("repository_id, repo_name, index_version").eq("repo_name", repo_name).eq("user_id", user_id).limit(1).execute()
        if res2.data and len(res2.data) > 0:
            row = res2.data[0]
            return str(row.get("repository_id") or repo_name), repo_name, row.get("index_version") or "v1"

        raise HTTPException(status_code=400, detail="Invalid or unauthorized repository selection.")

    return None, None, None


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
        
        # Step 2.5: Keyword Search (Exact Match Fallback)
        import re
        stop_words = {"how", "do", "did", "i", "we", "you", "what", "is", "where", "can", "find", "the", "a", "an", "to", "for", "in", "of", "and", "or", "my", "code", "file", "project", "this", "app", "use", "make", "create", "show", "tell", "give", "me", "get", "please", "about"}
        keywords = [word for word in re.findall(r'\b\w+\b', request.query.lower()) if word not in stop_words and len(word) > 2]
        
        # Sort keywords by length descending
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
                    keyword_results.extend(kw_res.data)

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
                confidence=0
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
            hf_env_token = os.getenv("HF_TOKEN")
            current_key = hf_env_token.strip() if hf_env_token else ""
            
            if not current_key:
                logger.info("ℹ️ HF_TOKEN not set; constructing retrieval summary response.")
                retrieved_files = list(set([s.get("file", "code file") for s in sources]))
                file_summary = ", ".join(retrieved_files[:5]) if retrieved_files else "indexed snippets"
                final_answer = f"Cerebro link established! Retrieved {len(sources)} matching code snippets from {file_summary}.\n\nTo enable full AI response generation with Llama-3.1, configure HF_TOKEN in your environment settings."
            else:
                url = "https://router.huggingface.co/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {current_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": "meta-llama/Llama-3.1-8B-Instruct",
                    "messages": messages,
                    "max_tokens": 500,
                    "temperature": 0.5
                }
                
                res = requests.post(url, headers=headers, json=payload, timeout=15)
                
                if res.status_code == 200:
                    final_answer = res.json()["choices"][0]["message"]["content"]
                else:
                    logger.error(f"HF Router Error: {res.text}")
                    final_answer = f"Cerebro link established! Snippets retrieved, but the AI router rejected the request (Status {res.status_code})."
        except Exception as api_e:
            logger.error(f"HF Router Connection Error: {type(api_e).__name__}")
            final_answer = f"Cerebro link established! Snippets retrieved, but the server could not connect to the AI router."

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
    Clone a GitHub repo, index it for authenticated_user_id, and clean up.
    """
    if request.user_id and request.user_id != authenticated_user_id:
        raise HTTPException(status_code=403, detail="User context mismatch: body user_id does not match authenticated identity.")

    target_user_id = authenticated_user_id
    temp_dir = tempfile.mkdtemp()
    try:
        logger.info("🚀 Ingesting repo for verified user")
        
        # 1. Clone the repo
        git.Repo.clone_from(request.repo_url, temp_dir, depth=1)
        
        # Extract repo name from URL
        repo_name = request.repo_url.rstrip("/").split("/")[-1].replace(".git", "")
        
        # 2. Initialize Indexer
        indexer = CodeIndexer(repos_path=temp_dir, repo_url=request.repo_url, repo_name=repo_name)
        if not indexer.initialize():
            raise HTTPException(status_code=500, detail="Indexer initialization failed")
        
        indexer.user_id = target_user_id
        
        # 3. Run Indexing
        snippets = indexer.scan_repos()
        if not snippets:
             return {"status": "success", "message": "No indexable code found in repo"}
             
        indexer.index_snippets(snippets)
        
        return {
            "status": "success", 
            "message": f"Successfully indexed {len(snippets)} snippets",
            "indexed_count": len(snippets)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Ingestion failed: {type(e).__name__} - {str(e)}")
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")
    finally:
        # 4. Cleanup with Force (Handles read-only files in .git on Windows)
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
async def get_user_repos(
    user_id: Optional[str] = None,
    authenticated_user_id: str = Depends(get_authenticated_user)
):
    """
    Fetch authoritative repository list for this user from user_repositories.
    Falls back gracefully to code_snippets if user_repositories table is unpopulated.
    """
    if user_id and user_id != authenticated_user_id:
        raise HTTPException(status_code=403, detail="User context mismatch: user_id parameter does not match authenticated identity.")

    target_user_id = authenticated_user_id
    if not db:
        raise HTTPException(status_code=500, detail="Database not connected")
    
    try:
        # 1. Authoritative lookup from user_repositories
        res_repos = db.table("user_repositories").select(
            "id, repository_name, repo_name, canonical_url, active_index_version, status"
        ).eq("user_id", target_user_id).execute()

        repos_data = res_repos.data or []
        if repos_data:
            repositories = []
            repos_names = []
            for r in repos_data:
                name = r.get("repository_name") or r.get("repo_name")
                if not name:
                    continue
                repos_names.append(name)
                repositories.append({
                    "id": str(r.get("id")),
                    "repository_name": name,
                    "repo_name": name,
                    "canonical_url": r.get("canonical_url", ""),
                    "active_index_version": r.get("active_index_version", "v1"),
                    "status": r.get("status", "ready")
                })
            repos = sorted(list(set(repos_names)))
            return {"repos": repos, "repositories": repositories}

        # Fallback to code_snippets if user_repositories empty
        result = db.table("code_snippets").select("repo_name, repository_id").eq("user_id", target_user_id).execute()
        repo_map = {}
        for r in (result.data or []):
            name = r.get("repo_name")
            r_id = r.get("repository_id")
            if name and name not in repo_map:
                repo_map[name] = {
                    "id": str(r_id or name),
                    "repository_name": name,
                    "repo_name": name,
                    "canonical_url": "",
                    "active_index_version": "v1",
                    "status": "ready"
                }

        repos = sorted(list(repo_map.keys()))
        repositories = list(repo_map.values())
        return {"repos": repos, "repositories": repositories}
    except Exception as e:
        logger.error(f"❌ Failed to fetch user repos: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/delete-repo")
async def delete_repo(
    repo_name: str, 
    user_id: Optional[str] = None,
    authenticated_user_id: str = Depends(get_authenticated_user)
):
    """
    Delete all snippets associated with a repository for a user.
    """
    if user_id and user_id != authenticated_user_id:
        raise HTTPException(status_code=403, detail="User context mismatch: user_id parameter does not match authenticated identity.")

    target_user_id = authenticated_user_id
    try:
        db.table("code_snippets").delete().eq("repo_name", repo_name).eq("user_id", target_user_id).execute()
        return {"status": "success", "message": f"Repository {repo_name} deleted"}
    except Exception as e:
        logger.error(f"❌ Failed to delete repo: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


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

        query = db.table("code_snippets").select("repo_name, file_path, repository_id").eq("user_id", target_user_id)
        if verified_repo_id:
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
