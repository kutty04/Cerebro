-- ==============================================================================
-- CEREBRO AI - PHASE 4 SUPABASE ROW LEVEL SECURITY (RLS) MIGRATION ARTIFACT
-- Status: REVIEWED / NOT YET APPLIED TO LIVE PRODUCTION
-- Instructions: Apply manually via Supabase Dashboard SQL Editor when ready.
-- ==============================================================================

-- 1. Create Repository Ownership Table
CREATE TABLE IF NOT EXISTS user_repositories (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    repo_name text NOT NULL,
    canonical_url text NOT NULL,
    default_branch text DEFAULT 'main',
    indexed_commit_sha text,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now(),
    CONSTRAINT user_repo_unique UNIQUE(user_id, repo_name)
);

-- Index for fast user/repo ownership lookups
CREATE INDEX IF NOT EXISTS idx_user_repos_owner ON user_repositories(user_id, repo_name);
CREATE INDEX IF NOT EXISTS idx_code_snippets_user_repo ON code_snippets(user_id, repo_name);

-- 2. Enable Row Level Security (RLS)
ALTER TABLE code_snippets ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_repositories ENABLE ROW LEVEL SECURITY;

-- 3. RLS Policies for code_snippets (Strict auth.uid() scoping)
DROP POLICY IF EXISTS "Users access own code snippets" ON code_snippets;
CREATE POLICY "Users access own code snippets" ON code_snippets
    FOR ALL
    USING (auth.uid()::text = user_id)
    WITH CHECK (auth.uid()::text = user_id);

-- 4. RLS Policies for user_repositories (Strict auth.uid() scoping)
DROP POLICY IF EXISTS "Users access own repositories" ON user_repositories;
CREATE POLICY "Users access own repositories" ON user_repositories
    FOR ALL
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- 5. Updated User-Scoped RPC Search Function (SECURITY INVOKER)
CREATE OR REPLACE FUNCTION search_code_snippets(
  query_embedding vector(384),
  match_count int DEFAULT 5,
  p_user_id text DEFAULT NULL
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
AS $$
DECLARE
  effective_user_id text;
BEGIN
  -- Prefer authenticated context auth.uid(), fallback to explicit p_user_id parameter if valid
  effective_user_id := COALESCE(auth.uid()::text, p_user_id);

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
  ORDER BY code_snippets.embedding <=> query_embedding
  LIMIT match_count;
END;
$$ LANGUAGE plpgsql;

-- Rollback Notes:
-- To disable RLS: ALTER TABLE code_snippets DISABLE ROW LEVEL SECURITY;
-- Do not execute automatic DROP TABLE or destructive SQL on production databases.
