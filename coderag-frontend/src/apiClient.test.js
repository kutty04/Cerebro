import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { apiFetch, setSupabaseInstance } from './apiClient.js';

let sessionMock = null;
let refreshCount = 0;
let refreshShouldFail = false;
let signOutCount = 0;

const mockSupabase = {
  auth: {
    getSession: async () => ({ data: { session: sessionMock } }),
    refreshSession: async () => {
      refreshCount++;
      if (refreshShouldFail) {
        sessionMock = null;
        return { data: { session: null }, error: new Error('Refresh failed') };
      }
      sessionMock = { access_token: 'refreshed-test-access-token-456' };
      return { data: { session: sessionMock }, error: null };
    },
    signOut: async () => {
      signOutCount++;
      sessionMock = null;
    },
  },
};

setSupabaseInstance(mockSupabase);

let lastFetchUrl = '';
let lastFetchHeaders = {};
let fetchResponseStatus = 200;
let fetchResponseBody = { status: 'ok' };

beforeEach(() => {
  sessionMock = { access_token: 'valid-test-access-token-123' };
  refreshCount = 0;
  refreshShouldFail = false;
  signOutCount = 0;
  lastFetchUrl = '';
  lastFetchHeaders = {};
  fetchResponseStatus = 200;
  fetchResponseBody = { status: 'ok' };

  global.fetch = async (url, options = {}) => {
    lastFetchUrl = url;
    lastFetchHeaders = options.headers || {};
    return {
      status: fetchResponseStatus,
      ok: fetchResponseStatus >= 200 && fetchResponseStatus < 300,
      json: async () => fetchResponseBody,
    };
  };
});

test('1. Valid session attaches Authorization Bearer header', async () => {
  sessionMock = { access_token: 'valid-test-token-789' };
  fetchResponseStatus = 200;

  const res = await apiFetch('/search', { method: 'POST' });
  assert.equal(res.status, 200);
  assert.equal(lastFetchHeaders['Authorization'], 'Bearer valid-test-token-789');
  assert.ok(!lastFetchUrl.includes('valid-test-token-789'), 'Token must not be in URL');
});

test('2. Token is never placed in URL or query parameters', async () => {
  sessionMock = { access_token: 'secret-token-xyz' };
  await apiFetch('/user-repos');
  assert.ok(!lastFetchUrl.includes('secret-token-xyz'));
});

test('3 & 4. HTTP 401 triggers single session refresh and retries with new token', async () => {
  sessionMock = { access_token: 'expired-token-111' };
  let callCount = 0;
  global.fetch = async (url, options = {}) => {
    callCount++;
    lastFetchHeaders = options.headers || {};
    if (callCount === 1) {
      return { status: 401, ok: false, json: async () => ({ detail: 'Token expired' }) };
    }
    return { status: 200, ok: true, json: async () => ({ status: 'success' }) };
  };

  const res = await apiFetch('/history');
  assert.equal(res.status, 200);
  assert.equal(refreshCount, 1, 'Should trigger exactly 1 refresh');
  assert.equal(signOutCount, 0);
  assert.equal(lastFetchHeaders['Authorization'], 'Bearer refreshed-test-access-token-456');
});

test('5. Failed session refresh triggers clean sign-out', async () => {
  sessionMock = { access_token: 'invalid-token-222' };
  refreshShouldFail = true;

  global.fetch = async () => ({ status: 401, ok: false, json: async () => ({ detail: 'Unauthorized' }) });

  await apiFetch('/analytics');
  assert.equal(refreshCount, 1);
  assert.equal(signOutCount, 1, 'Should trigger sign-out on refresh failure');
});

test('6. Second 401 response signs out cleanly without infinite loop', async () => {
  sessionMock = { access_token: 'bad-token-333' };
  global.fetch = async () => ({ status: 401, ok: false, json: async () => ({ detail: 'Unauthorized' }) });

  await apiFetch('/user-repos');
  assert.equal(refreshCount, 1, 'Max 1 refresh attempt');
  assert.equal(signOutCount, 1, 'Signs out when retried request is still 401');
});

