-- SQL Function for CodeRAG Vector Search
-- Run this in your Supabase SQL Editor after creating the code_snippets table

-- Create a function to search code snippets by embedding similarity
CREATE OR REPLACE FUNCTION search_code_snippets(
  query_embedding vector(384),
  match_count int DEFAULT 5
)
RETURNS TABLE (
  id bigint,
  repo_name text,
  file_path text,
  language text,
  code_content text,
  source_url text,
  similarity float4
) AS $$
BEGIN
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
  ORDER BY code_snippets.embedding <=> query_embedding
  LIMIT match_count;
END;
$$ LANGUAGE plpgsql;

-- Optional: Create a view for recent snippets
CREATE OR REPLACE VIEW recent_snippets AS
SELECT
  id,
  repo_name,
  file_path,
  language,
  code_content,
  source_url,
  created_at
FROM code_snippets
ORDER BY created_at DESC
LIMIT 100;

-- Optional: Create statistics view
CREATE OR REPLACE VIEW snippet_stats AS
SELECT
  repo_name,
  language,
  COUNT(*) as count
FROM code_snippets
GROUP BY repo_name, language
ORDER BY repo_name, language;
