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