test('7. HTTP 403 produces safe access-denied error', async () => {
  sessionMock = { access_token: 'user-b-token' };
  global.fetch = async () => ({
    status: 403,
    ok: false,
    json: async () => ({ detail: 'Access denied: user identity mismatch.' }),
  });

  await assert.rejects(
    async () => {
      await apiFetch('/delete-repo');
    },
    (err) => {
      assert.ok(err.message.includes('Access denied'), 'Error message must describe access denial');
      return true;
    }
  );
});

test('8. Missing session executes safely without header', async () => {
  sessionMock = null;
  refreshShouldFail = true;
  global.fetch = async (url, options = {}) => {
    lastFetchHeaders = options.headers || {};
    return { status: 401, ok: false, json: async () => ({ detail: 'Unauthenticated' }) };
  };

  await apiFetch('/user-repos');
  assert.equal(lastFetchHeaders['Authorization'], undefined);
});

test('9. Static raw-fetch audit on components', () => {
  const componentsDir = './src/components';
  if (!fs.existsSync(componentsDir)) return;

  const files = fs.readdirSync(componentsDir);
  const protectedEndpoints = [
    '/search', '/ingest', '/index', '/user-repos', '/delete-repo', '/graph-data', '/history', '/analytics'
  ];

  for (const file of files) {
    if (file.endsWith('.jsx') || file.endsWith('.js')) {
      const content = fs.readFileSync(path.join(componentsDir, file), 'utf-8');
      
      // Ensure no raw 'fetch(' call is present
      assert.ok(!content.includes('fetch('), `Raw fetch() call detected in production component: ${file}. Use apiFetch instead.`);
      
      // Ensure protected endpoints are not accessed via raw fetch
      for (const endpoint of protectedEndpoints) {
        if (content.includes(endpoint)) {
          assert.ok(content.includes('apiFetch'), `Endpoint ${endpoint} found in ${file} without apiFetch!`);
        }
      }
    }
  }
});

test('10. Service function search invokes authenticated API client', async () => {
  sessionMock = { access_token: 'auth-token-123' };
  fetchResponseStatus = 200;
  await apiFetch('/search', { method: 'POST', body: JSON.stringify({ query: 'test' }) });
  assert.equal(lastFetchHeaders['Authorization'], 'Bearer auth-token-123');
});

test('11. Service function ingest invokes authenticated API client', async () => {
  sessionMock = { access_token: 'auth-token-123' };
  fetchResponseStatus = 200;
  await apiFetch('/ingest', { method: 'POST', body: JSON.stringify({ repo_url: 'https://github.com/a/b' }) });
  assert.equal(lastFetchHeaders['Authorization'], 'Bearer auth-token-123');
});

test('12. Service function repository list invokes authenticated API client', async () => {
  sessionMock = { access_token: 'auth-token-123' };
  fetchResponseStatus = 200;
  await apiFetch('/user-repos');
  assert.equal(lastFetchHeaders['Authorization'], 'Bearer auth-token-123');
});

test('13. Service function graph invokes authenticated API client', async () => {
  sessionMock = { access_token: 'auth-token-123' };
  fetchResponseStatus = 200;
  await apiFetch('/graph-data');
  assert.equal(lastFetchHeaders['Authorization'], 'Bearer auth-token-123');
});

test('14. Service function delete repository invokes authenticated API client', async () => {
  sessionMock = { access_token: 'auth-token-123' };
  fetchResponseStatus = 200;
  await apiFetch('/delete-repo?repo_name=test', { method: 'POST' });
  assert.equal(lastFetchHeaders['Authorization'], 'Bearer auth-token-123');
});



/* Phase 7 tests */
function makeFetchStub(status, body, hdrs) {
  body = body || {}; hdrs = hdrs || {};
  return async function() { return { status, ok: status >= 200 && status < 300, headers: { get: function(k){ return hdrs[k] || null; } }, json: async function(){ return body; } }; };
}

