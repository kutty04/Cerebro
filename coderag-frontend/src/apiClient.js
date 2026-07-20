import { supabase as defaultSupabase } from './supabaseClient.js';

const rawApiUrl = (typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.VITE_API_URL) || 'http://localhost:8000';
export const API_BASE_URL = rawApiUrl.endsWith('/') ? rawApiUrl.slice(0, -1) : rawApiUrl;

let customSupabase = null;

export function setSupabaseInstance(instance) {
  customSupabase = instance;
}

/**
 * Centralized authenticated API fetch helper.
 * Automatically attaches Bearer access_token from active Supabase session.
 * Handles single 401 session refresh and signs out cleanly on persistent authentication failure.
 */
export async function apiFetch(endpointPath, options = {}) {
  const url = endpointPath.startsWith('http') ? endpointPath : `${API_BASE_URL}${endpointPath.startsWith('/') ? '' : '/'}${endpointPath}`;
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  const activeSupabase = customSupabase || defaultSupabase;

  // 1. Obtain current Supabase session
  if (activeSupabase) {
    try {
      const { data: { session } } = await activeSupabase.auth.getSession();
      if (session && session.access_token) {
        headers['Authorization'] = `Bearer ${session.access_token}`;
      }
    } catch (e) {
      // Session fetch error ignored
    }
  }

  const fetchOptions = { ...options, headers };
  let response = await fetch(url, fetchOptions);

  // 2. Handle HTTP 401 (Attempt one session refresh & retry)
  if (response.status === 401 && activeSupabase) {
    try {
      const { data: { session: refreshedSession }, error } = await activeSupabase.auth.refreshSession();
      if (!error && refreshedSession && refreshedSession.access_token) {
        headers['Authorization'] = `Bearer ${refreshedSession.access_token}`;
        response = await fetch(url, { ...options, headers });
        if (response.status === 401) {
          await activeSupabase.auth.signOut();
        }
      } else {
        await activeSupabase.auth.signOut();
      }
    } catch (refreshErr) {
      await activeSupabase.auth.signOut();
    }
  }

  // 3. Handle HTTP 403
  if (response.status === 403) {
    const errData = await response.json().catch(() => ({}));
    throw new Error(errData.detail || 'Access Denied: You do not have permission to access this resource.');
  }

  return response;
}
