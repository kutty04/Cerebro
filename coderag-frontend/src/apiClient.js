import { supabase } from './supabaseClient';

const rawApiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000';
export const API_BASE_URL = rawApiUrl.endsWith('/') ? rawApiUrl.slice(0, -1) : rawApiUrl;

/**
 * Centralized authenticated API fetch helper.
 * Automatically attaches Bearer access_token from active Supabase session.
 * Handles single 401 session refresh and signs out cleanly on persistent authentication failure.
 */
export async function apiFetch(endpointPath, options = {}) {
  const url = endpointPath.startsWith('http') ? endpointPath : `${API_BASE_URL}${endpointPath.startsWith('/') ? '' : '/'}${endpointPath}`;
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };

  // 1. Obtain current Supabase session
  if (supabase) {
    const { data: { session } } = await supabase.auth.getSession();
    if (session && session.access_token) {
      headers['Authorization'] = `Bearer ${session.access_token}`;
    }
  }

  const fetchOptions = { ...options, headers };
  let response = await fetch(url, fetchOptions);

  // 2. Handle HTTP 401 (Attempt one session refresh & retry)
  if (response.status === 401 && supabase) {
    try {
      const { data: { session: refreshedSession }, error } = await supabase.auth.refreshSession();
      if (!error && refreshedSession && refreshedSession.access_token) {
        headers['Authorization'] = `Bearer ${refreshedSession.access_token}`;
        response = await fetch(url, { ...options, headers });
      } else {
        // Refresh failed: sign out cleanly to return user to login screen
        await supabase.auth.signOut();
      }
    } catch (refreshErr) {
      await supabase.auth.signOut();
    }
  }

  // 3. Handle HTTP 403
  if (response.status === 403) {
    const errData = await response.json().catch(() => ({}));
    throw new Error(errData.detail || 'Access Denied: You do not have permission to access this resource.');
  }

  return response;
}
