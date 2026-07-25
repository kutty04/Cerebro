# Cerebro AI — Release Candidate Checklist (Manual Deployments)
**Manual Deployment and Verification Procedure**

This document details the checklist to be followed manually by human operators before merging, releasing, or deploying a release candidate of Cerebro.

---

## 1. Branch Verification
- [ ] Verify target branch is `enhancement/cerebro-renovation`.
- [ ] Confirm `main` branch matches `43f71488aa9747e464840f242f20f7a977bc4515` exactly.
- [ ] Ensure local working directory is completely clean (`git status --short` is empty).
- [ ] Review commits to verify no force-pushing occurred. In case of rollback requirements, proceed via `git revert`.

---

## 2. Supabase Migration Execution
- [ ] **Step A: Base Setup**: If starting fresh, copy and run SQL from `supabase_setup.sql`.
- [ ] **Step B: Multi-Tenant & Graph support**: Execute SQL from `supabase_phase5_migration.sql` to register additional vector similarity indexes, repository-scoping attributes, and ingestion logs.
- [ ] **Step C: Row-Level Security (RLS)**: Execute SQL from `supabase_rls_migration.sql` to configure Row-Level Security policies on `code_snippets` and `repositories` tables:
  ```sql
  ALTER TABLE code_snippets ENABLE ROW LEVEL SECURITY;
  -- Verify policies restrict reads/writes strictly to authenticated creators.
  ```
- [ ] Verify DB connections are secure and pgvector functions are accessible.

---

## 3. Environment Variable Provisioning
Verify the following production secrets are securely provisioned (never commit `.env` file):

### Backend (Hugging Face Space / Docker Environment)
- [ ] `SUPABASE_URL`: Fully qualified Supabase project API endpoint.
- [ ] `SUPABASE_SERVICE_ROLE_KEY`: Service-role key for backend ingestion bypass.
- [ ] `HF_TOKEN`: Valid Hugging Face API access token (read role).
- [ ] `CORS_ALLOWED_ORIGINS`: Set explicitly to `https://cerebro-delta-silk.vercel.app`.
- [ ] `PRODUCTION`: Set to `true` (enables startup validation checks).
- [ ] `PORT`: Set to `7860`.

### Frontend (Vercel Build Environment)
- [ ] `VITE_API_URL`: Set to the deployed Hugging Face Space URL (e.g., `https://username-coderag.hf.space`).

---

## 4. Pre-Release Test Execution
- [ ] Execute the full backend test suite in a **Linux/Docker environment** to run the symlink safety checks:
  ```bash
  # Inside a Linux container:
  python -m pytest tests/ -v
  ```
- [ ] Confirm `test_symlink_escape_blocked` passes (requires Windows developer privileges or standard Linux symlink access).
- [ ] Execute frontend test suite:
  ```bash
  npm run test
  ```
- [ ] Compile frontend production build to verify bundles are free from lint/compilation errors:
  ```bash
  npm run build
  ```

---

## 5. Deployment Actions
- [ ] Deploy the frontend to **Vercel** (`npm run build` and connect repo).
- [ ] Deploy the backend to **Hugging Face Spaces** as a custom Docker Space or deploy to a container runtime using the repository's `Dockerfile`.

---

## 6. Post-Deployment Smoke Probes
Execute the following verification probes against the live staging/production deployment:
- [ ] **Health Endpoint**: Request `https://<backend-host>/health`. Confirm response code is `200 OK` and reports active service configurations.
- [ ] **Readiness Check**: Request `https://<backend-host>/readiness`. Verify all critical systems (database, embeddings, LLM) report connection success.
- [ ] **CORS Preflight Verification**: Submit an OPTIONS preflight request to `/search` with Origin matching the production Vercel frontend. Check headers:
  - `Access-Control-Allow-Origin` matches the exact frontend URL (never wildcard `*`).
  - `Access-Control-Allow-Headers` lists `Authorization` and `Content-Type`.
  - `Access-Control-Allow-Credentials` is `true`.
- [ ] **Service-Worker Verification**: Run the PWA application and inspect Chrome DevTools Application tab. Confirm no authenticated API requests, `Authorization` headers, or query contents are stored in browser Cache Storage.
- [ ] **User-Isolation Verification**: 
  1. Log in with User A, ingest a private or public repository, and verify search queries return valid results.
  2. Log in with User B, issue a search query targeting User A's repository, and verify the backend rejects or yields zero context results (isolation verified).
- [ ] **No-Evidence (Grounding) Behavior**: Query for terms not present in the indexed repositories (e.g. "Explain how nuclear fusion works in this codebase"). Verify the LLM response gracefully states that no code evidence was retrieved instead of hallucinating.