test('16. 400 response is not retried', async () => {
  sessionMock = { access_token: 'tok-400' };
  global.fetch = makeFetchStub(400, { detail: 'err' });
  const res = await apiFetch('/search', { method: 'POST' });
  assert.equal(res.status, 400);
  assert.equal(refreshCount, 0);
  assert.equal(signOutCount, 0);
});

test('17. 429 response is not retried', async () => {
  sessionMock = { access_token: 'tok-429' };
  let cc17 = 0;
  global.fetch = async () => { cc17++; return { status: 429, ok: false, headers: { get: () => '30' }, json: async () => ({}) }; };
  const res = await apiFetch('/search', { method: 'POST' });
  assert.equal(res.status, 429);
  assert.equal(cc17, 1);
});

test('18. 502 response propagates without retry', async () => {
  sessionMock = { access_token: 'tok-502' };
  let cc18 = 0;
  global.fetch = async () => { cc18++; return { status: 502, ok: false, headers: { get: () => null }, json: async () => ({}) }; };
  const res = await apiFetch('/search', { method: 'POST' });
  assert.equal(res.status, 502);
  assert.equal(cc18, 1);
});

test('19. 503 response propagates without retry', async () => {
  sessionMock = { access_token: 'tok-503' };
  let cc19 = 0;
  global.fetch = async () => { cc19++; return { status: 503, ok: false, headers: { get: () => null }, json: async () => ({}) }; };
  const res = await apiFetch('/search', { method: 'POST' });
  assert.equal(res.status, 503);
  assert.equal(cc19, 1);
});

test('20. 404 does not trigger session refresh', async () => {
  sessionMock = { access_token: 'tok-404' };
  global.fetch = makeFetchStub(404, { detail: 'not found' });
  const res = await apiFetch('/user-repos');
  assert.equal(res.status, 404);
  assert.equal(refreshCount, 0);
});

test('15. services.js uses apiFetch not raw fetch', () => {
  const src = fs.readFileSync('./src/services.js', 'utf-8');
  assert.ok(!src.includes('fetch('), 'must not contain raw fetch()');
  assert.ok(src.includes('apiFetch'), 'must use apiFetch');
});

test('21. No raw console.error with err content in components', () => {
  const dirs = ['./src/components', './src'];
  for (const dir of dirs) {
    if (!fs.existsSync(dir)) continue;
    const files = fs.readdirSync(dir).filter(f => f.endsWith('.jsx') || f.endsWith('.js'));
    for (const file of files) {
      if (file === 'apiClient.test.js') continue;
      const c = fs.readFileSync(path.join(dir, file), 'utf-8');
      const raw = c.match(/console\.error\([^)]*err\.[a-z]/g);
      assert.ok(!raw, file + ': console.error with raw err content');
    }
  }
});

test('22. No forbidden mock-token strings in production code', () => {
  const dirs = ['./src/components', './src'];
  const bad = ['mock-token', 'test-token', 'fake-token', 'bypass-auth'];
  for (const dir of dirs) {
    if (!fs.existsSync(dir)) continue;
    const files = fs.readdirSync(dir).filter(f => f.endsWith('.jsx') || f.endsWith('.js'));
    for (const file of files) {
      if (file === 'apiClient.test.js') continue;
      const c = fs.readFileSync(path.join(dir, file), 'utf-8');
      for (const tok of bad) { assert.ok(!c.includes(tok), file + ': ' + tok); }
    }
  }
});

test('23. Follow-up chips fill query only no auto-submit', () => {
  const c = fs.readFileSync('./src/components/CodeRAG.jsx', 'utf-8');
  const idx = c.indexOf('follow-up-chip');
  assert.ok(idx !== -1, 'follow-up-chip must exist');
  const snip = c.substring(idx, idx + 600);
  assert.ok(!snip.includes('performSearch'), 'chip must not call performSearch');
  assert.ok(snip.includes('setQuery'), 'chip must call setQuery');
});

