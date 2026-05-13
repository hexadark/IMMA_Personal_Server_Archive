(function () {
  'use strict';

  window.imma = window.imma || {};

  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function toast(message, type = 'info', timeoutMs = 4200) {
    const existing = document.querySelector('.imma-toast-stack');
    const stack = existing || document.createElement('div');
    if (!existing) {
      stack.className = 'imma-toast-stack';
      document.body.appendChild(stack);
    }
    const item = document.createElement('div');
    item.className = `imma-toast imma-toast-${type}`;
    item.textContent = message;
    stack.appendChild(item);
    window.setTimeout(() => item.classList.add('visible'), 10);
    window.setTimeout(() => {
      item.classList.remove('visible');
      window.setTimeout(() => item.remove(), 250);
    }, timeoutMs);
  }

  function formatCurrency(value, currency = 'KRW') {
    if (value === null || value === undefined || value === '') return '-';
    const n = Number(value);
    if (!Number.isFinite(n)) return String(value);
    try {
      return new Intl.NumberFormat('ko-KR', { style: 'currency', currency, maximumFractionDigits: 0 }).format(n);
    } catch (_) {
      return `${n.toLocaleString('ko-KR')} ${currency}`;
    }
  }

  function formatDate(value) {
    if (!value) return '-';
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value).slice(0, 10) || '-';
    return d.toLocaleDateString('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit' });
  }

  function setLoading(el, isLoading, label) {
    if (!el) return;
    if (isLoading) {
      if (!el.dataset.originalText) el.dataset.originalText = el.textContent || '';
      el.disabled = true;
      el.setAttribute('aria-busy', 'true');
      el.classList.add('is-loading');
      if (label) el.textContent = label;
    } else {
      el.disabled = false;
      el.removeAttribute('aria-busy');
      el.classList.remove('is-loading');
      if (el.dataset.originalText) el.textContent = el.dataset.originalText;
      delete el.dataset.originalText;
    }
  }

  function roleLabel(role) {
    return role === 'buyer' ? '발주자' : role === 'supplier' ? '공급사' : role === 'admin' ? '관리자' : '방문자';
  }

  function displayName(user) {
    if (!user) return '';
    return user.name || user.contact_name || user.company_name || user.login_id || '';
  }

  function getSessionTarget() {
    const direct = document.querySelector('[data-imma-session]');
    if (direct) return direct;
    const selectors = [
      'header .header-actions',
      'header .header-actions-right',
      'header .topbar-actions',
      'header .mw-actions',
      '.mw-app-topbar .mw-actions',
      '.dash-topbar .topbar-actions',
      'header nav',
      '.navbar',
      'header'
    ];
    for (const selector of selectors) {
      const el = document.querySelector(selector);
      if (el) return el;
    }
    return null;
  }

  function renderSessionHeader() {
    const user = window.imma.getUser ? window.imma.getUser() : null;
    const target = getSessionTarget();
    if (!target || target.dataset.immaSessionRendered === 'true') return;

    // 비로그인 공개 페이지에는 이미 로그인/회원가입 CTA가 있으므로 중복 링크를 만들지 않는다.
    if (!user) return;

    target.dataset.immaSessionRendered = 'true';
    const box = document.createElement('div');
    box.className = 'imma-session-box';
    box.setAttribute('data-imma-session-box', 'true');

    const home = window.imma.redirectForRole ? window.imma.redirectForRole(user.role) : '/';
    box.innerHTML = `
      <span class="imma-session-user">${escapeHtml(displayName(user))}님 · ${escapeHtml(roleLabel(user.role))}</span>
      <a class="imma-session-link" href="${escapeHtml(home)}">대시보드</a>
      <button class="imma-session-logout" type="button">로그아웃</button>
    `;
    box.querySelector('button').addEventListener('click', () => window.imma.logout && window.imma.logout('manual'));

    if (target.hasAttribute('data-imma-session')) target.appendChild(box);
    else target.appendChild(box);
  }

  function getQueryParam(name) {
    return new URLSearchParams(window.location.search).get(name);
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  Object.assign(window.imma, {
    escapeHtml,
    toast,
    formatCurrency,
    formatDate,
    setLoading,
    renderSessionHeader,
    getQueryParam,
    asArray,
    displayName,
    roleLabel,
  });
})();
