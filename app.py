from fastapi import FastAPI, HTTPException
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
logger = logging.getLogger(__name__)

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

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", 
        "http://localhost:3000", 
        "https://cerebro-delta-silk.vercel.app"
    ],
    allow_credentials=False,

    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models
class SearchRequest(BaseModel):
    query: str
    top_k: Optional[int] = 5
    repo_filter: Optional[str] = None  # Filter by specific repo
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


class HealthResponse(BaseModel):
    status: str
    embedder_ready: bool
    supabase_ready: bool
    hf_ready: bool


# Health check endpoint
@app.get("/health", response_model=HealthResponse)
async def health_check():
    return {
        "status": "ok",
        "supabase_ready": db is not None,
        "hf_ready": os.getenv("HF_TOKEN") is not None,
        "mode": "serverless"
    }
import requests
def get_embedding(text: str) -> list:
    hf_token = os.getenv("HF_TOKEN")
    model_id = "sentence-transformers/all-MiniLM-L6-v2"
    api_url = f"https://router.huggingface.co/hf-inference/models/{model_id}/pipeline/feature-extraction"
    headers = {"Authorization": f"Bearer {hf_token}"}
    try:
        response = requests.post(api_url, headers=headers, json={"inputs": [text]}, timeout=10)
        if response.status_code == 200:
            res = response.json()
            if isinstance(res, list) and len(res) > 0 and isinstance(res[0], list):
                return res[0]
            elif isinstance(res, list) and len(res) > 0:
                return res
            return res
        return None
    except Exception as e:
        logger.error(f"Embedding failed: {e}")
        return None

# Search endpoint
@app.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    """
    Search codebases using vector similarity + LLM generation
    """
    if not db:
        raise HTTPException(status_code=500, detail="Database not initialized")

    start_time = time.time()
    
    # Check SQLite Cache (0 LLM Tokens)
    cached = get_cached_query(request.query, request.repo_filter)
    if cached:
        logger.info("🟢 Cache hit! Returning instant response (0 tokens)")
        latency_ms = (time.time() - start_time) * 1000
        log_search(request.query, request.repo_filter, cached["confidence"], latency_ms)
        return SearchResponse(
            answer=cached["answer"],
            sources=cached["sources"],
            query=request.query,
            follow_ups=["How does this connect to other files?", "Can you explain this in more detail?", "Where is this function called?"],
            confidence=cached["confidence"]
        )

    try:
        # Step 1: Embed Query (Serverless)
        query_embedding = get_embedding(request.query)
        if not query_embedding:
            raise HTTPException(status_code=500, detail="Failed to generate embedding")

        # Step 2: Search pgvector (Semantic)
        logger.info("📚 Searching vector database...")
        
        search_query = db.rpc(
            "search_code_snippets",
            {
                "query_embedding": query_embedding, 
                "match_count": request.top_k,
                "p_user_id": request.user_id
            },
        )
        
        if request.repo_filter:
            search_query = search_query.eq("repo_name", request.repo_filter)
        
        vector_results = search_query.execute()
        
        # Step 2.5: Keyword Search (Exact Match Fallback)
        import re
        stop_words = {"how", "do", "did", "i", "we", "you", "what", "is", "where", "can", "find", "the", "a", "an", "to", "for", "in", "of", "and", "or", "my", "code", "file", "project", "this", "app", "use", "make", "create", "show", "tell", "give", "me", "get", "please", "about"}
        keywords = [word for word in re.findall(r'\b\w+\b', request.query.lower()) if word not in stop_words and len(word) > 2]
        
        # Sort keywords by length descending (longer words are usually more specific like "predictor" vs "grid")
        keywords.sort(key=len, reverse=True)
        
        keyword_results = []
        if keywords:
            top_keywords = keywords[:3]
            for kw in top_keywords:
                kw_search = db.table("code_snippets").select("id, repo_name, file_path, language, code_content, source_url")
                if request.user_id:
                    kw_search = kw_search.eq("user_id", request.user_id)
                if request.repo_filter:
                    kw_search = kw_search.eq("repo_name", request.repo_filter)
                
                if request.user_id:
                    kw_search = kw_search.eq("user_id", request.user_id)
                
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
                
        for row in vector_results.data:
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
        if vector_results.data:
            max_sim = max([float(row.get("similarity", 0)) for row in vector_results.data], default=0)
        
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

CODE CONTEXT:
{context}