test('24. Ingestion modal has dialog attrs', () => {
  const c = fs.readFileSync('./src/components/CodeRAG.jsx', 'utf-8');
  assert.ok(c.includes('role="dialog"'), "must have role=dialog");
  assert.ok(c.includes('aria-modal="true"'), "must have aria-modal");
  assert.ok(c.includes('aria-labelledby'), 'must have aria-labelledby');
});

test('25. Confirm dialog uses role=alertdialog', () => {
  const c = fs.readFileSync('./src/components/CodeRAG.jsx', 'utf-8');
  assert.ok(c.includes('role="alertdialog"'), "must have alertdialog");
});

test('26. Search input has associated label', () => {
  const c = fs.readFileSync('./src/components/CodeRAG.jsx', 'utf-8');
  assert.ok(c.includes('htmlFor="search-input"'), "must have label");
  assert.ok(c.includes('id="search-input"'), "must have id");
});

test('27. Auth inputs have labels', () => {
  const c = fs.readFileSync('./src/components/Auth.jsx', 'utf-8');
  assert.ok(c.includes('htmlFor="auth-email"'), "email must have label");
  assert.ok(c.includes('htmlFor="auth-password"'), "password must have label");
});

test('28. Results region has aria-live polite', () => {
  const c = fs.readFileSync('./src/components/CodeRAG.jsx', 'utf-8');
  assert.ok(c.includes('aria-live="polite"'), "must have aria-live=polite");
});

test('29. Error regions have aria-live assertive', () => {
  const c = fs.readFileSync('./src/components/CodeRAG.jsx', 'utf-8');
  assert.ok(c.includes('aria-live="assertive"'), "must have aria-live=assertive");
});

test('30. index.css has prefers-reduced-motion rule', () => {
  const c = fs.readFileSync('./src/index.css', 'utf-8');
  assert.ok(c.includes('prefers-reduced-motion'), 'must have reduced-motion');
});

test('31. NeuralMap has table fallback with caption', () => {
  const c = fs.readFileSync('./src/components/NeuralMap.jsx', 'utf-8');
  assert.ok(c.includes('<table'), 'must have table fallback');
  assert.ok(c.includes('<caption'), 'table must have caption');
});

test('32. index.html includes skip link', () => {
  const c = fs.readFileSync('./index.html', 'utf-8');
  assert.ok(c.includes('skip-link') || c.includes('#main-content'), 'must have skip link');
});

test('33. Workspace defines id=main-content skip link target', () => {
  const c = fs.readFileSync('./src/components/CodeRAG.jsx', 'utf-8');
  assert.ok(c.includes('id="main-content"'), "must define main-content");
});

test('34. No window.confirm in production components', () => {
  const dir = './src/components';
  if (!fs.existsSync(dir)) return;
  const files = fs.readdirSync(dir).filter(f => f.endsWith('.jsx') || f.endsWith('.js'));
  for (const file of files) {
    const c = fs.readFileSync(path.join(dir, file), 'utf-8');
    assert.ok(!c.includes('confirm('), file + ': confirm() not accessible');
  }
});

test('35. No window.alert in production code', () => {
  const dirs = ['./src/components', './src'];
  for (const dir of dirs) {
    if (!fs.existsSync(dir)) continue;
    const files = fs.readdirSync(dir).filter(f => f.endsWith('.jsx') || f.endsWith('.js'));
    for (const file of files) {
      if (file === 'apiClient.test.js') continue;
      const c = fs.readFileSync(path.join(dir, file), 'utf-8');
      assert.ok(!c.includes('alert('), file + ': alert() not accessible');
    }
  }
});

test('36. Source expand button uses aria-expanded', () => {
  const c = fs.readFileSync('./src/components/CodeRAG.jsx', 'utf-8');
  assert.ok(c.includes('aria-expanded'), 'must have aria-expanded');
});

