import { supabase } from './supabaseClient';

const getRawApiUrl = () => {
  const raw = import.meta.env.VITE_API_URL || 'http://localhost:8000';
  return raw.endsWith('/') ? raw.slice(0, -1) : raw;
};

/**
 * Gets the current Supabase session access token safely.
 */
export async function getAuthHeaders() {
  try {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session || !session.access_token) {
      return { 'Content-Type': 'application/json' };
    }
    return {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${session.access_token}`
    };
  } catch (err) {
    console.warn('Failed to retrieve auth token:', err);
    return { 'Content-Type': 'application/json' };
  }
}

/**
 * Central authenticated API fetch helper.
 */
export async function apiFetch(endpoint, options = {}) {
  const baseUrl = getRawApiUrl();
  const url = endpoint.startsWith('http') ? endpoint : `${baseUrl}${endpoint.startsWith('/') ? '' : '/'}${endpoint}`;

  const authHeaders = await getAuthHeaders();
  const headers = {
    ...authHeaders,
    ...(options.headers || {})
  };

  const response = await fetch(url, {
    ...options,
    headers
  });

  if (response.status === 401) {
    throw new Error('Authentication required or session expired. Please sign in again.');
  }

  return response;
}
