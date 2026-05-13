(function () {
  'use strict';

  window.imma = window.imma || {};

  function normalizeDetail(data, fallback) {
    if (!data) return fallback;
    if (typeof data === 'string') return data;
    if (typeof data.detail === 'string') return data.detail;
    if (Array.isArray(data.detail)) {
      return data.detail.map(item => item.msg || item.message || JSON.stringify(item)).join('\n');
    }
    if (data.detail && typeof data.detail === 'object') {
      return data.detail.message || JSON.stringify(data.detail);
    }
    if (data.message) return data.message;
    return fallback;
  }

  async function parseBody(res) {
    const contentType = res.headers.get('content-type') || '';
    if (contentType.includes('application/json')) return res.json().catch(() => null);
    const text = await res.text().catch(() => '');
    return text ? { detail: text } : null;
  }

  async function fetchRaw(path, options = {}) {
    const token = window.imma.getToken && window.imma.getToken();
    const headers = new Headers(options.headers || {});
    if (token) headers.set('Authorization', `Bearer ${token}`);
    const body = options.body;
    const isForm = body instanceof FormData || body instanceof URLSearchParams;
    if (body && !isForm && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');

    let res;
    try {
      res = await fetch(path, { ...options, headers });
    } catch (cause) {
      const error = new Error('서버에 연결할 수 없습니다');
      error.code = 'NETWORK_ERROR';
      error.cause = cause;
      throw error;
    }

    if (res.status === 401) {
      if (window.imma.logout) window.imma.logout('unauthorized');
      const error = new Error('인증이 필요합니다');
      error.status = 401;
      throw error;
    }
    return res;
  }

  async function apiJson(path, options = {}) {
    const opts = { ...options };
    if (opts.body && !(opts.body instanceof FormData) && !(opts.body instanceof URLSearchParams) && typeof opts.body !== 'string') {
      opts.body = JSON.stringify(opts.body);
    }
    const res = await fetchRaw(path, opts);
    const data = await parseBody(res);
    if (!res.ok) {
      const error = new Error(normalizeDetail(data, `요청 실패: ${res.status}`));
      error.status = res.status;
      error.data = data;
      throw error;
    }
    return data;
  }

  async function apiForm(path, formData, options = {}) {
    if (!(formData instanceof FormData)) throw new Error('apiForm은 FormData만 받을 수 있습니다');
    const res = await fetchRaw(path, { ...options, method: options.method || 'POST', body: formData });
    const data = await parseBody(res);
    if (!res.ok) {
      const error = new Error(normalizeDetail(data, `요청 실패: ${res.status}`));
      error.status = res.status;
      error.data = data;
      throw error;
    }
    return data;
  }

  Object.assign(window.imma, { fetchRaw, apiJson, apiForm });
})();