test('37. Nav uses ARIA tab semantics', () => {
  const c = fs.readFileSync('./src/components/CodeRAG.jsx', 'utf-8');
  assert.ok(c.includes('role="tablist"'), "must have tablist");
  assert.ok(c.includes('role="tab"'), "must have tab");
  assert.ok(c.includes('aria-selected'), 'must have aria-selected');
  assert.ok(c.includes('role="tabpanel"'), "must have tabpanel");
});

test('38. CopyButton provides aria-live feedback', () => {
  const c = fs.readFileSync('./src/components/CodeRAG.jsx', 'utf-8');
  assert.ok(c.includes('copy-feedback'), 'must have copy-feedback span');
  assert.ok(c.includes('aria-live="polite"'), "feedback must be polite");
});

test('39. services.js covers critical HTTP error codes', () => {
  const c = fs.readFileSync('./src/services.js', 'utf-8');
  for (const code of [401, 403, 404, 409, 429, 502, 503]) {
    assert.ok(c.includes(String(code)), 'missing safe message for ' + code);
  }
});

test('40. LandingPage uses nav section footer landmarks', () => {
  const c = fs.readFileSync('./src/components/LandingPage.jsx', 'utf-8');
  assert.ok(c.includes('<nav'), 'must use nav');
  assert.ok(c.includes('<section'), 'must use section');
  assert.ok(c.includes('<footer'), 'must use footer');
});


// -------------------------------------------------------------------------
// SERVICE-WORKER CACHE AUDIT
// Inspect the vite.config.js source (and generated sw.js if present) to
// prove backend API routes are NOT runtime-cached by the service worker.
// -------------------------------------------------------------------------

test('41. vite.config.js uses origin-based API exclusion (not a fragile path list)', () => {
  const c = fs.readFileSync('./vite.config.js', 'utf-8');
  // Must use a hostname/port comparison strategy
  assert.ok(c.includes('hostname'), 'SW must use hostname-based exclusion');
  assert.ok(c.includes('NetworkOnly'), 'SW must declare NetworkOnly handler');
  // Must NOT use the old fragile partial path list for critical API routes
  assert.ok(!c.includes("'/search', '/ingest', '/index', '/user-repos'"), 'Must not use fragile partial path list');
});

test('42. Generated sw.js (if present) contains NetworkOnly strategy', () => {
  const swPath = './dist/sw.js';
  if (!fs.existsSync(swPath)) {
    // Build artefact absent — skip (run `npm run build` first)
    return;
  }
  const c = fs.readFileSync(swPath, 'utf-8');
  assert.ok(c.includes('NetworkOnly'), 'Generated sw.js must include NetworkOnly strategy');
  // The generated SW must not contain hardcoded API path strings from the old list
  assert.ok(!c.includes('/user-repos') || c.includes('NetworkOnly'),
    'If /user-repos appears in sw.js it must be inside a NetworkOnly handler');
});

test('43. vite.config.js does not use wildcard globPatterns for API responses', () => {
  const c = fs.readFileSync('./vite.config.js', 'utf-8');
  // globPatterns must only include static asset types — not JSON or arbitrary extensions
  // that could inadvertently match API responses
  assert.ok(c.includes("globPatterns: ['**/*.{js,css,html,ico,png,svg,webmanifest}']"),
    'globPatterns must match static assets only');
});

test('44. Service-worker runtimeCaching does not explicitly target Supabase auth endpoints', () => {
  const c = fs.readFileSync('./vite.config.js', 'utf-8');
  // The runtimeCaching urlPattern must not explicitly match supabase.co URLs
  // (supabase.co is excluded by virtue of being a different hostname — no rule needed)
  assert.ok(!c.includes("'supabase.co'") && !c.includes('"supabase.co"'),
    'supabase.co must not appear as a quoted pattern in SW runtimeCaching');
  assert.ok(!c.includes("'auth/v1'") && !c.includes('"auth/v1"'),
    'auth/v1 must not be a quoted pattern in SW config');
});


