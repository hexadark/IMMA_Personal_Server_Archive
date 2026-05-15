(function () {
  'use strict';

  window.imma = window.imma || {};

  const TOKEN_KEY = 'imma_access_token';
  const USER_KEY = 'imma_user';
  let logoutStarted = false;
  let verifyPromise = null;

  function parseJson(value, fallback = null) {
    try { return value ? JSON.parse(value) : fallback; }
    catch (_) { return fallback; }
  }

  function decodeJwtPayload(token) {
    try {
      const base64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
      const binary = window.atob(base64);
      const bytes = new Uint8Array([...binary].map(c => c.charCodeAt(0)));
      return JSON.parse(new TextDecoder('utf-8').decode(bytes));
    } catch (_) {
      return null;
    }
  }

  function isTokenExpired(token) {
    const payload = decodeJwtPayload(token);
    if (!payload || !payload.exp) return true;
    return payload.exp <= Math.floor(Date.now() / 1000) + 30;
  }

  function getToken() {
    return localStorage.getItem(TOKEN_KEY);
  }

  function getUser() {
    return parseJson(localStorage.getItem(USER_KEY));
  }

  function setSession(token, user) {
    if (!token || !user) throw new Error('세션 저장에 필요한 token/user가 없습니다');
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  }

  function clearUserScopedState(userId) {
    if (!userId) return;
    const prefix = `imma:${userId}:`;
    Object.keys(localStorage).forEach(key => {
      if (key.startsWith(prefix)) localStorage.removeItem(key);
    });
  }

  function clearSession() {
    const user = getUser();
    if (user && user.id) clearUserScopedState(user.id);
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  }

  function scopedKey(...parts) {
    const user = getUser();
    if (!user || !user.id) throw new Error('로그인이 필요합니다');
    return ['imma', user.id, ...parts].join(':');
  }

  function redirectForRole(role) {
    if (role === 'buyer') return '/client';
    if (role === 'supplier') return '/supplier';
    if (role === 'admin') return '/admin-ui';
    return '/';
  }

  function logout(reason = 'logout') {
    if (logoutStarted) return;
    logoutStarted = true;
    clearSession();
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.href = `/?reason=${encodeURIComponent(reason)}&next=${next}`;
  }

  async function verifySession(options = {}) {
    const token = getToken();
    if (!token || isTokenExpired(token)) {
      if (options.required !== false) logout('expired');
      throw new Error('세션이 만료되었습니다');
    }

    if (!verifyPromise) {
      verifyPromise = fetch('/api/me', { headers: { Authorization: `Bearer ${token}` } })
        .then(async res => {
          if (res.status === 401) {
            logout('unauthorized');
            throw new Error('인증이 필요합니다');
          }
          if (!res.ok) throw new Error('세션 확인 실패');
          const user = await res.json();
          localStorage.setItem(USER_KEY, JSON.stringify(user));
          return user;
        })
        .finally(() => { verifyPromise = null; });
    }
    return verifyPromise;
  }

  async function requireRole(roles) {
    const allowed = Array.isArray(roles) ? roles : [roles];
    const user = await verifySession({ required: true });
    if (!allowed.includes(user.role)) {
      if (window.imma.toast) window.imma.toast('접근 권한이 없습니다.', 'error');
      window.location.href = redirectForRole(user.role);
      throw new Error('권한 없음');
    }
    return user;
  }

  function requireAdmin() {
    return requireRole('admin');
  }

  async function login(loginId, password, role) {
    const endpoint = role === 'admin' ? '/api/admin/login' : '/api/login';
    // role 명시 분리 — admin 외에는 expected_role 을 body 에 전달
    const body = role === 'admin'
      ? { login_id: loginId, password }
      : { login_id: loginId, password, expected_role: role };
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      const error = new Error((data && (data.detail || data.message)) || `로그인 실패: ${res.status}`);
      error.status = res.status;
      throw error;
    }
    setSession(data.access_token, data.user);
    try {
      const verified = await verifySession();
      return verified;
    } catch (_) {
      return data.user;
    }
  }

  Object.assign(window.imma, {
    getToken,
    getUser,
    setSession,
    clearSession,
    clearUserScopedState,
    scopedKey,
    redirectForRole,
    logout,
    verifySession,
    requireRole,
    requireAdmin,
    decodeJwtPayload,
    isTokenExpired,
    login,
  });
})();
