(function () {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const h = value => window.imma.escapeHtml(value);

  function todayPlus(days) {
    const d = new Date();
    d.setDate(d.getDate() + days);
    return d.toISOString().slice(0, 10);
  }

  function statusBadge(status) {
    return `<span class="imma-badge imma-badge-${h(status || 'none')}">${h(status || '-')}</span>`;
  }

  function safeJson(value) {
    try { return JSON.stringify(value).replace(/</g, '\\u003c'); }
    catch (_) { return '{}'; }
  }

  function store(key, value) {
    localStorage.setItem(key, typeof value === 'string' ? value : JSON.stringify(value));
  }

  function read(key, fallback = null) {
    const v = localStorage.getItem(key);
    if (v === null) return fallback;
    try { return JSON.parse(v); } catch (_) { return v; }
  }

  function classifyReason(reason) {
    const r = String(reason || '');
    if (r.includes('[공정순서 위반]') || r.includes('[unsupported]')) return 'danger';
    if (r.includes('[WARN_') || r.includes('의심') || r.includes('[공정순서 권장위반]')) return 'warning';
    if (r.includes('[INFO_')) return 'info';
    return 'neutral';
  }

  function cleanReason(reason) {
    return String(reason || '')
      .replace('[INFO_CATEGORY_FALLBACK]', '카테고리 대체:')
      .replace('[INFO_PARENT_FALLBACK]', '상위 카테고리 대체:')
      .replace('[WARN_EQUIPMENT_CAPABILITY_MISSING]', '장비 정보 부족:')
      .replace('[공정 달성범위 의심·재질override]', '재질 override 정밀도 확인:')
      .replace('[공정 달성범위 의심]', '정밀도 확인:')
      .replace('[공정순서 위반]', '공정순서 위반:')
      .replace('[공정순서 권장위반]', '권장순서 확인:')
      .replace('[unsupported]', '지원 불가:')
      .trim();
  }

  function renderReason(reason) {
    return `<span class="imma-match-chip ${classifyReason(reason)}">${h(cleanReason(reason))}</span>`;
  }

  function renderCandidate(cand, rfqId, partId) {
    const reasons = Array.isArray(cand.match_reasons) ? cand.match_reasons : [];
    const score = cand.total_score == null ? '-' : `${Math.round(Number(cand.total_score) * 100)}%`;
    const payload = {
      rfq_id: rfqId,
      rfq_part_id: cand.rfq_part_id || partId,
      company_id: cand.company_id || cand.company_code,
      company_name: cand.company_name,
      match_run_id: cand.match_run_id,
      rank_no: cand.rank_no,
    };
    const feasible = cand.availability_info && cand.availability_info.delivery_feasible === true;
    return `
      <article class="imma-card imma-candidate-card" data-company-id="${h(payload.company_id)}">
        <div class="imma-card-row">
          <div>
            <p class="imma-eyebrow">순위 ${h(cand.rank_no || '-')}</p>
            <h3>${h(cand.company_name || '업체명 없음')}</h3>
          </div>
          <strong class="imma-score">${h(score)}</strong>
        </div>
        <div class="imma-card-meta">
          <span>${cand.equipment_verified ? '장비 검증' : '장비 확인 필요'}</span>
          <span>${feasible ? '납기 가능' : '납기 확인 필요'}</span>
          <span>평점 ${h(cand.avg_rating ?? '-')} · 리뷰 ${h(cand.review_count ?? 0)}</span>
        </div>
        <div class="imma-chip-wrap">${reasons.map(renderReason).join('') || '<span class="imma-muted">표시할 매칭 사유가 없습니다.</span>'}</div>
        <button type="button" class="imma-btn select-candidate" data-candidate="${h(safeJson(payload))}">후보로 표시</button>
      </article>`;
  }

  function renderPart(part, rfqId) {
    const partId = part.rfq_part_id || part.part_id || '';
    if (part.status === 'rejected') {
      return `
        <section class="imma-card imma-part-card">
          <h3>${h(part.part_name || '부품')}</h3>
          <p class="imma-danger">${h(part.message || part.rejection_reason || '매칭 불가')}</p>
          <div class="imma-chip-wrap">${(part.missing_fields || []).map(x => `<span class="imma-match-chip danger">${h(x)}</span>`).join('')}</div>
        </section>`;
    }
    const rec = Array.isArray(part.recommended_candidates) ? part.recommended_candidates : [];
    const cond = Array.isArray(part.conditional_candidates) ? part.conditional_candidates : [];
    return `
      <section class="imma-part-section">
        <div class="imma-section-title">
          <h3>${h(part.part_name || '부품')}</h3>
          <p>RFQ Part ID: ${h(partId || '-')}</p>
        </div>
        <h4>추천 후보</h4>
        <div class="imma-grid">${rec.map(c => renderCandidate(c, rfqId, partId)).join('') || '<p class="imma-empty">추천 후보가 없습니다.</p>'}</div>
        <details class="imma-details" ${cond.length ? '' : 'open'}>
          <summary>조건부 후보 ${cond.length}건</summary>
          <div class="imma-grid">${cond.map(c => renderCandidate(c, rfqId, partId)).join('') || '<p class="imma-empty">조건부 후보가 없습니다.</p>'}</div>
        </details>
      </section>`;
  }

  async function initLanding() {
    window.imma.renderSessionHeader();
    const body = window.imma.setPanelContent('실 API 로그인', 'buyer, supplier, admin 계정으로 실제 JWT 세션을 생성합니다.', `
      <form id="imma-login-form" class="imma-form">
        <label>역할
          <select name="role">
            <option value="buyer">Buyer</option>
            <option value="supplier">Supplier</option>
            <option value="admin">Admin</option>
          </select>
        </label>
        <label>ID <input name="login_id" value="kim_cheolsu" required></label>
        <label>Password <input name="password" type="password" value="demo1234" required></label>
        <button class="imma-btn" type="submit">로그인</button>
      </form>
      <p class="imma-muted">관리자: admin / test1234</p>
    `);
    const role = $('select[name="role"]', body);
    role.addEventListener('change', () => {
      const id = $('input[name="login_id"]', body);
      const pw = $('input[name="password"]', body);
      if (role.value === 'admin') { id.value = 'admin'; pw.value = 'test1234'; }
      else if (role.value === 'buyer') { id.value = 'kim_cheolsu'; pw.value = 'demo1234'; }
      else { id.value = ''; pw.value = 'demo1234'; }
    });
    $('#imma-login-form', body).addEventListener('submit', async e => {
      e.preventDefault();
      const btn = e.submitter || $('button', body);
      window.imma.setLoading(btn, true, '로그인 중...');
      try {
        const fd = new FormData(e.currentTarget);
        const user = await window.imma.login(fd.get('login_id'), fd.get('password'), fd.get('role'));
        window.imma.toast('로그인되었습니다.', 'success');
        window.location.href = window.imma.redirectForRole(user.role);
      } catch (err) {
        window.imma.toast(err.message, 'error');
      } finally {
        window.imma.setLoading(btn, false);
      }
    });
  }

  async function initClientRegister() {
    window.imma.renderSessionHeader();
    const body = window.imma.setPanelContent('Buyer 회원가입', '가입 후 자동 로그인합니다.', `
      <form id="imma-buyer-signup" class="imma-form">
        <label>ID <input name="login_id" required></label>
        <label>비밀번호 <input name="password" type="password" value="demo1234" required></label>
        <label>이름 <input name="name" required></label>
        <label>회사명 <input name="company_name"></label>
        <label>이메일 <input name="email" type="email" required></label>
        <label>전화 <input name="phone"></label>
        <button class="imma-btn" type="submit">가입 후 로그인</button>
      </form>
    `);
    $('#imma-buyer-signup', body).addEventListener('submit', async e => {
      e.preventDefault();
      const btn = e.submitter;
      window.imma.setLoading(btn, true, '가입 중...');
      try {
        const fd = new FormData(e.currentTarget);
        const payload = Object.fromEntries(fd.entries());
        payload.role = 'buyer';
        await window.imma.apiJson('/signup', { method: 'POST', body: payload });
        await window.imma.login(payload.login_id, payload.password, 'buyer');
        window.location.href = '/client';
      } catch (err) { window.imma.toast(err.message, 'error'); }
      finally { window.imma.setLoading(btn, false); }
    });
  }

  async function initSupplierRegister() {
    window.imma.renderSessionHeader();
    const body = window.imma.setPanelContent('Supplier 회원가입', '담당자명과 회사명을 분리해 저장하고, 가입 후 자동 로그인합니다.', `
      <form id="imma-supplier-signup" class="imma-form">
        <label>ID <input name="login_id" required></label>
        <label>비밀번호 <input name="password" type="password" value="demo1234" required></label>
        <label>담당자명 <input name="name" required></label>
        <label>회사명 <input name="company_name" required></label>
        <label>이메일 <input name="email" type="email" required></label>
        <label>전화 <input name="phone"></label>
        <label>주요 지역 <input name="region" placeholder="경기"></label>
        <button class="imma-btn" type="submit">가입 후 로그인</button>
      </form>
    `);
    $('#imma-supplier-signup', body).addEventListener('submit', async e => {
      e.preventDefault();
      const btn = e.submitter;
      window.imma.setLoading(btn, true, '가입 중...');
      try {
        const fd = new FormData(e.currentTarget);
        const payload = Object.fromEntries(fd.entries());
        payload.role = 'supplier';
        await window.imma.apiJson('/signup', { method: 'POST', body: payload });
        const user = await window.imma.login(payload.login_id, payload.password, 'supplier');
        try {
          await window.imma.apiJson('/api/company/profile', { method: 'PUT', body: {
            company_id: user.id,
            company_name: payload.company_name,
            main_email: payload.email,
            main_phone: payload.phone,
            region: payload.region || null,
          }});
        } catch (profileErr) {
          console.warn('profile 보강 실패', profileErr);
        }
        window.imma.toast('가입되었습니다. 관리자 승인 화면에서 검수할 수 있습니다.', 'success');
        window.location.href = '/supplier';
      } catch (err) { window.imma.toast(err.message, 'error'); }
      finally { window.imma.setLoading(btn, false); }
    });
  }

  async function initClientDashboard() {
    const user = await window.imma.requireRole('buyer');
    window.imma.renderSessionHeader();
    const body = window.imma.setPanelContent('Buyer 대시보드', 'RFQ 상태를 실제 /rfqs 응답에서 집계합니다.', '<p class="imma-loading">불러오는 중...</p>');
    try {
      const data = await window.imma.apiJson('/rfqs');
      const rfqs = data.rfqs || [];
      const counts = rfqs.reduce((acc, r) => { acc[r.status || 'unknown'] = (acc[r.status || 'unknown'] || 0) + 1; return acc; }, {});
      body.innerHTML = `
        <div class="imma-kpis">
          <div class="imma-kpi"><strong>${rfqs.length}</strong><span>전체 RFQ</span></div>
          <div class="imma-kpi"><strong>${counts.open || 0}</strong><span>open</span></div>
          <div class="imma-kpi"><strong>${counts.quoted || 0}</strong><span>quoted</span></div>
          <div class="imma-kpi"><strong>${counts.ordered || 0}</strong><span>ordered</span></div>
        </div>
        <p><a class="imma-btn" href="/quote-request">새 견적 요청</a></p>
        <div class="imma-table-wrap"><table class="imma-table"><thead><tr><th>RFQ</th><th>상태</th><th>소재</th><th>납기</th><th></th></tr></thead><tbody>
        ${rfqs.map(r => `<tr><td>${h(r.rfq_no || r.id)}</td><td>${statusBadge(r.status)}</td><td>${h(r.material || '-')}</td><td>${h(r.due_date || '-')}</td><td><a href="/order-management?rfq_id=${encodeURIComponent(r.id)}">견적 보기</a></td></tr>`).join('')}
        </tbody></table></div>`;
    } catch (err) {
      body.innerHTML = `<p class="imma-danger">${h(err.message)}</p>`;
    }
  }

  function createVlmProgress(container, onFallback, onRetry) {
    let startedAt = Date.now();
    let timer = null;
    const steps = [
      [0, '도면 업로드 처리 중'],
      [30, 'AI 분석 준비 중. 최초 분석은 시간이 걸립니다'],
      [90, '딥러닝 모델 분석 중'],
      [180, '최종 추출 중'],
      [240, '분석이 길어지고 있습니다. 실패 시 사전 분석 결과로 계속할 수 있습니다'],
      [300, 'AI 분석 시간 초과 — 사전 분석 결과로 전환할 수 있습니다'],
    ];
    function messageFor(sec) {
      let msg = steps[0][1];
      steps.forEach(([at, text]) => { if (sec >= at) msg = text; });
      return msg;
    }
    function render() {
      const sec = Math.floor((Date.now() - startedAt) / 1000);
      container.innerHTML = `<p>${h(messageFor(sec))}</p><small>${sec}초 경과</small>`;
    }
    return {
      start() { startedAt = Date.now(); render(); timer = window.setInterval(render, 1000); },
      stop() { if (timer) window.clearInterval(timer); timer = null; },
      showFallback(message) {
        this.stop();
        container.innerHTML = `
          <div class="imma-fallback-box">
            <p class="imma-warning">${h(message || 'AI 분석 시간 초과 — 사전 분석 결과로 계속할 수 있습니다')}</p>
            <button type="button" class="imma-btn" data-vlm-fallback>사전 분석 결과로 계속</button>
            <button type="button" class="imma-btn secondary" data-vlm-retry>다시 시도</button>
          </div>`;
        $('[data-vlm-fallback]', container).addEventListener('click', onFallback);
        $('[data-vlm-retry]', container).addEventListener('click', onRetry);
      },
    };
  }

  async function findFixtureDrawingId() {
    const fromQuery = window.imma.getQueryParam('fixture_drawing_id');
    if (fromQuery) return fromQuery;
    const fromStore = localStorage.getItem('imma_fixture_drawing_id');
    if (fromStore) return fromStore;
    const results = await window.imma.apiJson('/vlm-results');
    const rows = results.data || [];
    const found = rows.find(r => String(r.drawing_no || '').includes('sample_00015') || String(r.id || '').includes('sample_00015')) || rows[0];
    if (!found) throw new Error('fixture 도면이 없습니다. sample_00015 VLM 결과를 drawings 테이블에 먼저 넣어 주세요.');
    return found.id;
  }

  async function initQuoteRequest() {
    await window.imma.requireRole('buyer');
    window.imma.renderSessionHeader();
    const body = window.imma.setPanelContent('도면 분석 및 매칭', 'VLM은 /vlm/analyze-upload 경로로 호출하고, 502/504 때 fixture 전환 또는 재시도를 선택합니다.', `
      <form id="imma-quote-flow" class="imma-form">
        <label>도면 이미지 <input name="image" type="file" accept="image/*" required></label>
        <label>수량 <input name="order_quantity" type="number" value="100" min="1"></label>
        <label>요청 납기 <input name="requested_delivery_date" type="date" value="${todayPlus(30)}"></label>
        <label>예산 <input name="budget_amount" type="number" value="8000000"></label>
        <label>메모 <textarea name="note">Phase 1 시연용 RFQ</textarea></label>
        <button class="imma-btn" type="submit">VLM 분석 후 매칭 실행</button>
      </form>
      <div id="imma-vlm-progress" class="imma-progress-box"></div>
      <div id="imma-match-result-link"></div>
    `);
    const form = $('#imma-quote-flow', body);
    const progressEl = $('#imma-vlm-progress', body);
    const linkEl = $('#imma-match-result-link', body);

    async function executeMatch(drawingId, fd, fallbackUsed) {
      const matchPayload = {
        drawing_id: drawingId,
        order_quantity: Number(fd.get('order_quantity') || 1),
        requested_delivery_date: fd.get('requested_delivery_date') || null,
        budget_amount: fd.get('budget_amount') ? Number(fd.get('budget_amount')) : null,
        budget_currency: 'KRW',
        general_notes: { note: fd.get('note') || '', vlm_fallback_used: !!fallbackUsed },
      };
      const result = await window.imma.apiJson('/api/match-v2', { method: 'POST', body: matchPayload });
      const rfqId = result.rfq_id || (result.rfq && result.rfq.id);
      if (!rfqId) throw new Error('매칭 결과에 rfq_id가 없습니다');
      store(window.imma.scopedKey('current_rfq_id'), rfqId);
      store(window.imma.scopedKey(rfqId, 'match_result'), result);
      store(window.imma.scopedKey('current_drawing_id'), drawingId);
      linkEl.innerHTML = `<p class="imma-success">매칭이 완료되었습니다.</p><a class="imma-btn" href="/matching-ui?rfq_id=${encodeURIComponent(rfqId)}">매칭 결과 보기</a>`;
    }

    async function run(useFixture = false) {
      const fd = new FormData(form);
      let drawingId;
      if (useFixture) {
        drawingId = await findFixtureDrawingId();
        await executeMatch(drawingId, fd, true);
        return;
      }
      const upload = new FormData();
      upload.append('image', fd.get('image'));
      const vlm = await window.imma.apiForm('/vlm/analyze-upload', upload);
      drawingId = vlm.drawing_id;
      await executeMatch(drawingId, fd, false);
    }

    form.addEventListener('submit', async e => {
      e.preventDefault();
      const btn = e.submitter;
      const progress = createVlmProgress(progressEl, () => run(true).catch(err => window.imma.toast(err.message, 'error')), () => form.requestSubmit());
      window.imma.setLoading(btn, true, '분석 중...');
      progress.start();
      try {
        await run(false);
        progress.stop();
      } catch (err) {
        if (err.status === 502 || err.status === 504 || err.code === 'NETWORK_ERROR') {
          progress.showFallback(err.message);
        } else {
          progress.stop();
          window.imma.toast(err.message, 'error');
        }
      } finally {
        window.imma.setLoading(btn, false);
      }
    });
  }

  async function initMatching() {
    await window.imma.requireRole('buyer');
    window.imma.renderSessionHeader();
    const rfqId = window.imma.getQueryParam('rfq_id') || read(window.imma.scopedKey('current_rfq_id'));
    const body = window.imma.setPanelContent('매칭 결과', 'recommended와 conditional 후보를 part별로 표시합니다.', '<p class="imma-loading">불러오는 중...</p>');
    if (!rfqId) { body.innerHTML = '<p class="imma-danger">rfq_id가 없습니다.</p>'; return; }
    let result = read(window.imma.scopedKey(rfqId, 'match_result'));
    if (!result) {
      body.innerHTML = `<p class="imma-warning">저장된 match result가 없습니다. RFQ 상세만 조회합니다.</p>`;
      try { await window.imma.apiJson(`/api/rfq/${encodeURIComponent(rfqId)}`); } catch (_) {}
      return;
    }
    const parts = Array.isArray(result.parts) ? result.parts : [];
    body.innerHTML = `
      <p>RFQ ID: <code>${h(rfqId)}</code></p>
      ${parts.map(p => renderPart(p, rfqId)).join('') || '<p class="imma-empty">표시할 part가 없습니다.</p>'}
      <p><a class="imma-btn" href="/order-management?rfq_id=${encodeURIComponent(rfqId)}">견적 비교로 이동</a></p>
    `;
    $$('.select-candidate', body).forEach(btn => btn.addEventListener('click', () => {
      const payload = JSON.parse(btn.dataset.candidate || '{}');
      store(window.imma.scopedKey(rfqId, 'selected_candidate'), payload);
      $$('.candidate-selected', body).forEach(x => x.classList.remove('candidate-selected'));
      btn.closest('.imma-candidate-card').classList.add('candidate-selected');
      window.imma.toast('후보가 표시되었습니다. 실제 발주는 견적 비교 후 진행합니다.', 'success');
    }));
  }

  async function initOrderManagement() {
    const user = await window.imma.requireRole(['buyer', 'supplier']);
    window.imma.renderSessionHeader();
    const rfqId = window.imma.getQueryParam('rfq_id') || (user.role === 'buyer' ? read(window.imma.scopedKey('current_rfq_id')) : null);
    const orderId = window.imma.getQueryParam('order_id') || read(window.imma.scopedKey('current_order_id'));
    const body = window.imma.setPanelContent('견적 및 발주 관리', 'RFQ 견적 비교 또는 order 상태 전이를 실제 API로 수행합니다.', '<p class="imma-loading">불러오는 중...</p>');

    async function renderOrder(id) {
      const order = await window.imma.apiJson(`/api/orders/${encodeURIComponent(id)}`);
      const allowed = user.role === 'buyer'
        ? { contracting: ['ordered','cancelled'], ordered: ['cancelled'], in_production: ['cancelled','disputed'], inspection: ['in_production','disputed'], shipped: ['delivered','disputed'], delivered: ['completed','disputed'] }
        : { contracting: ['ordered'], ordered: ['in_production'], in_production: ['inspection','disputed'], inspection: ['shipped','disputed'], delivered: ['disputed'] };
      const nexts = allowed[order.status] || [];
      body.innerHTML = `
        <div class="imma-card">
          <h3>Order ${h(order.order_id)}</h3>
          <p>업체: ${h(order.company_name)} · 상태: ${statusBadge(order.status)} · 금액: ${h(window.imma.formatCurrency(order.total_price, order.currency_code || 'KRW'))}</p>
          <div class="imma-chip-wrap">${nexts.map(s => `<button class="imma-btn secondary order-status" data-status="${h(s)}">${h(s)}로 전이</button>`).join('') || '<span class="imma-muted">가능한 전이가 없습니다.</span>'}</div>
        </div>`;
      $$('.order-status', body).forEach(btn => btn.addEventListener('click', async () => {
        try {
          await window.imma.apiJson(`/api/orders/${encodeURIComponent(id)}/status`, { method: 'PUT', body: { status: btn.dataset.status } });
          window.imma.toast('상태가 변경되었습니다.', 'success');
          await renderOrder(id);
        } catch (err) { window.imma.toast(err.message, 'error'); }
      }));
    }

    if (orderId) { await renderOrder(orderId); return; }
    if (!rfqId) { body.innerHTML = '<p class="imma-danger">rfq_id 또는 order_id가 없습니다.</p>'; return; }
    if (user.role !== 'buyer') { body.innerHTML = '<p class="imma-danger">견적 비교는 buyer만 사용할 수 있습니다.</p>'; return; }
    try {
      const data = await window.imma.apiJson(`/api/rfq/${encodeURIComponent(rfqId)}/quotes`);
      const quotes = data.quotes || [];
      body.innerHTML = `
        <p>RFQ ID: <code>${h(rfqId)}</code></p>
        <div class="imma-grid">${quotes.map(q => `
          <article class="imma-card">
            <h3>${h(q.company_name || q.company_id)}</h3>
            <p>${h(window.imma.formatCurrency(q.total_price, q.currency || 'KRW'))}</p>
            <p>리드타임 ${h(q.estimated_lead_days ?? '-')}일 · 납기 ${h(q.proposed_delivery_date || '-')}</p>
            <p>${statusBadge(q.status)}</p>
            <button class="imma-btn create-order" data-quote-id="${h(q.quote_id)}">이 견적으로 발주</button>
          </article>`).join('') || '<p class="imma-empty">아직 제출된 견적이 없습니다.</p>'}</div>`;
      $$('.create-order', body).forEach(btn => btn.addEventListener('click', async () => {
        try {
          const order = await window.imma.apiJson('/api/orders', { method: 'POST', body: { quote_id: btn.dataset.quoteId } });
          store(window.imma.scopedKey('current_order_id'), order.order_id);
          window.location.href = `/order-management?order_id=${encodeURIComponent(order.order_id)}`;
        } catch (err) { window.imma.toast(err.message, 'error'); }
      }));
    } catch (err) { body.innerHTML = `<p class="imma-danger">${h(err.message)}</p>`; }
  }

  async function initSupplierDashboard() {
    await window.imma.requireRole('supplier');
    window.imma.renderSessionHeader();
    const body = window.imma.setPanelContent('Supplier 대시보드', '수신 매칭과 발주 알림을 실제 API로 요약합니다.', '<p class="imma-loading">불러오는 중...</p>');
    try {
      const [matches, notifications] = await Promise.all([
        window.imma.apiJson('/api/company/matches'),
        window.imma.apiJson('/api/notifications?unread_only=false').catch(() => []),
      ]);
      const orderEvents = (notifications || []).filter(n => n.event_type === 'order_confirmed' && n.reference_type === 'order');
      body.innerHTML = `
        <div class="imma-kpis"><div class="imma-kpi"><strong>${matches.count || 0}</strong><span>매칭 요청</span></div><div class="imma-kpi"><strong>${orderEvents.length}</strong><span>발주 알림</span></div></div>
        <p><a class="imma-btn" href="/supplier-workbench">작업대로 이동</a></p>`;
    } catch (err) { body.innerHTML = `<p class="imma-danger">${h(err.message)}</p>`; }
  }

  async function loadSupplierOrdersFromNotifications() {
    const notifications = await window.imma.apiJson('/api/notifications?unread_only=false');
    const events = (notifications || []).filter(n => n.event_type === 'order_confirmed' && n.reference_type === 'order' && n.reference_id);
    const orders = [];
    for (const event of events.slice(0, 5)) {
      try {
        const order = await window.imma.apiJson(`/api/orders/${encodeURIComponent(event.reference_id)}`);
        orders.push({ notification: event, order });
      } catch (err) { console.warn('order 조회 실패', event.reference_id, err); }
    }
    return orders;
  }

  async function initSupplierWorkbench() {
    const user = await window.imma.requireRole('supplier');
    window.imma.renderSessionHeader();
    const body = window.imma.setPanelContent('Supplier 작업대', '매칭 수락, 견적 제출, notifications 기반 발주 발견을 처리합니다.', '<p class="imma-loading">불러오는 중...</p>');

    async function refresh() {
      const matches = await window.imma.apiJson('/api/company/matches');
      const orders = await loadSupplierOrdersFromNotifications().catch(() => []);
      body.innerHTML = `
        <h3>수신 매칭</h3>
        <div class="imma-grid">${(matches.matches || []).map(m => `
          <article class="imma-card">
            <h4>${h(m.part_name || m.rfq_id)}</h4>
            <p>소재 ${h(m.material || '-')} · 공정 ${h(m.processes || '-')} · 점수 ${h(m.total_score ?? '-')} · ${statusBadge(m.supplier_response)}</p>
            <button class="imma-btn accept-match" data-match-run-id="${h(m.match_run_id)}">수락</button>
            <button class="imma-btn secondary decline-match" data-match-run-id="${h(m.match_run_id)}">거절</button>
            <details><summary>견적 제출</summary>
              <form class="imma-form quote-form" data-rfq-id="${h(m.rfq_id)}" data-part-id="${h(m.rfq_part_id || '')}">
                <label>총액 <input name="total_price" type="number" value="6250000"></label>
                <label>리드타임 <input name="estimated_lead_days" type="number" value="7"></label>
                <label>제안 납기 <input name="proposed_delivery_date" type="date" value="${todayPlus(20)}"></label>
                <label>유효일 <input name="validity_until" type="date" value="${todayPlus(14)}"></label>
                <label>가정 <textarea name="assumptions">S45C 소재 기준, 표면처리 제외</textarea></label>
                <button class="imma-btn" type="submit">견적 제출</button>
              </form>
            </details>
          </article>`).join('') || '<p class="imma-empty">수신 매칭이 없습니다.</p>'}</div>
        <h3>발주 알림</h3>
        <div class="imma-grid">${orders.map(({order}) => `
          <article class="imma-card"><h4>Order ${h(order.order_id)}</h4><p>${h(order.company_name)} · ${statusBadge(order.status)} · ${h(window.imma.formatCurrency(order.total_price, order.currency_code || 'KRW'))}</p><a class="imma-btn" href="/order-management?order_id=${encodeURIComponent(order.order_id)}">상태 관리</a></article>`).join('') || '<p class="imma-empty">아직 확정된 발주가 없습니다.</p>'}</div>`;

      $$('.accept-match', body).forEach(btn => btn.addEventListener('click', () => respond(btn.dataset.matchRunId, 'accepted')));
      $$('.decline-match', body).forEach(btn => btn.addEventListener('click', () => respond(btn.dataset.matchRunId, 'declined')));
      $$('.quote-form', body).forEach(form => form.addEventListener('submit', submitQuote));
    }

    async function respond(matchRunId, response) {
      try {
        await window.imma.apiJson(`/api/match-candidates/${encodeURIComponent(matchRunId)}/${encodeURIComponent(user.id)}/respond`, { method: 'PUT', body: { response } });
        window.imma.toast('응답이 저장되었습니다.', 'success');
        await refresh();
      } catch (err) { window.imma.toast(err.message, 'error'); }
    }

    async function submitQuote(e) {
      e.preventDefault();
      const fd = new FormData(e.currentTarget);
      const total = Number(fd.get('total_price') || 0);
      const rfqId = e.currentTarget.dataset.rfqId;
      try {
        await window.imma.apiJson('/api/quote', { method: 'POST', body: {
          rfq_id: rfqId,
          company_id: user.id,
          total_price: total,
          estimated_lead_days: Number(fd.get('estimated_lead_days') || 0),
          proposed_delivery_date: fd.get('proposed_delivery_date'),
          validity_until: fd.get('validity_until'),
          assumptions: fd.get('assumptions'),
          line_items: [{ description: 'Phase 1 견적', quantity: 1, unit_price: total, line_total: total, notes: 'UI quick quote' }],
        }});
        window.imma.toast('견적이 제출되었습니다.', 'success');
        await refresh();
      } catch (err) { window.imma.toast(err.message, 'error'); }
    }

    await refresh();
  }

  async function initSupplierSettings() {
    const user = await window.imma.requireRole('supplier');
    window.imma.renderSessionHeader();
    const body = window.imma.setPanelContent('Supplier 설정', 'Phase 1 최소 profile과 capability를 등록합니다.', `
      <form id="imma-supplier-settings" class="imma-form">
        <label>회사명 <input name="company_name" value="${h(user.company_name || '')}"></label>
        <label>대표 이메일 <input name="main_email" type="email"></label>
        <label>대표 전화 <input name="main_phone"></label>
        <label>지역 <input name="region" placeholder="경기"></label>
        <label>재질 코드 <input name="material_code" value="S45C"></label>
        <label>공정 코드 <input name="process_code" value="cnc_milling"></label>
        <label>장비명 <input name="equipment_name" value="CNC Mill"></label>
        <button class="imma-btn" type="submit">저장</button>
      </form>`);
    $('#imma-supplier-settings', body).addEventListener('submit', async e => {
      e.preventDefault();
      const fd = new FormData(e.currentTarget);
      try {
        await window.imma.apiJson('/api/company/profile', { method: 'PUT', body: {
          company_id: user.id,
          company_name: fd.get('company_name') || user.company_name,
          main_email: fd.get('main_email') || null,
          main_phone: fd.get('main_phone') || null,
          region: fd.get('region') || null,
        }});
        await window.imma.apiJson('/api/material-capability', {
          method: 'POST',
          body: { company_id: user.id, materials: [fd.get('material_code')].filter(Boolean), categories: [] }
        }).catch(console.warn);
        await window.imma.apiJson('/api/process-capability', {
          method: 'POST',
          body: { company_id: user.id, processes: [{ process_code: fd.get('process_code'), service_mode: 'in_house', best_it_grade: 7, best_ra_um: 1.6, typical_lead_days: 7 }] }
        }).catch(console.warn);
        await window.imma.apiJson('/api/equipment', {
          method: 'POST',
          body: { company_id: user.id, display_name: fd.get('equipment_name'), equipment_category_code: 'machining_center_3axis' }
        }).catch(console.warn);
        window.imma.toast('저장되었습니다.', 'success');
      } catch (err) { window.imma.toast(err.message, 'error'); }
    });
  }

  async function initSupplierRfqDetail() {
    await window.imma.requireRole('supplier');
    window.imma.renderSessionHeader();
    const rfqId = window.imma.getQueryParam('rfq_id');
    const body = window.imma.setPanelContent('Supplier RFQ 상세', '매칭된 RFQ 상세를 조회합니다.', '<p class="imma-loading">불러오는 중...</p>');
    if (!rfqId) { body.innerHTML = '<p class="imma-danger">rfq_id가 없습니다. workbench에서 RFQ를 선택해 주세요.</p>'; return; }
    try {
      const rfq = await window.imma.apiJson(`/api/rfq/${encodeURIComponent(rfqId)}`);
      body.innerHTML = `<pre class="imma-pre">${h(JSON.stringify(rfq, null, 2))}</pre><p><a class="imma-btn" href="/supplier-workbench">작업대로 이동</a></p>`;
    } catch (err) { body.innerHTML = `<p class="imma-danger">${h(err.message)}</p>`; }
  }

  async function initAdminDashboard() {
    await window.imma.requireAdmin();
    window.imma.renderSessionHeader();
    const body = window.imma.setPanelContent('Admin 대시보드', 'Phase 1에서는 업체 승인만 실데이터로 연결합니다.', '<p class="imma-loading">불러오는 중...</p>');
    try {
      const rows = await window.imma.apiJson('/api/admin/companies/pending');
      body.innerHTML = `<div class="imma-kpis"><div class="imma-kpi"><strong>${rows.length}</strong><span>검수 대기/승인 대상</span></div></div><p><a class="imma-btn" href="/admin-control-center">업체 검수로 이동</a></p>`;
    } catch (err) { body.innerHTML = `<p class="imma-danger">${h(err.message)}</p>`; }
  }

  async function initAdminControlCenter() {
    await window.imma.requireAdmin();
    window.imma.renderSessionHeader();
    const body = window.imma.setPanelContent('Admin 업체 검수', 'pending 업체를 verify/reject합니다.', '<p class="imma-loading">불러오는 중...</p>');
    async function refresh() {
      const rows = await window.imma.apiJson('/api/admin/companies/pending');
      body.innerHTML = `<div class="imma-grid">${rows.map(c => `
        <article class="imma-card"><h3>${h(c.company_name)}</h3><p>${h(c.main_email || '-')} · ${h(c.region || '-')} · ${statusBadge(c.onboarding_status)}</p><button class="imma-btn verify-company" data-id="${h(c.company_id)}">승인</button><button class="imma-btn secondary reject-company" data-id="${h(c.company_id)}">반려</button></article>`).join('') || '<p class="imma-empty">대상 업체가 없습니다.</p>'}</div>`;
      $$('.verify-company', body).forEach(btn => btn.addEventListener('click', async () => {
        try { await window.imma.apiJson(`/api/admin/companies/${encodeURIComponent(btn.dataset.id)}/verify`, { method: 'PUT' }); window.imma.toast('승인되었습니다.', 'success'); await refresh(); } catch (err) { window.imma.toast(err.message, 'error'); }
      }));
      $$('.reject-company', body).forEach(btn => btn.addEventListener('click', async () => {
        const reason = prompt('반려 사유를 입력하세요', '자료 보완 필요') || '';
        try { await window.imma.apiJson(`/api/admin/companies/${encodeURIComponent(btn.dataset.id)}/reject`, { method: 'PUT', body: { reason } }); window.imma.toast('반려되었습니다.', 'success'); await refresh(); } catch (err) { window.imma.toast(err.message, 'error'); }
      }));
    }
    await refresh();
  }

  async function initProtected(role) {
    await window.imma.requireRole(role);
    window.imma.renderSessionHeader();
  }

  async function route() {
    const path = window.location.pathname;
    try {
      if (path === '/') return initLanding();
      if (path === '/client-register') return initClientRegister();
      if (path === '/supplier-register') return initSupplierRegister();
      if (path === '/client') return initClientDashboard();
      if (path === '/quote-request') return initQuoteRequest();
      if (path === '/matching-ui') return initMatching();
      if (path === '/order-management') return initOrderManagement();
      if (path === '/supplier') return initSupplierDashboard();
      if (path === '/supplier-workbench') return initSupplierWorkbench();
      if (path === '/supplier-settings') return initSupplierSettings();
      if (path === '/supplier-rfq-detail') return initSupplierRfqDetail();
      if (path === '/supplier-messages') return initProtected('supplier');
      if (path === '/admin-ui') return initAdminDashboard();
      if (path === '/admin-control-center') return initAdminControlCenter();
      if (path === '/admin-operations') return initProtected('admin');
      if (path === '/client-fulfillment' || path === '/payment-success') return initProtected('buyer');
      window.imma.renderSessionHeader();
    } catch (err) {
      console.error(err);
      if (window.imma.toast) window.imma.toast(err.message || '초기화 실패', 'error');
    }
  }

  document.addEventListener('DOMContentLoaded', route);
})();
