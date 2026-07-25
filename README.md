---
title: Cerebro Workspace
sdk: docker
app_port: 7860
---

# Cerebro AI — Grounded Codebase Intelligence

Cerebro is an authenticated, multi-tenant repository intelligence workspace and semantic search engine that resolves the "context gap" in software analysis. Instead of manually parsing multiple repositories or scanning stale docs, developers query Cerebro in natural language (e.g. *"How do we handle rate limits in our API client?"*) and receive grounded explanations paired with verified source citations, module relationships, and retrieval stats.

---

## Technical Features

- **Multi-Tenant Ingestion Security**: Authenticated git-ingestion pipeline verifying Supabase JWT user credentials before cloning repositories.
- **SSRF Ingestion Protection**: Asynchronous resolver validation checking destination hostnames and rejecting link-local, loopback, private, and broadcast IP subnets.
- **Vector-Based Indexing**: Text chunking and embedding pipelines utilizing Hugging Face models (`all-MiniLM-L6-v2`) and Supabase PostgreSQL with the `pgvector` extension.
- **Grounded Retrieval Pipeline**: Isolated query matching scoped to the active user's repository credentials, ensuring data privacy and zero cross-tenant leakages.
- **Citational Answers & Evidence**: Answers are enriched with precise source file links, line ranges, retrieval execution timing, and match confidence scores.
- **Accessible Responsive UI**: Custom HSL-tailored dark workspace featuring full keyboard tabbed-navigation (`role="tablist"`), skip-to-content anchors, and high-fidelity cytoscape-based knowledge graph fallbacks.
- **Service Worker Cache Privacy**: Router rules matching the backend origin (`VITE_API_URL`) to handle all API responses, auth headers, and queries as `NetworkOnly` (never cached in browser storage).

---

## Tech Stack

- **Frontend**: React, CSS3 Variables, PWA / Workbox, Cytoscape.
- **Backend**: FastAPI (Python 3.11+), requests (Session Connection Pooling), GitPython.
- **Database**: Supabase PostgreSQL + `pgvector`.
- **Inference Engines**: Hugging Face Inference API.
- **Testing**: Pytest (Python), Node test runner (JS).

---

## Repository Structure

```
├── .env.example
├── .gitignore
├── Dockerfile                  # Ephemeral container definition (Port 7860)
├── app.py                      # Main authenticated FastAPI API
├── db_adapter.py               # Supabase database access adapter
├── indexer.py                  # Chunking and embedding generation pipeline
├── ingestion_validator.py      # Git cloning, SSRF, DNS & rate-limit guards
├── requirements.txt            # Python backend dependencies
├── telemetry.py                # Ephemeral local database logging & purging
├── security/
│   └── auth.py                 # JWT verify and current user resolver
├── docs/
│   ├── portfolio.md            # Resume bullets and architectural details
│   ├── release-checklist.md    # Manual deployment checks
│   └── screenshots/
│       └── README.md           # Visual layout captures checklist
├── tests/                      # Python backend test suite
└── coderag-frontend/           # React workspace client directory
```

---

## Local Setup

### 1. Prerequisites
Ensure you have Python 3.11+ and Node.js installed. Create a Supabase project and enable `pgvector`.

### 2. Backend Setup
1. Copy the environment template:
   ```bash
   cp .env.example .env
   ```
2. Populate `.env` with your Supabase credentials, Hugging Face Token, and workspace variables (never commit this file to git).
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Run the API server:
   ```bash
   python app.py
   # Runs locally at http://localhost:7860
   ```

### 3. Frontend Setup
1. Navigate to the frontend workspace:
   ```bash
   cd coderag-frontend
   ```
2. Install client packages:
   ```bash
   npm install
   ```
3. Run the development server:
   ```bash
   npm run dev
   # Runs locally at http://localhost:3000
   ```

### 4. Running Tests
- **Backend**:
  ```bash
  python -m pytest tests/
  ```
- **Frontend**:
  ```bash
  npm run test
  ```

---

## Environment Variables

| Variable | Scope | Required | Description / Value |
|---|---|---|---|
| `SUPABASE_URL` | Server-only | Yes | Project URL (e.g. `https://your-proj.supabase.co`) |
| `SUPABASE_SERVICE_ROLE_KEY` | Server-only | Yes | Service Role Secret |
| `HF_TOKEN` | Server-only | Yes | Hugging Face Access Token |
| `CORS_ALLOWED_ORIGINS` | Server-only | Yes (Prod) | Comma-separated allowed HTTPS origins (Vercel) |
| `PRODUCTION` | Server-only | No | Enforces startup checks (`true` / `false`) |
| `PORT` | Server-only | No | Backend port (default `7860`) |
| `VITE_API_URL` | Frontend-only | Yes | Backend target URL (e.g. `http://localhost:7860`) |

---

## Deployment Guidelines

1. **Supabase Schemas**: Run `supabase_setup.sql`, `supabase_phase5_migration.sql`, and `supabase_rls_migration.sql` in sequence.
2. **CORS Configuration**: Configure `CORS_ALLOWED_ORIGINS` explicitly in production to `https://cerebro-delta-silk.vercel.app`. The server rejects wildcards (`*`) and will fail to start if empty.
3. **Vercel Frontend**: Connect the `coderag-frontend` directory, set the `VITE_API_URL` variable, and trigger the build (`npm run build`).
4. **Hugging Face Space / Docker**: Configure space container targeting port `7860` with environment secrets populated.

---

## Security, Privacy & Telemetry Model

- **Authentication**: Extracted and verified bearer tokens against Supabase JWT signatures. No client-side database credentials.
- **User Isolation**: Row-Level Security restricts all query retrieval and snippet generation to matching User UUIDs.
- **Privacy ephemerality**: Local SQLite telemetries cache SHA-256 query hashes, opaque user UUIDs, and query text strictly for 30 days. All stored data is ephemeral and wiped upon Space restart or user-scoped repository deletion.
- **Cache Policy**: The service-worker origin-based matching rejects local runtime caching for all authenticated calls. Sensitive data never hits browser disk caches.

---

## Known Limitations

- **Symlink Test Environment**: Pytest `test_symlink_escape_blocked` requires Windows developer mode privileges; full determinism must be verified in Linux/Docker runtimes.
- **Client Deployment**: Current deployment setups exist as Release Candidates; full verification on Hugging Face Spaces production endpoints requires manual configuration.
- **Syntax Parsing**: Advanced AST line slicing is limited for non-standard or custom DSL languages.
- **Free-Tier Limits**: External Hugging Face APIs are subject to standard rate-limiting.
