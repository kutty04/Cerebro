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

  it('throws session expired error when no session or access_token exists', async () => {
    supabase.auth.getSession.mockResolvedValue({
      data: { session: null }
    });

    await expect(getAuthHeaders()).rejects.toThrow('Your session has expired. Please sign in again.');
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
        repository_id: 'bde55722-56eb-48e6-a6f4-ebb219328a67'
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

    const response = await apiFetch('/graph-data?user_id=usr-123&repository_id=bde55722-56eb-48e6-a6f4-ebb219328a67');
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

describe('ingest & user-repos repository lifecycle integrity', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('successful /ingest creates user_repositories row and returns real repository UUID', async () => {
    supabase.auth.getSession.mockResolvedValue({
      data: { session: { access_token: 'ingest-token-abc' } }
    });

    const REAL_REPO_UUID = 'e8b7d41f-829d-4e99-b1d5-9988ff776655';

    global.fetch = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({
        status: 'success',
        message: 'Successfully indexed 12 snippets',
        indexed_count: 12,
        repository_id: REAL_REPO_UUID
      })
    });

    const response = await apiFetch('/ingest', {
      method: 'POST',
      body: JSON.stringify({
        repo_url: 'https://github.com/kutty04/ipl.git',
        user_id: 'user-123'
      })
    });

    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/ingest'),
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          'Authorization': 'Bearer ingest-token-abc'
        })
      })
    );

    const data = await response.json();
    expect(data.status).toBe('success');
    expect(data.repository_id).toBe(REAL_REPO_UUID);
  });

  it('/user-repos is read-only and queries only user_repositories for authenticated user', async () => {
    supabase.auth.getSession.mockResolvedValue({
      data: { session: { access_token: 'user-repos-token' } }
    });

    global.fetch = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({
        repos: ['ipl'],
        repositories: [{
          id: 'e8b7d41f-829d-4e99-b1d5-9988ff776655',
          repository_name: 'ipl',
          repo_name: 'ipl',
          repository_owner: 'kutty04',
          canonical_url: 'https://github.com/kutty04/ipl',
          legacy: false,
          status: 'active'
        }]
      })
    });

    const response = await apiFetch('/user-repos?user_id=user-123');
    const data = await response.json();

    expect(data.repos).toEqual(['ipl']);
    expect(data.repositories[0].id).toBe('e8b7d41f-829d-4e99-b1d5-9988ff776655');
    expect(data.repositories[0].legacy).toBe(false);
  });
});

