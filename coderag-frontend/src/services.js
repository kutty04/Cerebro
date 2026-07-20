/**
 * services.js — Centralized API service layer for Cerebro.
 *
 * Every function:
 *  - routes through the authenticated apiFetch client (token attached server-side)
 *  - maps HTTP error codes to safe, actionable user-facing strings
 *  - never logs raw error messages, tokens or backend content
 *  - never exposes raw HTTP objects to callers
 */
import { apiFetch } from './apiClient';

/* ────────────────────────────────────────────────────────────
   Safe error mapping
   ──────────────────────────────────────────────────────────── */

const SAFE_MESSAGES = {
  400: 'The request could not be processed. Please check your input.',
  401: 'Your session has expired. Please sign in again.',
  403: 'Access denied. You do not have permission to perform this action.',
  404: 'The requested resource was not found.',
  409: 'A conflict occurred. The repository may already be indexed or the request is ambiguous.',
  422: 'The server could not process this request.',
  429: 'Rate limit reached. Please wait a moment before trying again.',
  500: 'An internal error occurred. Please try again.',
  502: 'The AI provider returned an invalid response. Please try again.',
  503: 'The AI provider is temporarily unavailable. Please try again in a few moments.',
  504: 'The request timed out. Please try again.',
};

function safePick(status) {
  return SAFE_MESSAGES[status] ?? `Request failed (${status}). Please try again.`;
}

function makeServiceError(status, retryAfter) {
  const err = new Error(safePick(status));
  err.status = status;
  if (retryAfter) err.retryAfter = retryAfter;
  return err;
}

async function handleResponse(res) {
  if (res.ok) return res.json();
  const retryAfter = res.headers?.get?.('Retry-After') ?? null;
  throw makeServiceError(res.status, retryAfter);
}

/* ────────────────────────────────────────────────────────────
   Search
   ──────────────────────────────────────────────────────────── */

/**
 * @param {Object} params
 * @param {string}   params.query
 * @param {string}   [params.repoFilter]
 * @param {Array}    [params.history]
 * @param {number}   [params.topK]
 * @returns {Promise<SearchResponse>}
 */
export async function search({ query, repoFilter, history = [], topK = 5 }) {
  const body = { query, top_k: topK, history };
  if (repoFilter) body.repo_filter = repoFilter;

  const res = await apiFetch('/search', {
    method: 'POST',
    body: JSON.stringify(body),
  });
  return handleResponse(res);
}

/* ────────────────────────────────────────────────────────────
   Repositories
   ──────────────────────────────────────────────────────────── */

/**
 * Returns { repos: string[], repositories: RepoRecord[] }
 */
export async function fetchUserRepos() {
  const res = await apiFetch('/user-repos');
  return handleResponse(res);
}

/**
 * @param {string} repoName
 */
export async function deleteRepo(repoName) {
  const res = await apiFetch(`/delete-repo?repo_name=${encodeURIComponent(repoName)}`, {
    method: 'POST',
  });
  if (!res.ok) throw makeServiceError(res.status);
  return true;
}

/* ────────────────────────────────────────────────────────────
   Ingestion
   ──────────────────────────────────────────────────────────── */

/**
 * @param {string} repoUrl
 * @returns {Promise<IngestionResponse>}
 */
export async function ingestRepo(repoUrl) {
  const res = await apiFetch('/ingest', {
    method: 'POST',
    body: JSON.stringify({ repo_url: repoUrl }),
  });
  return handleResponse(res);
}

/* ────────────────────────────────────────────────────────────
   Analytics & History
   ──────────────────────────────────────────────────────────── */

export async function fetchAnalytics() {
  const res = await apiFetch('/analytics');
  return handleResponse(res);
}

export async function fetchHistory() {
  const res = await apiFetch('/history');
  return handleResponse(res);
}

/* ────────────────────────────────────────────────────────────
   Graph
   ──────────────────────────────────────────────────────────── */

export async function fetchGraphData() {
  const res = await apiFetch('/graph-data');
  return handleResponse(res);
}
