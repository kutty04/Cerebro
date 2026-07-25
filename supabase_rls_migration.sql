-- ==============================================================================
-- CEREBRO AI - PHASE 4 SUPABASE ROW LEVEL SECURITY (RLS) MIGRATION ARTIFACT
-- Status: REVIEWED / NOT YET APPLIED TO LIVE PRODUCTION
-- Instructions: Apply manually via Supabase Dashboard SQL Editor when ready.
-- ==============================================================================

SET search_path = public, pg_temp;

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

-- 2. Create User Conversations Table (Supabase Sync Schema)
CREATE TABLE IF NOT EXISTS user_conversations (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    repo_filter text DEFAULT 'ALL',
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

-- Indexes for fast user/repo and conversation ownership lookups
CREATE INDEX IF NOT EXISTS idx_user_repos_owner ON user_repositories(user_id, repo_name);
CREATE INDEX IF NOT EXISTS idx_user_conversations_owner ON user_conversations(user_id);

-- 3. Safe Migration Strategy for Legacy Data:
-- Ensure code_snippets.user_id exists as TEXT before applying NOT NULL constraint.
-- Legacy rows where user_id IS NULL are NOT purged automatically; they remain inaccessible via auth.uid() policies.

-- 4. Enable Row Level Security (RLS)
ALTER TABLE code_snippets ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_repositories ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_conversations ENABLE ROW LEVEL SECURITY;

-- 5. RLS Policies for code_snippets (Strict auth.uid() UUID comparison)
DROP POLICY IF EXISTS "Users access own code snippets" ON code_snippets;
CREATE POLICY "Users access own code snippets" ON code_snippets
    FOR ALL
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- 6. RLS Policies for user_repositories
DROP POLICY IF EXISTS "Users access own repositories" ON user_repositories;
CREATE POLICY "Users access own repositories" ON user_repositories
    FOR ALL
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- 7. RLS Policies for user_conversations
DROP POLICY IF EXISTS "Users access own conversations" ON user_conversations;
CREATE POLICY "Users access own conversations" ON user_conversations
    FOR ALL
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- 8. Updated User-Scoped RPC Search Function (SECURITY INVOKER & Explicit search_path)
CREATE OR REPLACE FUNCTION search_code_snippets(
  query_embedding vector(384),
  match_count int DEFAULT 5,
  p_user_id uuid DEFAULT NULL
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
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
DECLARE
  effective_user_id uuid;
BEGIN
  -- Strict isolation: Require auth.uid() context or explicit p_user_id parameter
  effective_user_id := COALESCE(auth.uid(), p_user_id);

  IF effective_user_id IS NULL THEN
    RAISE EXCEPTION 'Authentication required for vector search';
  END IF;

  RETURN QUERY
  SELECT
    code_snippets.id,
    user_repositories.repo_name,
    code_snippets.file_path,
    code_snippets.language,
    code_snippets.code_content,
    code_snippets.source_url,
    (1 - (code_snippets.embedding <=> query_embedding))::float4 as similarity
  FROM code_snippets
  INNER JOIN user_repositories ON code_snippets.repository_id = user_repositories.id
  WHERE code_snippets.embedding IS NOT NULL
    AND code_snippets.user_id = effective_user_id
  ORDER BY code_snippets.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;

-- Service Role Privilege Note:
-- The Supabase Service-Role Key bypasses Row Level Security.
-- Backend endpoints using server-side credentials must maintain strict `.eq("user_id", authenticated_user.id)`
-- filtering on all queries to enforce isolation independently of database RLS settings.
