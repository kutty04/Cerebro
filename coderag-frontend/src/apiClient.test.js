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

describe('apiClient authentication helper', () => {
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
});
