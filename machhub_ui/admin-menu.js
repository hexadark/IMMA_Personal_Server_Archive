(function () {
  const menuItems = [
    { section: '메인' },
    { id: 'dashboard', label: '대시보드', icon: 'ri-dashboard-line', href: '/admin-ui' },
    { id: 'quotes', label: '견적 요청 관리', icon: 'ri-file-list-3-line', href: '/admin-operations' },
    { id: 'database', label: '통합 DB', icon: 'ri-database-2-line', href: '/admin-control-center#database' },
    { section: '운영 데이터' },
    { id: 'ai', label: 'AI 분석 로그', icon: 'ri-brain-line', href: '/admin-control-center#ai' },
    { id: 'matching', label: '매칭 관리', icon: 'ri-links-line', href: '/admin-control-center#matching' },
    { id: 'contracts', label: '계약·결제', icon: 'ri-shield-check-line', href: '/admin-control-center#contracts' },
    { id: 'production', label: '생산·지연', icon: 'ri-line-chart-line', href: '/admin-control-center#production' },
    { id: 'delivery', label: '납품·정산 로그', icon: 'ri-truck-line', href: '/admin-control-center#delivery' },
    { id: 'issues', label: '문의·문제제기', icon: 'ri-customer-service-2-line', href: '/admin-control-center#issues' },
    { id: 'reviews', label: '리뷰·평점', icon: 'ri-star-line', href: '/admin-control-center#reviews' },
    { section: '시스템' },
    { id: 'system', label: '시스템 모니터링', icon: 'ri-server-line', href: '/admin-control-center#system' },
    { id: 'settings', label: '설정', icon: 'ri-settings-4-line', href: '/admin-control-center#system' }
  ];

  // FastAPI 라우터 매핑 기준: 페이지 식별자는 path의 마지막 segment.
  // / → ''   /admin-ui → 'admin-ui'   /admin-control-center → 'admin-control-center'
  function currentPage() {
    return location.pathname.split('/').pop() || 'admin-ui';
  }

  function currentActiveId() {
    const page = currentPage();
    if (page === 'admin-ui') return 'dashboard';
    if (page === 'admin-operations') return 'quotes';
    if (page === 'admin-control-center') {
      return (location.hash || '#database').slice(1) || 'database';
    }
    return '';
  }

  // admin-control-center 페이지 안에서 메뉴 클릭은 같은 페이지의 앵커로 처리.
  function localHref(href) {
    if (currentPage() !== 'admin-control-center') return href;
    return href.replace(/^\/admin-control-center/, '') || '#database';
  }

  function renderMenu(host) {
    const withLogo = host.dataset.adminLogo === 'true';
    const nav = menuItems.map((item) => {
      if (item.section) return `<div class="admin-menu-section">${item.section}</div>`;
      return `
        <a class="admin-menu-link" data-admin-menu-id="${item.id}" href="${localHref(item.href)}">
          <i class="${item.icon}"></i>
          <span>${item.label}</span>
        </a>
      `;
    }).join('');

    host.innerHTML = `
      ${withLogo ? '<a class="admin-menu-logo" href="/admin-ui">IMM<span>A</span></a>' : ''}
      <nav class="admin-unified-nav">${nav}</nav>
      <div class="admin-menu-user">
        <strong>관리자</strong>
        <span>운영팀 · 플랫폼 상태 정상</span>
      </div>
      <div class="admin-demo-notice">
        <i class="ri-flask-line"></i>
        <span>Demo UI · 일부 데이터는 시연용 샘플입니다</span>
      </div>
    `;
  }

  function syncActive() {
    const activeId = currentActiveId();
    document.querySelectorAll('[data-admin-menu-id]').forEach((link) => {
      link.classList.toggle('active', link.dataset.adminMenuId === activeId);
    });
  }

  function initAdminMenu() {
    document.querySelectorAll('[data-admin-menu]').forEach(renderMenu);
    syncActive();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAdminMenu);
  } else {
    initAdminMenu();
  }

  window.addEventListener('hashchange', syncActive);
}());
