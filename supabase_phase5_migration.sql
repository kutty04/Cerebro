-- ==============================================================================
-- CEREBRO AI - PHASE 5A SUPABASE STABLE REPOSITORY MODEL & RLS MIGRATION ARTIFACT
-- Status: REVIEWED / NOT YET APPLIED TO LIVE PRODUCTION
-- Instructions: Run via Supabase Dashboard SQL Editor when ready.
-- ==============================================================================

SET search_path = public, pg_temp;

-- ==========================================
-- STAGE 1: ADDITIVE SCHEMA UPDATES
-- ==========================================

-- 1. Create User Repositories Table
CREATE TABLE IF NOT EXISTS user_repositories (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    provider text DEFAULT 'github',
    repository_owner text NOT NULL,
    repository_name text NOT NULL,
    canonical_url text NOT NULL,
    default_branch text DEFAULT 'main',
    indexed_commit_sha text,
    active_index_version text DEFAULT 'v1',
    status text DEFAULT 'pending', -- pending, cloning, indexing, ready, failed, deleting
    indexed_file_count integer DEFAULT 0,
    indexed_chunk_count integer DEFAULT 0,
    last_error_category text,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now(),
    last_indexed_at timestamptz DEFAULT now(),
    CONSTRAINT user_repo_unique UNIQUE(user_id, canonical_url)
);

-- 2. Create Ingestion Jobs Table
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    repository_id uuid NOT NULL REFERENCES user_repositories(id) ON DELETE CASCADE,
    status text DEFAULT 'pending', -- pending, cloning, indexing, completed, failed
    started_at timestamptz DEFAULT now(),
    completed_at timestamptz,
    updated_at timestamptz DEFAULT now(),
    failure_category text,
    inserted_chunk_count integer DEFAULT 0,
    index_version text DEFAULT 'v1',
    commit_sha text
);

-- 3. Extend code_snippets Table with Scoping and Versioning Columns
DO $$
BEGIN
    -- Add repository_id column if not exists
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='code_snippets' AND column_name='repository_id') THEN
        ALTER TABLE code_snippets ADD COLUMN repository_id uuid REFERENCES user_repositories(id) ON DELETE CASCADE;
    END IF;

    -- Add ingestion_job_id column if not exists
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='code_snippets' AND column_name='ingestion_job_id') THEN
        ALTER TABLE code_snippets ADD COLUMN ingestion_job_id uuid REFERENCES ingestion_jobs(id) ON DELETE SET NULL;
    END IF;

    -- Add index_version column if not exists
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='code_snippets' AND column_name='index_version') THEN
        ALTER TABLE code_snippets ADD COLUMN index_version text DEFAULT 'v1';
    END IF;

    -- Add commit_sha column if not exists
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='code_snippets' AND column_name='commit_sha') THEN
        ALTER TABLE code_snippets ADD COLUMN commit_sha text;
    END IF;

    -- Add content_hash column if not exists
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='code_snippets' AND column_name='content_hash') THEN
        ALTER TABLE code_snippets ADD COLUMN content_hash text;
    END IF;
END;
$$;

-- 4. Create Indexes for Scoped and Versioned Operations
CREATE INDEX IF NOT EXISTS idx_user_repos_lookup ON user_repositories(user_id, canonical_url);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_lookup ON ingestion_jobs(repository_id, status);
CREATE INDEX IF NOT EXISTS idx_code_snippets_repo_version ON code_snippets(repository_id, index_version);
CREATE INDEX IF NOT EXISTS idx_code_snippets_job ON code_snippets(ingestion_job_id);
CREATE INDEX IF NOT EXISTS idx_code_snippets_content_hash ON code_snippets(content_hash);


-- ==========================================
-- STAGE 2: SAFE LEGACY-DATA BACKFILL & ISOLATION
-- ==========================================

-- PREFLIGHT DIAGNOSTICS: RUN THESE SELECTS TO SEE LEGACY COUNTS
-- SELECT COUNT(*) FROM code_snippets WHERE user_id IS NULL;
-- SELECT COUNT(*) FROM code_snippets WHERE repository_id IS NULL;

-- Legitimate legacy backfill note:
-- Legacy snippets that contain valid user_id and repo_name can be mapped to repositories
-- ONLY if we create repository records for them first.
-- To maintain complete isolation, unowned legacy rows (user_id IS NULL) are LEFT UNTOUCHED (quarantined).
-- They will not be visible to any user under the auth.uid() policies below.


-- ==========================================
-- STAGE 3: ROW LEVEL SECURITY & POLICIES
-- ==========================================

ALTER TABLE user_repositories ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingestion_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE code_snippets ENABLE ROW LEVEL SECURITY;

-- 1. Policies for user_repositories (Strict auth.uid() owner comparison)
DROP POLICY IF EXISTS "Users access own repositories" ON user_repositories;
CREATE POLICY "Users access own repositories" ON user_repositories
    FOR ALL
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- 2. Policies for ingestion_jobs (Strict auth.uid() comparison)
DROP POLICY IF EXISTS "Users access own ingestion jobs" ON ingestion_jobs;
CREATE POLICY "Users access own ingestion jobs" ON ingestion_jobs
    FOR ALL
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- 3. Policies for code_snippets (Strict auth.uid() text format comparison)
DROP POLICY IF EXISTS "Users access own code snippets" ON code_snippets;
CREATE POLICY "Users access own code snippets" ON code_snippets
    FOR ALL
    USING (auth.uid()::text = user_id)
    WITH CHECK (auth.uid()::text = user_id);


-- ==========================================
-- STAGE 4: SEARCH FUNCTION UPDATES
-- ==========================================

CREATE OR REPLACE FUNCTION search_code_snippets(
  query_embedding vector(384),
  match_count int DEFAULT 5,
  p_user_id text DEFAULT NULL,
  p_repository_id uuid DEFAULT NULL,
  p_index_version text DEFAULT NULL
)
RETURNS TABLE (
  id bigint,
  repo_name text,
  file_path text,
  language text,
  code_content text,
  source_url text,
  similarity float4
)
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
DECLARE
  effective_user_id text;
BEGIN
  -- Strict context isolation
  effective_user_id := COALESCE(auth.uid()::text, p_user_id);

  IF effective_user_id IS NULL THEN
    RAISE EXCEPTION 'Authentication required for vector search';
  END IF;

  RETURN QUERY
  SELECT
    code_snippets.id,
    code_snippets.repo_name,
    code_snippets.file_path,
    code_snippets.language,
    code_snippets.code_content,
    code_snippets.source_url,
    (1 - (code_snippets.embedding <=> query_embedding))::float4 as similarity
  FROM code_snippets
  WHERE code_snippets.embedding IS NOT NULL
    AND code_snippets.user_id = effective_user_id
    AND (p_repository_id IS NULL OR code_snippets.repository_id = p_repository_id)
    AND (p_index_version IS NULL OR code_snippets.index_version = p_index_version)
  ORDER BY code_snippets.embedding <=> query_embedding
  LIMIT match_count;
END;
$$ LANGUAGE plpgsql;

-- Restrict function execution to authenticated and anon users explicitly
REVOKE ALL ON FUNCTION search_code_snippets(vector, int, text, uuid, text) FROM public;
GRANT EXECUTE ON FUNCTION search_code_snippets(vector, int, text, uuid, text) TO authenticated;
GRANT EXECUTE ON FUNCTION search_code_snippets(vector, int, text, uuid, text) TO anon;
