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

describe('reconcile-legacy repair flow', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('repair button: /user-repos with legacy repos triggers a single authenticated POST to reconcile-legacy', async () => {
    supabase.auth.getSession.mockResolvedValue({
      data: { session: { access_token: 'repair-token-abc' } }
    });

    global.fetch = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({
        status: 'ok',
        reconciled: [{ repo_name: 'ipl', id: 'f47ac10b-58cc-4372-a567-0e02b2c3d479' }],
        errors: []
      })
    });

    const response = await apiFetch('/repositories/reconcile-legacy', { method: 'POST' });

    // Must send exactly one fetch call
    expect(global.fetch).toHaveBeenCalledTimes(1);

    // Must use POST method
    const [url, opts] = global.fetch.mock.calls[0];
    expect(opts.method).toBe('POST');

    // Must send Authorization: Bearer from session token — never a fake UUID or repo name
    expect(opts.headers['Authorization']).toBe('Bearer repair-token-abc');

    // Body must NOT contain a repository_id (endpoint takes no repo ID)
    const body = opts.body;
    expect(body).toBeUndefined();

    expect(response.ok).toBe(true);
    const data = await response.json();
    expect(data.status).toBe('ok');
    expect(data.reconciled).toHaveLength(1);
    expect(data.reconciled[0].repo_name).toBe('ipl');
    // Reconciled repo must have a real UUID id, not null or a name string
    expect(typeof data.reconciled[0].id).toBe('string');
    expect(data.reconciled[0].id).not.toBe('ipl');
    expect(data.reconciled[0].id).not.toBe('None');
    expect(data.reconciled[0].id).not.toBeNull();
  });

  it('repair call: reconcile-legacy is never triggered by /user-repos (page load must not call it)', async () => {
    supabase.auth.getSession.mockResolvedValue({
      data: { session: { access_token: 'pageload-token' } }
    });

    const fetchedUrls = [];
    global.fetch = vi.fn().mockImplementation((url) => {
      fetchedUrls.push(url);
      return Promise.resolve({
        status: 200,
        ok: true,
        json: async () => ({
          repos: ['ipl'],
          repositories: [{ id: null, repo_name: 'ipl', legacy: true }]
        })
      });
    });

    // Simulate page load: call /user-repos only
    await apiFetch('/user-repos?user_id=some-uid');

    // Exactly one fetch call — only /user-repos
    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(fetchedUrls[0]).toContain('/user-repos');
    // reconcile-legacy must NOT have been called automatically
    expect(fetchedUrls.every(u => !u.includes('reconcile-legacy'))).toBe(true);
  });

  it('after successful repair: /user-repos returns real UUID for previously-legacy ipl repo', async () => {
    supabase.auth.getSession.mockResolvedValue({
      data: { session: { access_token: 'post-repair-token' } }
    });

    const REAL_IPL_UUID = 'a3bb189e-8bf9-3888-9912-ace4e6543002';

    // First call: /user-repos after reconciliation returns ipl with a real UUID
    global.fetch = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({
        repos: ['ipl'],
        repositories: [{
          id: REAL_IPL_UUID,
          repo_name: 'ipl',
          repository_name: 'ipl',
          legacy: false,
          status: 'active'
        }]
      })
    });

    const response = await apiFetch('/user-repos?user_id=some-uid');
    const data = await response.json();

    const iplRepo = data.repositories.find(r => r.repo_name === 'ipl');
    expect(iplRepo).toBeDefined();
    // After repair, ipl must have a real UUID id
    expect(iplRepo.id).toBe(REAL_IPL_UUID);
    // After repair, ipl must NOT be legacy
    expect(iplRepo.legacy).toBe(false);
    // The id must not be a name string
    expect(iplRepo.id).not.toBe('ipl');
    expect(iplRepo.id).not.toBeNull();
  });
});

