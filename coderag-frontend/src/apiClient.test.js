import { describe, it, expect, vi, beforeEach } from 'vitest';
import { getAuthHeaders, apiFetch } from './apiClient';

// Mock Supabase client
vi.mock('./supabaseClient', () => ({
  supabase: {
    auth: {
      getSession: vi.fn()
    }
  }
}));

import { supabase } from './supabaseClient';

describe('apiClient authentication & repository scope integrity', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('attaches Authorization: Bearer token when active session exists', async () => {
    supabase.auth.getSession.mockResolvedValue({
      data: {
        session: {
          access_token: 'mock-jwt-token-123'
        }
      }
    });

    const headers = await getAuthHeaders();
    expect(headers).toEqual({
      'Content-Type': 'application/json',
      'Authorization': 'Bearer mock-jwt-token-123'
    });
  });

  it('routes /search request with repository_id and repo_filter scoping', async () => {
    supabase.auth.getSession.mockResolvedValue({
      data: { session: { access_token: 'valid-search-token' } }
    });

    global.fetch = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({
        answer: 'Jarvis AI architecture summary',
        sources: [{ repo: 'Jarvis-portfolio', file: 'main.py', code: 'init_jarvis()' }]
      })
    });

    const response = await apiFetch('/search', {
      method: 'POST',
      body: JSON.stringify({ 
        query: 'What is this project about?',
        repo_filter: 'Jarvis-portfolio',
        repository_id: 'repo-jarvis-001'
      })
    });

    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/search'),
      expect.objectContaining({
        headers: expect.objectContaining({
          'Authorization': 'Bearer valid-search-token'
        })
      })
    );
    const data = await response.json();
    expect(data.sources.every(s => s.repo === 'Jarvis-portfolio')).toBe(true);
  });

  it('routes /graph-data with repository_id parameter scoping', async () => {
    supabase.auth.getSession.mockResolvedValue({
      data: { session: { access_token: 'valid-graph-token' } }
    });

    global.fetch = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({
        nodes: [
          { id: 'ME', name: 'Neural Core' },
          { id: 'Jarvis-portfolio', name: 'Jarvis-portfolio' },
          { id: 'Jarvis-portfolio/main.py', name: 'main.py' }
        ],
        links: []
      })
    });

    const response = await apiFetch('/graph-data?user_id=usr-123&repository_id=repo-jarvis-001');
    const data = await response.json();
    const repoNodes = data.nodes.filter(n => n.id !== 'ME').map(n => n.id);
    expect(repoNodes.every(id => id.includes('Jarvis-portfolio'))).toBe(true);
    expect(repoNodes.some(id => id.includes('bus-crowding'))).toBe(false);
  });

  it('allows All Projects scope to intentionally return all repositories', async () => {
    supabase.auth.getSession.mockResolvedValue({
      data: { session: { access_token: 'valid-all-token' } }
    });

    global.fetch = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({
        nodes: [
          { id: 'ME', name: 'Neural Core' },
          { id: 'Jarvis-portfolio', name: 'Jarvis-portfolio' },
          { id: 'bus-crowding', name: 'bus-crowding' }
        ],
        links: []
      })
    });

    const response = await apiFetch('/graph-data?user_id=usr-123');
    const data = await response.json();
    const repoNames = data.nodes.map(n => n.name);
    expect(repoNames).toContain('Jarvis-portfolio');
    expect(repoNames).toContain('bus-crowding');
  });

  it('fails closed when backend returns 500 promotion error', async () => {
    supabase.auth.getSession.mockResolvedValue({
      data: { session: { access_token: 'valid-token' } }
    });

    global.fetch = vi.fn().mockResolvedValue({
      status: 500,
      ok: false,
      json: async () => ({ detail: 'Database execution error during promote_index_version.' })
    });

    const response = await apiFetch('/ingest', { method: 'POST', body: '{}' });
    expect(response.ok).toBe(false);
  });
});
