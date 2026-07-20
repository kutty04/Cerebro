# Cerebro AI — Grounded Codebase Intelligence
**Engineering Portfolio & Resume Impact Profile**

---

## Project Overview

Cerebro is an authenticated, multi-tenant repository intelligence platform built to secure developer workspaces and answer natural-language architectural questions. By utilizing a hybrid semantic search engine and LLM verification pipeline, Cerebro parses codebase content, maps structural knowledge graphs, retrieves context-grounded source code, and formats answers with verifiable citations. 

- **Core Problem Solved**: Eliminates the "context gap" in code analysis where developers must manually search across repositories or consult stale documentation. Cerebro provides secure, repository-isolated, and contextually grounded answers directly referencing the source files, reducing navigation time and onboarding overhead.
- **Visual Design**: The UI features a premium, accessible dark workspace with custom HSL-tailored colors, dynamic micro-animations (sliding panel transitions, canvas knowledge graphs, reactive states), a focus-anchored keyboard navigation system, and a semantic tabbed workspace.

---

## Technical Stack

- **Frontend**: React (Vite), CSS3 Custom Variables, PWA / Workbox, Cytoscape / custom SVG fallbacks.
- **Backend**: Python 3.11+, FastAPI (lifespan contexts, connection pooling, custom middleware).
- **Security**: Supabase Go/JSON JWT Bearer Verification, User-Repository isolation, and custom SSRF/DNS protection adapters.
- **Database**: Supabase PostgreSQL (pgvector extension, RLS schemas).
- **ML / AI**: Hugging Face Inference APIs (Embeddings + LLM pipelines).
- **Testing**: Pytest (backend), Node test runner (frontend).

---

## Engineering Impact Bullets (Resume-Ready)

- **Engineered a Secure multi-tenant repository indexing pipeline** with FastAPI and Supabase PostgreSQL (utilizing the pgvector extension), achieving isolated indexing and retrieval scoped strictly by verified Supabase JWT user credentials.
- **Hardened ingestion handlers against server-side request forgery (SSRF)**, implementing asynchronous host-level DNS validation and filtering out link-local, private, loopback, and broadcast IP ranges before starting remote git cloning processes.
- **Implemented a Service-Worker caching policy** that excludes all authenticated endpoints using hostname-based matching, ensuring zero grounded answers, repository structures, auth headers, or user analytics are cached locally.
- **Developed a responsive, accessible frontend** featuring full tabbed-navigation keyboard accessibility (role="tablist"/"tab"), skip-to-content anchors, dynamic SVG fallback representations for codebase knowledge graphs, and live screen-reader announcements (aria-live="polite").
- **Authored a comprehensive test suite** of 150+ backend and 49 frontend tests including static structural contamination audits that verify zero pytest-specific mock helpers pollute production files.

---

## Key Achievements

### 1. Unified Authentication & Decoupled Transport Mocks
Removed hardcoded pytest runtime checks (`"pytest" in sys.modules` or wrapper redirection) from production modules. Created a clean test boundary using autouse fixtures in `tests/conftest.py` that intercept and patch connection-pooled HTTP clients at their use-site, ensuring 100% production code integrity.

### 2. High-Fidelity Knowledge Graph Fallback
To ensure accessibility, the interactive codebase graph renders dynamically when WebGL/Canvas is available, but automatically exposes a semantic `<table>` fallback with aria-describedby annotations. Keyboard-only users can navigate repo modules without losing information hierarchy.

### 3. ephemerality & Privacy Compliance
Audited local database structures and documented exact retention: user query text and cached LLM responses are stored locally within an ephemeral SQLite database for a maximum of 30 days and are purged immediately on Hugging Face Spaces container recycle or user account deletion.
