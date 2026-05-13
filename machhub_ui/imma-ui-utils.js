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
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleDateString('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit' });
  }

  function setLoading(el, isLoading, label) {
    if (!el) return;
    if (isLoading) {
      if (!el.dataset.originalText) el.dataset.originalText = el.textContent || '';
      el.disabled = true;
      el.setAttribute('aria-busy', 'true');
      if (label) el.textContent = label;
    } else {
      el.disabled = false;
      el.removeAttribute('aria-busy');
      if (el.dataset.originalText) el.textContent = el.dataset.originalText;
      delete el.dataset.originalText;
    }
  }

  function roleLabel(role) {
    return role === 'buyer' ? '발주자' : role === 'supplier' ? '공급사' : role === 'admin' ? '관리자' : '방문자';
  }

  function displayName(user) {
    if (!user) return '';
    return user.name || user.company_name || user.login_id || '';
  }

  function renderSessionHeader() {
    const user = window.imma.getUser ? window.imma.getUser() : null;
    const anchors = Array.from(document.querySelectorAll('a[href="/"], a[href="/client"], a[href="/supplier"], a[href="/admin-ui"]'));
    const target = document.querySelector('[data-imma-session]') || document.querySelector('header nav') || document.querySelector('.navbar') || document.querySelector('header');
    if (!target || target.dataset.immaSessionRendered === 'true') return;
    target.dataset.immaSessionRendered = 'true';

    const box = document.createElement('div');
    box.className = 'imma-session-box';
    box.setAttribute('data-imma-session', 'true');
    if (user) {
      const home = window.imma.redirectForRole ? window.imma.redirectForRole(user.role) : '/';
      box.innerHTML = `
        <span class="imma-session-user">${escapeHtml(displayName(user))}님 · ${escapeHtml(roleLabel(user.role))}</span>
        <a class="imma-session-link" href="${escapeHtml(home)}">대시보드</a>
        <button class="imma-session-logout" type="button">로그아웃</button>
      `;
      box.querySelector('button').addEventListener('click', () => window.imma.logout && window.imma.logout('manual'));
    } else {
      box.innerHTML = `<a class="imma-session-link" href="/">로그인</a>`;
    }

    if (target.tagName && target.tagName.toLowerCase() === 'nav') target.appendChild(box);
    else target.insertBefore(box, target.firstChild);

    anchors.forEach(a => {
      if (user && a.getAttribute('href') === '/') a.dataset.immaPublicHome = 'true';
    });
  }

  function ensurePanel(title, description) {
    let panel = document.getElementById('imma-phase1-panel');
    if (panel) return panel;
    panel = document.createElement('section');
    panel.id = 'imma-phase1-panel';
    panel.className = 'imma-phase1-panel';
    panel.innerHTML = `
      <div class="imma-phase1-head">
        <div>
          <p class="imma-eyebrow">IMMA Phase 1 실 API</p>
          <h2>${escapeHtml(title)}</h2>
          ${description ? `<p>${escapeHtml(description)}</p>` : ''}
        </div>
      </div>
      <div class="imma-phase1-body"></div>
    `;
    const main = document.querySelector('main') || document.body;
    main.insertBefore(panel, main.firstChild);
    return panel;
  }

  function setPanelContent(title, description, html) {
    const panel = ensurePanel(title, description);
    const body = panel.querySelector('.imma-phase1-body');
    body.innerHTML = html;
    return body;
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
    ensurePanel,
    setPanelContent,
    getQueryParam,
    asArray,
    displayName,
    roleLabel,
  });
})();
