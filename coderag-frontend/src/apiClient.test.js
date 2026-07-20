import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
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
