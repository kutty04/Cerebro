---
title: Cerebro Backend
sdk: docker
app_port: 7860
---

# Cerebro / CodeRAG — Semantic Search for Codebases

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](#license)  
A lightweight AI-powered semantic search engine that answers natural-language questions about your codebase and returns relevant code snippets with explanations.

Quick: Ask "How do I handle GPS in Flutter?" and get code snippets + an explanation.

---

## Table of contents

- [Why CodeRAG](#why-coderag)
- [Features](#features)
- [Tech stack](#tech-stack)
- [Quick start (local)](#quick-start-local)
  - [Prerequisites](#prerequisites)
  - [Supabase setup](#supabase-setup)
  - [Get Hugging Face token](#get-hugging-face-token)
  - [Prepare repos](#prepare-repos)
  - [Backend setup & run](#backend-setup--run)
  - [Frontend setup & run](#frontend-setup--run)
- [API](#api)
- [Deployment](#deployment)
- [Troubleshooting](#troubleshooting)
- [Performance & limits](#performance--limits)
- [Next steps & roadmap](#next-steps--roadmap)
- [Contributing](#contributing)
- [License](#license)

---

## Why CodeRAG

CodeRAG indexes code using embeddings so you can search by intent rather than keywords. It returns relevant code snippets, their source files, and an AI-generated explanation — useful for onboarding, audits, and developer productivity.

---

## Features

- Semantic natural-language search (pgvector + embeddings)
- AI-powered explanations for results
- Multi-repo indexing
- Syntax highlighting and snippet copying
- Dark mode support
- Built to work well on free-tier services (Hugging Face, Supabase)

---

## Tech stack

- Frontend: React (Vite)
- Backend: Python + FastAPI
- Database: Supabase (Postgres + pgvector)
- Embeddings: Hugging Face (all-MiniLM-L6-v2)
- LLM: Hugging Face Inference API (e.g., Mistral-7B)
- Hosting: Vercel (frontend), Hugging Face Spaces (backend / indexer)

---

## Quick start (local)

### Prerequisites

- Python 3.10+
- Node.js 18+
- Git
- Hugging Face account
- Supabase account

### Supabase setup

1. Create a project at https://supabase.com
2. In SQL editor, run:

```sql
-- Enable pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- Create code_snippets table
CREATE TABLE code_snippets (
  id BIGSERIAL PRIMARY KEY,
  file_path TEXT NOT NULL,
  language TEXT NOT NULL,
  code_content TEXT NOT NULL,
  embedding vector(384),
  repo_name TEXT NOT NULL,
  source_url TEXT,
  created_at TIMESTAMP DEFAULT NOW()
);

-- Create index for fast nearest-neighbor search
CREATE INDEX ON code_snippets USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

3. Copy Project URL and Anon Key (Settings → API)

### Get Hugging Face API key

- Create a token at https://huggingface.co/settings/tokens (read-only is sufficient)
- Copy the token for HF inference and embedding calls

### Prepare your repos

Create a staging folder and put or clone the repos to index:

```bash
mkdir ~/Desktop/coderag-data
cd ~/Desktop/coderag-data

# copy or clone the projects you want indexed
cp -r /path/to/your/project1 ./project1
git clone https://github.com/yourusername/project2 ./project2
```

---

## Backend setup & run

1. Install Python deps:

```bash
pip install -r requirements.txt
```

2. Configure environment (copy and edit):

```bash
cp .env.example .env
```

Edit `.env` to include:

```
SUPABASE_URL=your_project_url
SUPABASE_KEY=your_anon_key
HF_TOKEN=your_hf_token
REPOS_PATH=./coderag-data
```

3. Index your code:

```bash
python indexer.py
```

What the indexer does:
- Scans all repos under REPOS_PATH
- Extracts code files and metadata
- Splits files into chunks
- Generates embeddings
- Stores snippets & vectors into Supabase

Expected output:

```
✅ Successfully indexed: 245 snippets
❌ Failed: 2
```

4. Run the backend:

```bash
python app.py
```

Server runs at http://localhost:7860

Health check:

```bash
curl http://localhost:7860/health
```

---

## Frontend setup & run

1. Create React app (or use the included frontend skeleton):

```bash
npm create vite@latest coderag-frontend -- --template react
cd coderag-frontend
npm install
npm install lucide-react
```

2. Add component files:

- Copy `CodeRAG.jsx` → `src/components/CodeRAG.jsx`
- Copy `CodeRAG.css` → `src/components/CodeRAG.css`

3. Update `src/App.jsx`:

```jsx
import CodeRAG from './components/CodeRAG'

function App() {
  return <CodeRAG />
}

export default App
```

4. Configure API URL in `.env`:

```
VITE_API_URL=http://localhost:7860
```

5. Run frontend:

```bash
npm run dev
```

Visit http://localhost:5173

---

## API

POST /search
Request:

```json
{
  "query": "How do I handle errors?",
  "top_k": 5
}
```

Response:

```json
{
  "query": "How do I handle errors?",
  "answer": "Based on the code, you handle errors using try-catch blocks...",
  "sources": [
    {
      "rank": 1,
      "repo": "bus-app",
      "file": "lib/services/location_service.dart",
      "language": "dart",
      "code": "try {\n  var location = await location_service.getLocation();\n} catch (e) {\n  print('Error: $e');\n}",
      "url": "file://..."
    }
  ]
}
```

GET /health

```json
{
  "status": "ok",
  "embedder_ready": true,
  "supabase_ready": true,
  "hf_ready": true
}
```

---

## Deployment

### Backend (Hugging Face Spaces)
1. Create a new Space (Python / FastAPI).
2. Add repository files:
   - app.py
   - indexer.py (if you want periodic indexing or manual runs)
   - requirements.txt
   - .env (use the UI to add secrets — don't commit secrets to git)
3. Deploy — HF Spaces will auto-deploy.

### Frontend (Vercel)
1. Build:

```bash
npm run build
```

2. Connect repo to https://vercel.com, set env var:
```
VITE_API_URL=https://your-hf-space-url
```

3. Deploy

---

## Troubleshooting

- No matching code snippets found
  - Ensure indexer ran successfully
  - Verify Supabase: `SELECT COUNT(*) FROM code_snippets;`
  - Try a more specific query

- Failed to connect to Supabase
  - Check `.env` credentials and SUPABASE_URL format (should include .supabase.co)

- HF token invalid
  - Regenerate token at https://huggingface.co/settings/tokens

- Slow searches
  - Reduce `top_k`
  - Verify pgvector index exists
  - Adjust chunking in indexer (reduce max_lines to create more embeddings)

Quick debug commands:

- Tail indexer log:
```bash
tail -f indexer.log
```

- Test search:
```bash
curl -X POST http://localhost:7860/search -H "Content-Type: application/json" -d '{"query":"test"}'
```

---

## Performance & Limits (Free tier)

| Service       | Limit               | Notes |
|---------------|---------------------|-------|
| Supabase      | ~500MB              | ~50k vectors (est.) |
| HF Inference  | 30k calls / month   | ~1k/day |
| Vercel        | Free frontend       | - |
| HF Spaces     | 2 free spaces       | Backend + indexer |

---

## Next steps & roadmap

- Improve chunking and metadata (language, repo, function/class context)
- Add scheduled re-indexing or webhooks for repo updates
- Add user-auth + private repo indexing
- Add support for alternate embedders / LLMs

---

## Contributing

Contributions welcome! Please open issues or PRs. If you add features that affect indexing, add tests and update the README.

Suggested PR checklist:
- [ ] Tests / smoke checks for indexer
- [ ] Documentation for new config variables
- [ ] Example env files or CI for deployments

---

## CORS configuration

`CORS_ALLOWED_ORIGINS` is **required in production**. Set it to the exact frontend origin.

```bash
# Existing production frontend
CORS_ALLOWED_ORIGINS=https://cerebro-delta-silk.vercel.app

# To also allow a specific Vercel preview URL (after review):
CORS_ALLOWED_ORIGINS=https://cerebro-delta-silk.vercel.app,https://cerebro-git-branch-name.vercel.app
```

Rules enforced by the server:
- Wildcard `*` is never permitted.
- HTTPS is required for all non-localhost origins.
- Path segments (e.g. `/app`) are not allowed.
- Each Vercel preview URL must be explicitly and individually listed.
- The broad `*.vercel.app` wildcard pattern is rejected.

In **production mode** (`PRODUCTION=true`), the server will **refuse to start**
if `CORS_ALLOWED_ORIGINS` is missing, empty, or contains only invalid values.

---

## License

MIT — see LICENSE file.
