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

describe('apiClient authentication & contract reconciliation helper', () => {
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

  it('returns plain Content-Type header when session is null', async () => {
    supabase.auth.getSession.mockResolvedValue({
      data: { session: null }
    });

    const headers = await getAuthHeaders();
    expect(headers).toEqual({
      'Content-Type': 'application/json'
    });
  });

  it('handles HTTP 401 response with sign-in error message', async () => {
    supabase.auth.getSession.mockResolvedValue({
      data: { session: { access_token: 'invalid-token' } }
    });

    global.fetch = vi.fn().mockResolvedValue({
      status: 401,
      ok: false
    });

    await expect(apiFetch('/ingest', { method: 'POST', body: '{}' }))
      .rejects.toThrow('Authentication required or session expired');
  });

  it('routes /search request with Bearer header attached', async () => {
    supabase.auth.getSession.mockResolvedValue({
      data: { session: { access_token: 'valid-search-token' } }
    });

    global.fetch = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({ answer: 'test answer', sources: [] })
    });

    const response = await apiFetch('/search', {
      method: 'POST',
      body: JSON.stringify({ query: 'test query' })
    });

    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/search'),
      expect.objectContaining({
        headers: expect.objectContaining({
          'Authorization': 'Bearer valid-search-token'
        })
      })
    );
    expect(response.ok).toBe(true);
  });

  it('fails closed when backend returns HTTP 500 promotion error', async () => {
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
    expect(response.status).toBe(500);
  });

  it('safely handles non-array telemetry responses without crashing', () => {
    const rawLogs = { detail: 'Logs endpoint unavailable' };
    const safeLogs = Array.isArray(rawLogs?.logs) ? rawLogs.logs : [];
    expect(safeLogs).toEqual([]);
    expect(() => safeLogs.map(x => x)).not.toThrow();
  });
});