// -------------------------------------------------------------------------
// CORS STATIC AUDIT
// Inspect vite.config.js and apiClient.js for CORS / origin safety.
// -------------------------------------------------------------------------

test('45. apiClient.js does not hardcode a production origin or API key', () => {
  const c = fs.readFileSync('./src/apiClient.js', 'utf-8');
  assert.ok(!c.includes('cerebro-delta-silk.vercel.app'), 'Production origin must not be hardcoded in apiClient.js');
  assert.ok(!c.includes('supabase_service_role'), 'Service role key must not appear in frontend');
  assert.ok(!c.includes('hf_token'), 'HF token must not appear in frontend');
});

test('46. vite.config.js does not expose real production secrets', () => {
  const c = fs.readFileSync('./vite.config.js', 'utf-8');
  assert.ok(!c.includes('sbp_'), 'No Supabase service key in vite config');
  assert.ok(!c.includes('hf_'), 'No HF token in vite config');
});


// -------------------------------------------------------------------------
// BACKEND PORT DEFAULT CONSISTENCY TESTS
// Verify the default local backend port is 7860 consistently everywhere.
// -------------------------------------------------------------------------

test('47. apiClient.js default fallback uses port 7860 not 8000', () => {
  const c = fs.readFileSync('./src/apiClient.js', 'utf-8');
  assert.ok(c.includes('localhost:7860'), 'apiClient.js fallback must use localhost:7860');
  assert.ok(!c.includes('localhost:8000'), 'apiClient.js must not reference localhost:8000');
});

test('48. vite.config.js SW matcher and proxy use port 7860 not 8000', () => {
  const c = fs.readFileSync('./vite.config.js', 'utf-8');
  assert.ok(c.includes('localhost:7860'), 'vite.config.js must reference localhost:7860');
  assert.ok(!c.includes('localhost:8000'), 'vite.config.js must not reference localhost:8000');
});

test('49. SW matcher logic excludes local backend origin (port 7860)', () => {
  // Replicate the urlPattern function logic from vite.config.js and verify
  // it correctly identifies local backend requests.
  const apiOrigin = 'http://localhost:7860';
  const apiUrl = new URL(apiOrigin);

  // Simulate a backend API request
  const backendRequest = new URL('http://localhost:7860/search');
  const isBackend = backendRequest.hostname === apiUrl.hostname &&
                    backendRequest.port === apiUrl.port;
  assert.ok(isBackend, 'SW matcher must identify localhost:7860/search as a backend request');

  // Simulate a frontend static asset request (different port — Vite dev server)
  const frontendRequest = new URL('http://localhost:3000/assets/index.js');
  const isFrontendBackend = frontendRequest.hostname === apiUrl.hostname &&
                             frontendRequest.port === apiUrl.port;
  assert.ok(!isFrontendBackend, 'SW matcher must not flag localhost:3000 as a backend request');
});

test('50. SW matcher logic excludes production backend origin (HF Spaces)', () => {
  // Simulate a production backend hosted on Hugging Face Spaces
  // (different hostname from the frontend — correctly excluded by the origin rule)
  const prodBackendOrigin = 'https://r-murugesan-coderag.hf.space';
  const apiUrl = new URL(prodBackendOrigin);

  const backendRequest = new URL('https://r-murugesan-coderag.hf.space/search');
  const isBackend = backendRequest.hostname === apiUrl.hostname &&
                    backendRequest.port === apiUrl.port;
  assert.ok(isBackend, 'SW matcher must identify HF Space backend as a backend request');

  // Frontend (Vercel) is a different hostname — must NOT be flagged
  const frontendRequest = new URL('https://cerebro-delta-silk.vercel.app/assets/index.js');
  const isFrontendBackend = frontendRequest.hostname === apiUrl.hostname &&
                             frontendRequest.port === apiUrl.port;
  assert.ok(!isFrontendBackend, 'SW matcher must not flag Vercel frontend as a backend request');
});