RULES:
1. Use ONLY the code context provided above. Do not use outside knowledge.
2. If the answer, or a closely related concept (e.g. lap time prediction instead of grid prediction), is in the code, explain it and cite the exact file path and function name.
3. If the context is completely unrelated and contains no useful information, explicitly say "I couldn't find this in the retrieved codebase snippets."
4. Never hallucinate, guess, or make up APIs.
5. Keep your answer concise and include a brief code example if relevant.
6. Provide exactly 3 short follow-up questions the user could ask next to explore the codebase. Put them at the very end formatted EXACTLY like this:
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
                logger.error("❌ HF_TOKEN is missing from environment variables!")
                return {"answer": "Error: AI Brain (HF_TOKEN) is not configured on the server. Please check environment variables.", "confidence": 0, "sources": []}
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
            logger.error(f"HF Router Connection Error: {str(api_e)}")
            final_answer = f"Cerebro link established! Snippets retrieved, but the server could not connect to the AI router. Error: {str(api_e)}"

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

        # Log analytics and save history
        latency_ms = (time.time() - start_time) * 1000
        log_search(request.query, request.repo_filter, confidence, latency_ms)
        save_chat("default_thread", request.query, answer_text, sources)
        set_cached_query(request.query, request.repo_filter, answer_text, sources, confidence)

        return SearchResponse(
            answer=answer_text,
            sources=sources,
            query=request.query,
            follow_ups=follow_ups[:3],
            confidence=confidence
        )

    except Exception as e:
        logger.error(f"❌ Search failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

@app.get("/analytics")
async def fetch_analytics():
    return get_analytics()

@app.get("/history")
async def fetch_history():
    return get_chat_history()


# Indexing endpoint (for manual uploads)
@app.post("/index")
async def index_snippet(
    repo_name: str,
    file_path: str,
    language: str,
    code_content: str,
    source_url: Optional[str] = None,
):
    """
    Manually add a code snippet to the index
    """
    if not embedder or not db:
        raise HTTPException(status_code=500, detail="System not initialized")

    try:
        logger.info(f"📝 Indexing {repo_name}/{file_path}")

        # Generate embedding
        embedding = embedder.encode(code_content).tolist()

        # Store in Supabase
        result = db.table("code_snippets").insert(
            {
                "repo_name": repo_name,
                "file_path": file_path,
                "language": language,
                "code_content": code_content,
                "embedding": embedding,
                "source_url": source_url,
            }
        ).execute()

        logger.info(f"✅ Successfully indexed {file_path}")
        return {"status": "success", "snippet_id": result.data[0]["id"]}

    except Exception as e:
        logger.error(f"❌ Indexing failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Indexing failed: {str(e)}")


@app.post("/ingest")
async def ingest_repo(request: IngestRequest):
    """
    Clone a GitHub repo, index it for a specific user, and clean up.
    """
    temp_dir = tempfile.mkdtemp()
    try:
        logger.info(f"🚀 Ingesting repo: {request.repo_url} for user: {request.user_id}")
        
        # 1. Clone the repo
        git.Repo.clone_from(request.repo_url, temp_dir, depth=1)
        
        # 2. Initialize Indexer
        # We pass temp_dir as the repos_path. 
        # CodeIndexer.scan_repos will treat temp_dir as the root.
        indexer = CodeIndexer(repos_path=temp_dir, repo_url=request.repo_url)
        if not indexer.initialize():
            raise HTTPException(status_code=500, detail="Indexer initialization failed")
        
        indexer.user_id = request.user_id
        
        # 3. Run Indexing
        snippets = indexer.scan_repos()
        if not snippets:
             return {"status": "success", "message": "No indexable code found in repo"}
             
        indexer.index_snippets(snippets)
        
        return {
            "status": "success", 
            "message": f"Successfully indexed {len(snippets)} snippets from {request.repo_url}",
            "indexed_count": len(snippets)
        }
        
    except Exception as e:
        logger.error(f"❌ Ingestion failed: {str(e)}")
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
async def get_user_repos(user_id: str):
    """
    Fetch unique repository names indexed for this user.
    """
    if not db:
        raise HTTPException(status_code=500, detail="Database not connected")
    
    try:
        # Get unique repo names for the user
        result = db.table("code_snippets").select("repo_name").eq("user_id", user_id).execute()
        repos = sorted(list(set([r["repo_name"] for r in result.data])))
        return {"repos": repos}
    except Exception as e:
        logger.error(f"❌ Failed to fetch user repos: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/delete-repo")
async def delete_repo(repo_name: str, user_id: str):
    """
    Delete all snippets associated with a repository for a user.
    """
    try:
        db.table("code_snippets").delete().eq("repo_name", repo_name).eq("user_id", user_id).execute()
        return {"status": "success", "message": f"Repository {repo_name} deleted"}
    except Exception as e:
        logger.error(f"❌ Failed to delete repo: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/graph-data")
async def get_graph_data(user_id: str):
    """
    Generate graph nodes and links for the user's codebase.
    """
    try:
        # Fetch basic metadata for all snippets
        result = db.table("code_snippets").select("repo_name, file_path").eq("user_id", user_id).execute()
        
        nodes = []
        links = []
        seen_repos = set()
        seen_files = set()
        
        # Central Node (The User)
        nodes.append({"id": "ME", "name": "Neural Core", "val": 15, "color": "#38bdf8"})
        
        for item in result.data:
            repo = item["repo_name"]
            file = item["file_path"]
            
            # Repo Node
            if repo not in seen_repos:
                nodes.append({"id": repo, "name": repo, "val": 10, "color": "#818cf8"})
                links.append({"source": "ME", "target": repo})
                seen_repos.add(repo)
            
            # File Node
            file_id = f"{repo}:{file}"
            if file_id not in seen_files:
                nodes.append({"id": file_id, "name": file.split('/')[-1], "val": 4, "color": "#94a3b8"})
                links.append({"source": repo, "target": file_id})
                seen_files.add(file_id)
                
        return {"nodes": nodes, "links": links}
    except Exception as e:
        logger.error(f"❌ Failed to generate graph: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


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
    uvicorn.run(app, host="0.0.0.0", port=8000)
