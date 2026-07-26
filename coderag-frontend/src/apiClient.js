import { supabase } from './supabaseClient';

const getRawApiUrl = () => {
  const raw = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';
  return raw.endsWith('/') ? raw.slice(0, -1) : raw;
};

/**
 * Gets the current Supabase session access token safely.
 * Throws an Error if no active session or valid access_token exists.
 */
export async function getAuthHeaders() {
  const { data: { session } } = await supabase.auth.getSession();
  const token = session?.access_token;

  if (!token) {
    throw new Error('Your session has expired. Please sign in again.');
  }

  return {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`
  };
}

/**
 * Central authenticated API fetch helper.
 * Enforces session verification before making any network request.
 */
export async function apiFetch(endpoint, options = {}) {
  const baseUrl = getRawApiUrl();
  const url = endpoint.startsWith('http') ? endpoint : `${baseUrl}${endpoint.startsWith('/') ? '' : '/'}${endpoint}`;

  const authHeaders = await getAuthHeaders();
  const headers = {
    ...(options.headers || {}),
    ...authHeaders,
  };

  const response = await fetch(url, {
    ...options,
    headers
  });

  if (response.status === 401) {
    throw new Error('Your session has expired. Please sign in again.');
  }

  return response;
}
