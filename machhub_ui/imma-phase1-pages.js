(function () {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const h = value => window.imma.escapeHtml(value);

  function text(el, value) {
    if (el) el.textContent = value == null || value === '' ? '-' : String(value);
  }

  function html(el, value) {
    if (el) el.innerHTML = value == null ? '' : String(value);
  }

  function shortId(value) {
    return value ? String(value).slice(0, 8).toUpperCase() : '-';
  }

  function numberOnly(value) {
    const n = Number(String(value || '').replace(/[^0-9.-]/g, ''));
    return Number.isFinite(n) ? n : 0;
  }

  function safeJson(value) {
    try { return JSON.stringify(value).replace(/</g, '\\u003c'); }
    catch (_) { return '{}'; }
  }

  function firstPart(rfq) {
    return rfq && Array.isArray(rfq.parts) && rfq.parts.length ? rfq.parts[0] : {};
  }

  function processText(part) {
    if (!part) return '-';
    if (Array.isArray(part.processes)) return part.processes.join(', ') || '-';
    return part.processes || part.process || '-';
  }

  function partMaterial(part) {
    return part && (part.material_raw_text || part.material || part.material_category_code) || '-';
  }

  function partTolerance(part) {
    if (!part) return '-';
    if (part.tightest_tolerance_mm !== null && part.tightest_tolerance_mm !== undefined) return `±${part.tightest_tolerance_mm} mm`;
    if (part.tightest_it_grade) return `IT${part.tightest_it_grade}`;
    return '-';
  }

  function setCardValue(card, value, suffix = '건') {
    const el = card && card.querySelector('.stat-value, .kpi-num, .mw-kpi-value, .kpi-value');
    if (!el) return;
    if (suffix) {
      el.innerHTML = `${h(value)}<span style="font-size:14px;font-weight:600;color:var(--text-muted);">${h(suffix)}</span>`;
    } else {
      el.textContent = value;
    }
  }

  function scopedSet(keyParts, value) {
    localStorage.setItem(window.imma.scopedKey(...keyParts), typeof value === 'string' ? value : JSON.stringify(value));
  }

  function scopedGet(keyParts, fallback = null) {
    try {
      const raw = localStorage.getItem(window.imma.scopedKey(...keyParts));
      if (raw == null) return fallback;
      try { return JSON.parse(raw); } catch (_) { return raw; }
    } catch (_) {
      return fallback;
    }
  }

  function findFixtureDrawingId() {
    const fromQuery = window.imma.getQueryParam('fixture_drawing_id');
    if (fromQuery) return fromQuery;
    const user = window.imma.getUser && window.imma.getUser();
    if (user && user.id) {
      const scoped = localStorage.getItem(`imma:${user.id}:fixture_drawing_id`);
      if (scoped) return scoped;
    }
    return localStorage.getItem('imma_fixture_drawing_id') || '';
  }

  function latestDrawingId() {
    return scopedGet(['current_drawing_id']) || localStorage.getItem('imma_drawing_id') || findFixtureDrawingId();
  }

  function storeDrawing(data, fallbackUsed) {
    if (!data || !data.drawing_id) return;
    localStorage.setItem('imma_drawing_id', data.drawing_id);
    if (data.file_uri) localStorage.setItem('imma_drawing_file_uri', data.file_uri);
    if (data.original_filename) localStorage.setItem('imma_drawing_original_filename', data.original_filename);
    if (data.file_sha256) localStorage.setItem('imma_drawing_sha256', data.file_sha256);
    scopedSet(['current_drawing_id'], data.drawing_id);
    if (fallbackUsed) scopedSet([data.drawing_id, 'vlm_fallback_used'], true);
  }

  function candidateScore(cand) {
    const score = Number(cand && cand.total_score);
    return Number.isFinite(score) ? `${Math.round(score * 100)}%` : '-';
  }

  function getMatchCandidates(result) {
    const output = [];
    const parts = Array.isArray(result && result.parts) ? result.parts : [];
    parts.forEach(part => {
      const rec = Array.isArray(part.recommended_candidates) ? part.recommended_candidates : [];
      const cond = Array.isArray(part.conditional_candidates) ? part.conditional_candidates : [];
      rec.concat(cond).forEach(cand => output.push({ part, cand }));
    });
    return output;
  }

  async function enrichMatches(matches, limit = 5) {
    const list = (matches || []).slice(0, limit);
    const enriched = await Promise.all(list.map(async match => {
      if (!match.rfq_id) return match;
      try {
        const rfq = await window.imma.apiJson(`/api/rfq/${encodeURIComponent(match.rfq_id)}`);
        const part = firstPart(rfq);
        return { ...match, rfq, rfq_part: part };
      } catch (_) {
        return match;
      }
    }));
    return enriched;
  }

  function quotePayloadFromWorkbench(match, user) {
    // supplier #reply 영역의 5 항목 수집 (납기 / 금액 / 인증 / 후처리 / 메모)
    // assumptions 영역에 5 항목 구조화 통합 (buyer order-management 영역에서 parse 표시)
    const dueInput = $('#reply-due-date');
    const dueDate = dueInput && dueInput.value;
    const amountInput = $('#reply-amount');
    const amount = numberOnly(amountInput && amountInput.value);
    if (!amount) return null;
    const certInput = $('#reply-certification');
    const certification = certInput && certInput.value ? certInput.value : '';
    const postTreatmentInput = $('#reply-post-treatment');
    const postTreatment = postTreatmentInput && postTreatmentInput.value ? postTreatmentInput.value : '';
    const memoInput = $('#reply-memo');
    const memo = memoInput && memoInput.value ? memoInput.value : '';

    // 납기 일수 계산 (today → dueDate)
    let leadDays = 7;
    if (dueDate) {
      const today = new Date();
      const due = new Date(dueDate);
      const diff = Math.round((due - today) / (1000 * 60 * 60 * 24));
      if (diff > 0) leadDays = diff;
    }

    // 5 항목 구조화 — buyer 영역에서 parse 후 표시
    const structuredAssumptions = JSON.stringify({
      certification,
      post_treatment: postTreatment,
      memo,
    });

    const rfqPartId = match.rfq_part && match.rfq_part.rfq_part_id;
    const quantity = match.rfq_part && match.rfq_part.quantity ? Number(match.rfq_part.quantity) : 1;
    return {
      rfq_id: match.rfq_id,
      company_id: user.id,
      total_price: amount,
      estimated_lead_days: leadDays,
      proposed_delivery_date: dueDate || null,
      assumptions: structuredAssumptions,
      line_items: [{
        rfq_part_id: rfqPartId || null,
        process_code: match.processes || null,
        description: match.part_name || 'Phase 1 견적',
        quantity,
        unit_price: quantity > 0 ? Math.round(amount / quantity) : amount,
        line_total: amount,
        notes: memo || null,
      }],
    };
  }

  // assumptions JSON 영역 안전 parse — 구버전 (자유 텍스트) 호환
  function parseQuoteAssumptions(assumptions) {
    if (!assumptions) return { certification: '', post_treatment: '', memo: '' };
    try {
      const parsed = JSON.parse(assumptions);
      if (parsed && typeof parsed === 'object') {
        return {
          certification: parsed.certification || '',
          post_treatment: parsed.post_treatment || '',
          memo: parsed.memo || '',
        };
      }
    } catch (e) { /* 구버전 자유 텍스트 */ }
    return { certification: '', post_treatment: '', memo: assumptions };
  }

  // 예산 select 라벨 → KRW 금액 (대표값)
  const UI_BUDGET_TO_AMOUNT = {
    '50만원 미만': 500000,
    '50~100만원': 1000000,
    '100~500만원': 5000000,
    '500만원 이상': 10000000,
  };

  function showFallbackChoice(container, file, retryFn) {
    const fixtureId = findFixtureDrawingId();
    const box = document.createElement('div');
    box.style.marginTop = '10px';
    box.style.display = 'flex';
    box.style.gap = '8px';
    box.style.flexWrap = 'wrap';
    box.innerHTML = `
      <button type="button" class="btn-primary" style="font-size:12px;padding:7px 10px;">사전 분석 결과로 계속</button>
      <button type="button" class="btn-outline" style="font-size:12px;padding:7px 10px;">다시 시도</button>
    `;
    if (container) container.appendChild(box);
    box.querySelector('.btn-primary').addEventListener('click', () => {
      if (!fixtureId) {
        window.imma.toast('fixture_drawing_id가 없습니다. URL 또는 localStorage에 지정해 주세요.', 'warning');
        return;
      }
      const data = { drawing_id: fixtureId, original_filename: 'sample_00015 fixture' };
      storeDrawing(data, true);
      if (container) container.textContent = `사전 분석 결과 사용 중 · drawing_id ${fixtureId}`;
      window.imma.toast('사전 분석 결과로 전환했습니다.', 'success');
    });
    box.querySelector('.btn-outline').addEventListener('click', () => retryFn && retryFn(file));
  }

  async function initLanding() {
    window.imma.renderSessionHeader();
    const originalShowLoginForm = window.showLoginForm;
    if (typeof originalShowLoginForm === 'function' && !originalShowLoginForm.__immaWrapped) {
      window.showLoginForm = function (type) {
        window.__immaLoginType = type;
        return originalShowLoginForm.apply(this, arguments);
      };
      window.showLoginForm.__immaWrapped = true;
    }

    const form = $('#login-form-container form');
    if (!form || form.dataset.immaHooked === 'true') return;
    form.dataset.immaHooked = 'true';
    form.removeAttribute('onsubmit');
    form.onsubmit = null;
    window.submitLogin = function () { form.requestSubmit(); };

    form.addEventListener('submit', async e => {
      e.preventDefault();
      const loginId = $('#login-id') ? $('#login-id').value.trim() : '';
      const password = $('#login-pw') ? $('#login-pw').value : '';
      const title = $('#login-title') ? $('#login-title').textContent : '';
      let role;
      if (window.__immaLoginType === 'admin' || title.includes('관리자')) {
        role = 'admin';
      } else if (window.__immaLoginType === 'corporate' || title.includes('기업')) {
        role = 'supplier';
      } else {
        role = 'buyer';
      }
      const btn = form.querySelector('button[type="submit"]');
      window.imma.setLoading(btn, true, '로그인 중...');
      try {
        const user = await window.imma.login(loginId, password, role);
        window.imma.toast('로그인되었습니다.', 'success');
        window.location.href = window.imma.redirectForRole(user.role);
      } catch (err) {
        window.imma.toast(err.message, 'error');
      } finally {
        window.imma.setLoading(btn, false);
      }
    });
  }

  // 중복 확인 버튼 hook — client-register / supplier-register 공용.
  // R6 endpoint /api/check-login-id 활용. 빈 값/4 자 미만은 클라이언트단 사전 차단.
  function bindLoginIdCheck(buttonSelector, inputSelector) {
    const btn = $(buttonSelector);
    const input = $(inputSelector);
    if (!btn || !input || btn.dataset.immaHooked === 'true') return;
    btn.dataset.immaHooked = 'true';
    btn.addEventListener('click', async () => {
      const loginId = (input.value || '').trim();
      if (!loginId) {
        window.imma.toast('아이디를 입력해주세요.', 'warning');
        return;
      }
      if (loginId.length < 4) {
        window.imma.toast('ID 는 4 자 이상이어야 합니다.', 'warning');
        return;
      }
      window.imma.setLoading(btn, true, '확인 중...');
      try {
        const result = await window.imma.apiJson(`/api/check-login-id?login_id=${encodeURIComponent(loginId)}`);
        if (result && result.available) {
          window.imma.toast('사용 가능한 아이디입니다', 'success');
        } else {
          window.imma.toast((result && result.reason) || '이미 사용 중인 ID 입니다', 'warning');
        }
      } catch (err) {
        window.imma.toast(err.message, 'error');
      } finally {
        window.imma.setLoading(btn, false);
      }
    });
  }

  async function initClientRegister() {
    window.imma.renderSessionHeader();
    bindLoginIdCheck('#client-check-login-id', '#client-login-id');
    const form = $('.register-card form');
    if (!form || form.dataset.immaHooked === 'true') return;
    form.dataset.immaHooked = 'true';
    form.removeAttribute('onsubmit');
    form.onsubmit = null;
    form.addEventListener('submit', async e => {
      e.preventDefault();
      const inputs = $$('input', form);
      const password = inputs[4] && inputs[4].value;
      const confirm = inputs[5] && inputs[5].value;
      if (password !== confirm) {
        window.imma.toast('비밀번호 확인값이 다릅니다.', 'warning');
        return;
      }
      const payload = {
        role: 'buyer',
        name: inputs[0] && inputs[0].value.trim(),
        phone: inputs[1] && inputs[1].value.trim(),
        email: inputs[2] && inputs[2].value.trim(),
        login_id: inputs[3] && inputs[3].value.trim(),
        password,
        company_name: inputs[6] && inputs[6].value.trim(),
      };
      const btn = e.submitter || form.querySelector('button[type="submit"]');
      window.imma.setLoading(btn, true, '가입 중...');
      try {
        await window.imma.apiJson('/signup', { method: 'POST', body: payload });
        await window.imma.login(payload.login_id, payload.password, 'buyer');
        window.imma.toast('가입되었습니다.', 'success');
        window.location.href = '/client';
      } catch (err) {
        window.imma.toast(err.message, 'error');
      } finally {
        window.imma.setLoading(btn, false);
      }
    });
  }

  async function initSupplierRegister() {
    window.imma.renderSessionHeader();
    bindLoginIdCheck('#supplier-check-login-id', '#supplier-login-id');
    const form = $('.register-wrap form');
    if (!form || form.dataset.immaHooked === 'true') return;
    form.dataset.immaHooked = 'true';
    form.removeAttribute('onsubmit');
    form.onsubmit = null;
    form.addEventListener('submit', async e => {
      e.preventDefault();
      const inputs = $$('input[type="text"], input[type="tel"], input[type="email"], input[type="password"]', form);
      const password = inputs[4] && inputs[4].value;
      const confirm = inputs[5] && inputs[5].value;
      if (password !== confirm) {
        window.imma.toast('비밀번호 확인값이 다릅니다.', 'warning');
        return;
      }
      const payload = {
        role: 'supplier',
        name: inputs[0] && inputs[0].value.trim(),
        phone: inputs[1] && inputs[1].value.trim(),
        email: inputs[2] && inputs[2].value.trim(),
        login_id: inputs[3] && inputs[3].value.trim(),
        password,
        company_name: inputs[6] && inputs[6].value.trim(),
      };
      const btn = e.submitter || form.querySelector('button[type="submit"]');
      window.imma.setLoading(btn, true, '가입 중...');
      try {
        await window.imma.apiJson('/signup', { method: 'POST', body: payload });
        const user = await window.imma.login(payload.login_id, payload.password, 'supplier');
        try {
          await window.imma.apiJson('/api/company/profile', {
            method: 'PUT',
            body: {
              company_id: user.id,
              company_name: payload.company_name,
              main_email: payload.email,
              main_phone: payload.phone,
            },
          });
        } catch (profileErr) {
          console.warn('profile 보강 실패', profileErr);
        }
        window.imma.toast('가입되었습니다. 온보딩을 이어서 진행해주세요.', 'success');
        window.location.href = '/supplier-settings#onboarding';
      } catch (err) {
        window.imma.toast(err.message, 'error');
      } finally {
        window.imma.setLoading(btn, false);
      }
    });
  }

  // 알림 event_type → 표시 라벨 + 색상.
  // buyer 가 받는 핵심 이벤트: 견적 도착, supplier 매칭 수락/거절.
  const BUYER_NOTIFICATION_TYPES = {
    quote_received:     { label: '견적 도착',     color: '#dcfce7', textColor: '#166534' },
    supplier_accepted:  { label: '매칭 수락',     color: '#dcfce7', textColor: '#166534' },
    supplier_declined:  { label: '매칭 거절',     color: '#fef3f2', textColor: '#b42318' },
  };

  function notificationLink(n) {
    // reference_type 기반 link 결정. rfq → order-management?rfq_id=, 그 외 폴백.
    if (n.reference_type === 'rfq' && n.reference_id) {
      return `/order-management?rfq_id=${encodeURIComponent(n.reference_id)}`;
    }
    if (n.reference_type === 'order' && n.reference_id) {
      return `/order-management?order_id=${encodeURIComponent(n.reference_id)}`;
    }
    return '';
  }

  function renderClientNotifications(notifications) {
    const list = $('#notification-list');
    if (!list) return;
    const filtered = (notifications || []).filter(n => BUYER_NOTIFICATION_TYPES[n.event_type]);
    if (!filtered.length) {
      list.innerHTML = '<div style="font-size:13px; color:var(--text-muted); padding:8px 0;">아직 도착한 알림이 없습니다.</div>';
      return;
    }
    list.innerHTML = filtered.slice(0, 6).map(n => {
      const meta = BUYER_NOTIFICATION_TYPES[n.event_type];
      const link = notificationLink(n);
      const time = (n.created_at || '').slice(0, 16).replace('T', ' ');
      const titleHtml = link
        ? `<a href="${h(link)}" style="color:#111; text-decoration:none;">${h(n.title || meta.label)}</a>`
        : h(n.title || meta.label);
      return `<div class="order-row" style="grid-template-columns: 100px 1fr 160px; padding:14px 16px;">
        <span class="badge" style="background:${meta.color}; color:${meta.textColor}; padding:4px 8px; font-size:12px; border-radius:6px; font-weight:700; text-align:center;">${h(meta.label)}</span>
        <div>
          <div class="o-name">${titleHtml}</div>
          <div class="o-supplier">${h(n.message || '')}</div>
        </div>
        <div class="o-date" style="text-align:right;">${h(time || '-')}</div>
      </div>`;
    }).join('');
  }

  async function initClientDashboard() {
    await window.imma.requireRole('buyer');
    window.imma.renderSessionHeader();
    try {
      const data = await window.imma.apiJson('/rfqs');
      const rfqs = data.rfqs || [];
      const counts = rfqs.reduce((acc, r) => {
        const status = r.status || 'unknown';
        acc[status] = (acc[status] || 0) + 1;
        return acc;
      }, {});
      const cards = $$('.stat-card');
      const inProgress = (counts.ordered || 0) + (counts.in_production || 0) + (counts.inspection || 0) + (counts.shipped || 0);
      const awaiting = (counts.open || 0) + (counts.quoted || 0);
      const delivered = (counts.delivered || 0) + (counts.completed || 0);
      setCardValue(cards[0], inProgress);
      setCardValue(cards[1], awaiting);
      setCardValue(cards[2], delivered);
      // 누적 결제 금액 — 합산 endpoint 부재이므로 0 자세 강제 (Phase 2 영역에서 구축)
      if (cards[3]) {
        const valEl = cards[3].querySelector('.stat-value');
        if (valEl) valEl.innerHTML = '0<span style="font-size:14px;font-weight:600;color:var(--text-muted);">원</span>';
      }

      // 최근 진행 현황 hydrate — ordered 이상 status 영역 + 최근 정렬 상위 5 건
      const recentList = $('#recent-orders');
      if (recentList) {
        const recentStatuses = new Set(['ordered', 'in_production', 'inspection', 'shipped', 'delivered', 'completed']);
        const recent = rfqs.filter(r => recentStatuses.has(r.status)).slice(0, 5);
        if (recent.length === 0) {
          recentList.innerHTML = '<div style="font-size:13px; color:var(--text-muted); padding:24px; text-align:center;">아직 진행 중인 발주가 없습니다. 새 견적을 요청해보세요.</div>';
        } else {
          const STATUS_LABEL = {
            ordered: { label: '발주 확정', bg: '#fef3c7', fg: '#92400e' },
            in_production: { label: '생산 중', bg: '#dcfce7', fg: '#166534' },
            inspection: { label: '검수 중', bg: '#dbeafe', fg: '#1e40af' },
            shipped: { label: '배송 중', bg: '#e0e7ff', fg: '#3730a3' },
            delivered: { label: '납품 완료', bg: '#e2e8f0', fg: '#475569' },
            completed: { label: '완료', bg: '#e2e8f0', fg: '#475569' },
          };
          recentList.innerHTML = recent.map(r => {
            const s = STATUS_LABEL[r.status] || { label: r.status, bg: '#e2e8f0', fg: '#475569' };
            return `<div class="order-row">
              <div class="o-id">${h(r.rfq_no || ('RFQ-' + (r.id || '').slice(0,8)))}</div>
              <div>
                <div class="o-name">${h(r.material || '-')} / ${h(r.process || '-')}</div>
                <div class="o-supplier">수량 ${h(r.order_quantity || r.quantity || '-')}</div>
              </div>
              <div class="o-date">${r.due_date ? '납기 ' + h(r.due_date) : '-'}</div>
              <div><span class="badge" style="background:${s.bg};color:${s.fg};padding:4px 8px;font-size:12px;border-radius:4px;font-weight:700;">${h(s.label)}</span></div>
              <a href="/order-management?rfq_id=${encodeURIComponent(r.id)}" class="btn-outline" style="text-align:center; padding:6px 0; font-size:12px; text-decoration:none;">상세 보기</a>
            </div>`;
          }).join('');
        }
      }
    } catch (err) {
      window.imma.toast(err.message, 'error');
      const recentList = $('#recent-orders');
      if (recentList) recentList.innerHTML = '<div style="font-size:13px; color:var(--text-muted); padding:24px; text-align:center;">진행 현황을 불러올 수 없습니다.</div>';
    }

    // 최근 알림 hydrate — 견적 도착·supplier 응답 이벤트만 필터.
    // 실패 시 toast 부재 (사용자 흐름 차단 회피, 빈 메시지로 폴백).
    try {
      const notifications = await window.imma.apiJson('/api/notifications?unread_only=false');
      renderClientNotifications(notifications);
    } catch (err) {
      const list = $('#notification-list');
      if (list) list.innerHTML = '<div style="font-size:13px; color:var(--text-muted); padding:8px 0;">알림을 불러올 수 없습니다.</div>';
    }
  }

  // ── AI 분석 결과 카드 hydrate + 인라인 수정 hook ──
  // VLM 완료 시 vlm_result_jsonb 의 title_block / view / notes 영역 추출 → 카드 표시.
  // 사용자가 수정 버튼 클릭 시 인라인 input 으로 전환, 저장 시 dataset.userEdited='true' 표시.
  // submit 영역에서 dataset.userEdited 영역 모아 client_notes 영역에 반영.
  function hydrateAiResultCard(vlmResult, drawingId) {
    const card = document.getElementById('ai-result-card');
    if (!card || !vlmResult) return;

    const tb = vlmResult.title_block_1 || {};
    const view = vlmResult.view_1 || {};
    const notes = vlmResult.notes_1 || {};

    const partName = tb.Part_Name || tb.Title || '';
    const material = tb.Material || '';
    const drawingNo = tb.Drawing_No || tb.Project_ID || '';
    const measuresArr = (view.measures || []);
    const measuresCount = measuresArr.length;
    const notesArr = (notes.lines || []);
    // 후처리 영역 — notes 첫 line 또는 measures 안의 GDT/Ra 영역 (단순 요약)
    const ptSummary = notesArr[0] || measuresArr.slice(0, 3).join(' · ') || '';

    setAiField('ai-part-name', partName);
    setAiField('ai-material', material);
    setAiField('ai-measures', measuresCount > 0 ? `${measuresCount} 항목 추출 (view 1)` : '추출 부재');
    setAiField('ai-post-treatment', ptSummary);
    setAiField('ai-drawing-no', drawingNo);

    bindAiEditHooks();
    card.style.display = 'block';
    setTimeout(() => card.scrollIntoView({ behavior: 'smooth', block: 'center' }), 200);
  }

  function setAiField(elementId, value) {
    const el = document.getElementById(elementId);
    if (!el) return;
    el.textContent = value || '-';
    el.dataset.original = value || '';
    delete el.dataset.userEdited;
  }

  function bindAiEditHooks() {
    $$('#ai-result-card .ai-edit-btn').forEach(btn => {
      if (btn.dataset.immaHooked === 'true') return;
      btn.dataset.immaHooked = 'true';
      btn.addEventListener('click', () => {
        const targetId = btn.dataset.target;
        const target = document.getElementById(targetId);
        if (!target) return;
        const isEditing = btn.classList.contains('editing');
        if (isEditing) {
          // 저장
          const input = target.querySelector('input');
          const newValue = input ? input.value.trim() : '';
          target.textContent = newValue || '-';
          if (newValue && newValue !== target.dataset.original) {
            target.dataset.userEdited = 'true';
          } else {
            delete target.dataset.userEdited;
          }
          btn.classList.remove('editing');
          btn.innerHTML = '<i class="ri-pencil-line"></i> 수정';
        } else {
          // 편집 시작
          const currentValue = target.textContent === '-' ? '' : target.textContent;
          target.innerHTML = `<input type="text" value="${h(currentValue)}" />`;
          const input = target.querySelector('input');
          if (input) { input.focus(); input.select(); }
          btn.classList.add('editing');
          btn.innerHTML = '<i class="ri-check-line"></i> 저장';
        }
      });
    });
  }

  // submit 영역에서 사용자 수정 영역 client_notes 영역에 반영
  function collectAiUserEdits() {
    const edits = {};
    ['ai-part-name', 'ai-material', 'ai-post-treatment', 'ai-drawing-no'].forEach(id => {
      const el = document.getElementById(id);
      if (el && el.dataset.userEdited === 'true') {
        const field = el.dataset.field;
        edits[field] = el.textContent.trim();
      }
    });
    return edits;
  }

  async function initQuoteRequest() {
    await window.imma.requireRole('buyer');
    window.imma.renderSessionHeader();

    const fileInput = $('#file-input');
    const displaySpan = $('#file-name-display');
    const sFile = $('#s-file');

    window.uploadDrawingToServer = async function (file) {
      if (!file) throw new Error('파일이 없습니다');
      const formData = new FormData();
      const isImage = file.type && file.type.startsWith('image/');
      formData.append(isImage ? 'image' : 'file', file);
      if (displaySpan) {
        displaySpan.textContent = isImage ? `AI 분석 중: ${file.name}...` : `업로드 중: ${file.name}...`;
        displaySpan.style.color = '#7A5C00';
      }
      try {
        const data = isImage
          ? await window.imma.apiForm('/vlm/analyze-upload', formData)
          : await window.imma.apiForm('/api/drawings/upload', formData);
        storeDrawing(data, false);
        if (displaySpan) {
          displaySpan.innerHTML = `도면 준비 완료: ${h(data.original_filename || file.name)}<br><small style="font-size:10px;color:#888;font-weight:400;display:block;margin-top:4px;">ID: ${h(data.drawing_id)}</small>`;
          displaySpan.style.color = '#059669';
        }
        if (sFile) sFile.textContent = '1개 (준비됨)';
        // VLM 완료 → AI 분석 결과 카드 hydrate (사용자 확인 + 인라인 수정 가능)
        if (isImage && data.vlm_result_jsonb) {
          hydrateAiResultCard(data.vlm_result_jsonb, data.drawing_id);
        }
        return data;
      } catch (err) {
        if (displaySpan) {
          displaySpan.textContent = `${isImage ? 'AI 분석' : '업로드'} 실패: ${err.message}`;
          displaySpan.style.color = '#dc2626';
          if (isImage && (err.status === 502 || err.status === 504 || err.code === 'NETWORK_ERROR')) {
            showFallbackChoice(displaySpan, file, window.uploadDrawingToServer);
          }
        }
        window.imma.toast(err.message, 'error');
        throw err;
      }
    };

    if (fileInput && !fileInput.dataset.immaHooked) {
      fileInput.dataset.immaHooked = 'true';
      fileInput.addEventListener('change', async ev => {
        const files = ev.target.files;
        if (!files || files.length === 0) return;
        try {
          await window.uploadDrawingToServer(files[0]);
        } catch (_) {
          // 오류는 uploadDrawingToServer 내부에서 처리
        }
      });
    }

    // q-material 토글 hook — '__custom__' 선택 시 직접 입력 input 노출.
    // inline script 와 중복 안전 — dataset 가드로 1회만 binding.
    const materialSel = $('#q-material');
    const materialCustom = $('#q-material-custom');
    if (materialSel && materialCustom && materialSel.dataset.immaCustomHooked !== 'true') {
      materialSel.dataset.immaCustomHooked = 'true';
      const syncCustom = () => {
        const isCustom = materialSel.value === '__custom__';
        materialCustom.style.display = isCustom ? 'block' : 'none';
      };
      syncCustom();
      materialSel.addEventListener('change', syncCustom);
    }

    const submitLink = $('.submit-area a[href="/matching-ui"], a[href="/matching-ui"]');
    if (!submitLink || submitLink.dataset.immaHooked === 'true') return;
    submitLink.dataset.immaHooked = 'true';
    submitLink.addEventListener('click', async e => {
      e.preventDefault();
      const drawingId = latestDrawingId();
      if (!drawingId) {
        window.imma.toast('도면을 먼저 업로드하거나 fixture_drawing_id를 지정해 주세요.', 'warning');
        return;
      }

      const quantityInput = $('#q-quantity');
      const materialSelect = $('#q-material');
      const materialCustom = $('#q-material-custom');
      const dateInput = $('#q-due');
      const budgetSelect = $('#q-budget');
      const regionSelect = $('#q-region');
      const surfaceSelect = $('#q-surface');
      const surfaceExtra = $('#q-surface-extra');
      const heatSelect = $('#q-heat');
      const heatExtra = $('#q-heat-extra');
      const certInput = $('#q-certifications');
      const noteInput = $('#q-notes');

      const orderQuantity = Number(quantityInput && quantityInput.value) || 1;
      const dueDate = dateInput && dateInput.value ? dateInput.value : null;
      const budgetLabel = budgetSelect && budgetSelect.value ? budgetSelect.value : '';
      const budgetAmount = UI_BUDGET_TO_AMOUNT[budgetLabel] || null;

      // 소재 입력 — 직접 입력이면 자유 텍스트, 카테고리 선택이면 한글 라벨.
      // pipeline_runner.py 의 client_material fallback 이 VLM 결과 부재 시에만 채운다.
      const materialLabel = materialSelect && materialSelect.value ? materialSelect.value : '';
      const isMaterialCustom = materialLabel === '__custom__';
      const materialCustomText = (materialCustom && materialCustom.value || '').trim();
      let materialInput = null;
      if (isMaterialCustom && materialCustomText) {
        materialInput = materialCustomText;
      } else if (materialLabel && !isMaterialCustom) {
        materialInput = materialLabel;
      }

      // 표면처리·열처리는 client_notes 부수 정보로만 전달. 매칭 영향 부재, 견적 단계 참고용.
      const postTreatmentParts = [];
      const sVal = surfaceSelect && surfaceSelect.value ? surfaceSelect.value : '';
      const sExtra = (surfaceExtra && surfaceExtra.value || '').trim();
      const hVal = heatSelect && heatSelect.value ? heatSelect.value : '';
      const hExtra = (heatExtra && heatExtra.value || '').trim();
      if (sVal && sVal !== '없음') postTreatmentParts.push(sVal);
      if (sExtra) postTreatmentParts.push(sExtra);
      if (hVal && hVal !== '없음') postTreatmentParts.push(hVal);
      if (hExtra) postTreatmentParts.push(hExtra);
      const postTreatmentRequest = postTreatmentParts.length ? postTreatmentParts.join(', ') : null;

      // AI 분석 결과 카드 사용자 수정 영역 — VLM 추출 값을 사용자가 정정한 경우 client_notes 영역에 반영.
      // pipeline_runner.py 의 client_material fallback 영역이 VLM 결과 부재 또는 사용자 우선 영역에서 사용.
      const aiEdits = collectAiUserEdits();
      const materialOverride = aiEdits.material || materialInput;
      const postTreatmentOverride = aiEdits.post_treatment
        ? (postTreatmentRequest ? `${postTreatmentRequest}, ${aiEdits.post_treatment}` : aiEdits.post_treatment)
        : postTreatmentRequest;

      // pipeline_runner.py 가 우선 lookup 하는 client_notes 키로 통일 전달.
      // 공정·공차·envelope·GDT 는 도면 분석이 단일 원천이므로 parts 명시 전달은 수행하지 않는다.
      const payload = {
        drawing_id: drawingId,
        order_quantity: orderQuantity,
        requested_delivery_date: dueDate,
        budget_amount: budgetAmount,
        budget_currency: 'KRW',
        client_notes: {
          material: materialOverride,
          delivery_region: regionSelect && regionSelect.value ? regionSelect.value : null,
          certifications: certInput && certInput.value ? certInput.value : null,
          post_treatment_request: postTreatmentOverride,
          notes: noteInput && noteInput.value ? noteInput.value : null,
          vlm_fallback_used: Boolean(scopedGet([drawingId, 'vlm_fallback_used'], false)),
          ai_user_edits: Object.keys(aiEdits).length ? aiEdits : null,
        },
      };

      window.imma.setLoading(submitLink, true, '매칭 실행 중...');
      try {
        const result = await window.imma.apiJson('/api/match-v2', { method: 'POST', body: payload });
        const rfqId = result.rfq_id || (result.rfq && result.rfq.id);
        if (!rfqId) throw new Error('매칭 응답에 rfq_id가 없습니다');
        scopedSet(['current_rfq_id'], rfqId);
        scopedSet([rfqId, 'match_result'], result);
        window.location.href = `/matching-ui?rfq_id=${encodeURIComponent(rfqId)}`;
      } catch (err) {
        window.imma.toast(err.message, 'error');
      } finally {
        window.imma.setLoading(submitLink, false);
      }
    });
  }

  // ── AI 요약 카드 hydrate (match_input 필드 활용) ──
  function renderAiSummaryCard(rfq, part, result) {
    const matchInput = (result && result.match_input) || {};
    const materialCol = document.getElementById('ai-col-material');
    const featuresCol = document.getElementById('ai-col-features');
    const extraCol = document.getElementById('ai-col-extra');

    function aiItem(text) {
      return `<div class="ai-item">${h(text)}</div>`;
    }
    function aiWarn(text) {
      return `<div class="ai-warn"><i class="ri-alert-line"></i> ${h(text)}</div>`;
    }

    const materialText = matchInput.material || partMaterial(part);
    const processes = matchInput.processes
      || (Array.isArray(part.processes) ? part.processes.join(', ') : (part.processes || part.process || ''));
    const materialItems = [];
    if (materialText && materialText !== '-') materialItems.push(`소재: ${materialText}`);
    if (processes) materialItems.push(`공정: ${processes}`);
    if (materialCol) materialCol.innerHTML = materialItems.length ? materialItems.map(aiItem).join('') : aiItem('—');

    const featureItems = [];
    const tolerance = partTolerance(part);
    if (tolerance && tolerance !== '-') featureItems.push(`공차 요구: ${tolerance}`);
    if (matchInput.surface_roughness_ra) featureItems.push(`표면거칠기: Ra ${matchInput.surface_roughness_ra}`);
    if (part.tightest_it_grade) featureItems.push(`IT 등급: IT${part.tightest_it_grade}`);
    if (matchInput.envelope_mm) featureItems.push(`외형: ${matchInput.envelope_mm}`);
    if (featuresCol) featuresCol.innerHTML = featureItems.length ? featureItems.map(aiItem).join('') : aiItem('—');

    const extras = [];
    const warnings = Array.isArray(matchInput.warnings) ? matchInput.warnings : [];
    warnings.slice(0, 3).forEach(w => extras.push(aiWarn(w)));
    const postTreatment = matchInput.post_treatment_request
      || (rfq && rfq.client_notes && rfq.client_notes.post_treatment_request);
    if (postTreatment) extras.push(aiItem(`표면·열처리: ${postTreatment}`));
    if (matchInput.vlm_fallback_used) extras.push(aiWarn('사전 분석 결과 사용 (VLM 폴백)'));
    if (extraCol) extraCol.innerHTML = extras.length ? extras.join('') : aiItem('—');
  }

  // ── AI 점수 가중 분해 tooltip ──
  function buildScoreTooltip(cand) {
    const breakdown = cand.score_breakdown || cand.breakdown || {};
    const technical = breakdown.technical ?? breakdown.tech ?? null;
    const availability = breakdown.availability ?? breakdown.avail ?? null;
    const quality = breakdown.quality ?? breakdown.qual ?? null;
    const parts = [];
    if (technical != null) parts.push(`기술 적합: ${Math.round(Number(technical) * 100)}%`);
    if (availability != null) parts.push(`가용성: ${Math.round(Number(availability) * 100)}%`);
    if (quality != null) parts.push(`품질: ${Math.round(Number(quality) * 100)}%`);
    return parts.join(' · ');
  }

  function renderEquipmentSummary(cand) {
    const list = Array.isArray(cand.equipment_summary) ? cand.equipment_summary : [];
    if (!list.length) return '<strong>주요 보유 설비</strong>—';
    const items = list.slice(0, 3).map(eq => {
      const name = eq.category_name_ko || eq.category_code || '-';
      const rep = eq.representative_model ? ` (${eq.representative_model})` : '';
      const count = eq.count ? ` ×${eq.count}` : '';
      return `${h(name)}${h(rep)}${h(count)}`;
    });
    return `<strong>주요 보유 설비</strong>${items.join('<br>')}`;
  }

  function renderStrengths(cand) {
    const reasons = Array.isArray(cand.reasons) ? cand.reasons : [];
    if (!reasons.length) return '<div class="strength"><i class="ri-check-line"></i> —</div>';
    const sorted = reasons.slice().sort((a, b) => {
      const pa = classifyReason(a).priority;
      const pb = classifyReason(b).priority;
      return pb - pa;
    });
    return sorted.slice(0, 4).map(r => renderReason(r)).join('');
  }

  function renderCompareSidebar(candidates, selectedIndex) {
    const itemsBox = document.getElementById('compare-items');
    const tableBox = document.getElementById('compare-table');
    const selectedName = document.getElementById('compare-selected-name');
    if (!itemsBox || !tableBox) return;

    const top = candidates.slice(0, 3);
    const selected = top[selectedIndex] || top[0] || null;
    if (selectedName) selectedName.textContent = (selected && selected.cand.company_name) || '—';

    itemsBox.innerHTML = top.map((item, i) => {
      const c = item.cand;
      return `
        <div class="compare-item">
          <div class="compare-num">${i + 1}</div>
          <div class="compare-name">${h(c.company_name || '—')}</div>
          <div><div class="compare-price">견적 도착 후</div></div>
        </div>`;
    }).join('') || '<div class="compare-item"><div class="compare-num">—</div><div class="compare-name">—</div><div><div class="compare-price">—</div></div></div>';

    function row(label, vals) {
      const cells = vals.map(v => `<span class="ct-val">${h(v)}</span>`).join('');
      return `<div class="ct-row"><span class="ct-label">${h(label)}</span><div class="ct-vals">${cells}</div></div>`;
    }
    const prices = top.map(() => '견적 도착 후');
    const leads = top.map(() => '—');
    const scores = top.map(item => candidateScore(item.cand));
    const ratings = top.map(item => {
      const r = item.cand.avg_rating || item.cand.rating;
      return r ? `★ ${Number(r).toFixed(1)}` : '—';
    });
    const processes = top.map(item => {
      const p = item.cand.processes || (Array.isArray(item.cand.process_codes) ? item.cand.process_codes.join('·') : '');
      return p || '—';
    });
    const certs = top.map(item => item.cand.certifications_summary || '—');
    const response = top.map(item => item.cand.avg_response_minutes ? `${item.cand.avg_response_minutes}분 이내` : '—');

    tableBox.innerHTML = [
      row('예상 금액', prices),
      row('예상 납기', leads),
      row('AI 매칭 점수', scores),
      row('평균 평점', ratings),
      row('주요 공정', processes),
      row('품질 인증', certs),
      row('응답 속도', response),
    ].join('');
  }

  async function initMatching() {
    await window.imma.requireRole('buyer');
    window.imma.renderSessionHeader();
    const rfqId = window.imma.getQueryParam('rfq_id') || scopedGet(['current_rfq_id']);
    if (!rfqId) return;

    let rfq = null;
    let part = {};
    const result = scopedGet([rfqId, 'match_result']);

    try {
      rfq = await window.imma.apiJson(`/api/rfq/${encodeURIComponent(rfqId)}`);
      part = firstPart(rfq);
      const values = $$('.rfq-summary-card .rfq-value');
      // part_name 은 Phase A GraphRAG 시정 결과 그대로 표시 (원문 보존 정책)
      text(values[0], rfq.rfq_no || shortId(rfq.rfq_id));
      text(values[1], part.part_name || '—');
      text(values[2], processText(part));
      text(values[3], part.quantity || rfq.order_quantity || '—');
      text(values[4], rfq.created_at ? rfq.created_at.slice(0, 10) : '—');
    } catch (err) {
      console.warn('RFQ summary 조회 실패', err);
    }

    try {
      renderAiSummaryCard(rfq || {}, part || {}, result || {});
    } catch (err) {
      console.warn('AI summary hydrate 실패', err);
    }

    const candidates = getMatchCandidates(result).slice(0, 5);
    const rows = $$('.supplier-row');
    let selectedIndex = 0;
    let hydrateOk = false;

    try {
      rows.forEach((row, index) => {
        const item = candidates[index];
        if (!item) return;
        const cand = item.cand;

        // 업체명 + AI 배지
        const h4 = row.querySelector('.s-info h4');
        const badge = h4 && h4.querySelector('.ai-badge');
        if (h4) {
          h4.textContent = `${cand.company_name || '업체명 없음'} `;
          if (badge) {
            badge.textContent = `AI ${candidateScore(cand)}`;
            const tip = buildScoreTooltip(cand);
            if (tip) badge.setAttribute('title', tip);
            h4.appendChild(badge);
          }
        }

        // 지역
        const locEl = row.querySelector('.s-info .loc');
        if (locEl) {
          const region = cand.region || cand.address || cand.location || '';
          const respMin = cand.avg_response_minutes ? ` · 평균 견적 ${cand.avg_response_minutes}분` : '';
          locEl.innerHTML = `<i class="ri-map-pin-line"></i> ${h(region || '—')}${h(respMin)}`;
        }

        // 평점 + 납기
        const ratingEl = row.querySelector('.s-info .s-rating');
        if (ratingEl) {
          const rating = cand.avg_rating || cand.rating;
          const reviewCount = cand.review_count || cand.rating_count;
          const leadAvg = cand.avg_lead_days;
          const ratingTxt = rating ? `<strong>${Number(rating).toFixed(1)}</strong>` : `<strong>—</strong>`;
          const reviewTxt = reviewCount ? `(${reviewCount})` : '';
          const leadTxt = leadAvg ? ` · 평균 납기 ${leadAvg}일` : '';
          ratingEl.innerHTML = `<i class="ri-star-fill star"></i>${ratingTxt}${h(reviewTxt)}${h(leadTxt)}`;
        }

        // 강점 (reasons → 신호 토큰)
        const strengthsEl = row.querySelector('.s-strengths');
        if (strengthsEl) strengthsEl.innerHTML = renderStrengths(cand);

        // 가격 / 일수 — matching 시점 견적 부재
        // (HTML 의 정적 — 유지)

        // 보유 설비
        const equipEl = row.querySelector('.s-equip');
        if (equipEl) equipEl.innerHTML = renderEquipmentSummary(cand);

        // 후보 데이터 + 이벤트
        const payload = {
          rfq_id: rfqId,
          rfq_part_id: cand.rfq_part_id || item.part.rfq_part_id,
          company_id: cand.company_id || cand.company_code,
          company_name: cand.company_name,
          match_run_id: cand.match_run_id,
          rank_no: cand.rank_no,
        };
        row.dataset.candidate = safeJson(payload);
        const actionButton = row.querySelector('.s-actions .btn-primary, .s-actions .btn-outline:last-child');
        if (actionButton) {
          actionButton.type = 'button';
          actionButton.dataset.candidate = safeJson(payload);
          actionButton.addEventListener('click', () => {
            scopedSet([rfqId, 'selected_candidate'], payload);
            rows.forEach(r => r.classList.remove('selected'));
            row.classList.add('selected');
            selectedIndex = index;
            renderCompareSidebar(candidates, selectedIndex);
            window.imma.toast(`${payload.company_name || '후보'}를 표시했습니다.`, 'success');
          });
        }
        const checkbox = row.querySelector('.s-checkbox input');
        if (checkbox) {
          checkbox.addEventListener('change', () => {
            if (checkbox.checked) {
              scopedSet([rfqId, 'selected_candidate'], payload);
              selectedIndex = index;
              renderCompareSidebar(candidates, selectedIndex);
            }
          });
        }
      });

      // 비교 사이드바 hydrate
      renderCompareSidebar(candidates, selectedIndex);
      hydrateOk = candidates.length > 0;
    } catch (err) {
      console.warn('후보 hydrate 실패', err);
    }

    // hydrate 실패 시 row1 의 정적 selected/checked 영역 제거
    if (!hydrateOk) {
      const row1 = document.getElementById('row1');
      if (row1) {
        row1.classList.remove('selected');
        const cb = row1.querySelector('.s-checkbox input');
        if (cb) cb.checked = false;
      }
    }

    const proceedBtn = $('#compare-proceed-btn') || $('.compare-box a[href="/order-management"]');
    if (proceedBtn) proceedBtn.href = `/order-management?rfq_id=${encodeURIComponent(rfqId)}`;

    // 상세 보기 modal hook — row 첫 번째 .btn-outline 클릭 시 후보 정보 modal 노출.
    bindSupplierDetailModal(candidates);
  }

  // D-2 시정: supplier-row 의 *상세 보기* 버튼은 R8 까지 동작 부재.
  // matching.html 의 #supplier-detail-modal 영역에 후보 정보 hydrate 후 노출한다.
  function bindSupplierDetailModal(candidates) {
    const rows = $$('.supplier-row');
    rows.forEach((row, index) => {
      const item = candidates[index];
      if (!item) return;
      const cand = item.cand;
      // .s-actions 의 첫 번째 .btn-outline = "상세 보기" (action 버튼은 :last-child 로 분리 hook 되어 있음).
      const detailBtn = row.querySelector('.s-actions .btn-outline');
      if (!detailBtn || detailBtn.dataset.immaDetailHooked === 'true') return;
      detailBtn.dataset.immaDetailHooked = 'true';
      detailBtn.type = 'button';
      detailBtn.addEventListener('click', (ev) => {
        ev.preventDefault();
        openSupplierDetailModal(cand);
      });
    });

    // 닫기 hook 은 modal 영역에 한 번만 부착.
    const modal = $('#supplier-detail-modal');
    const closeBtn = $('#supplier-detail-close');
    if (modal && closeBtn && closeBtn.dataset.immaHooked !== 'true') {
      closeBtn.dataset.immaHooked = 'true';
      closeBtn.addEventListener('click', () => modal.classList.remove('open'));
      // overlay 바깥 클릭 시 닫기 — modal 내부 클릭은 무시.
      modal.addEventListener('click', (ev) => {
        if (ev.target === modal) modal.classList.remove('open');
      });
    }
  }

  function openSupplierDetailModal(cand) {
    const modal = $('#supplier-detail-modal');
    if (!modal) return;

    text($('#sd-company-name'), cand.company_name || '-');

    // 추천 / 조건부 배지 — equipment_verified 기반 (matching summary 의 기존 분류 정합).
    const isRec = !!cand.equipment_verified && !cand.equipment_verified_warning;
    const badgeEl = $('#sd-recommend-badge');
    if (badgeEl) {
      const bgColor = isRec ? '#ecfdf3' : '#fffaeb';
      const fgColor = isRec ? '#027a48' : '#b54708';
      const label = isRec ? '추천' : '조건부';
      badgeEl.innerHTML = `<span class="sd-recommend-chip" style="background:${bgColor};color:${fgColor};">${label}</span>`;
    }

    // 점수 분해 — buildScoreTooltip 과 동일 source (cand.score_breakdown 우선, fallback breakdown).
    const breakdown = cand.score_breakdown || cand.breakdown || {};
    const technical = breakdown.technical ?? breakdown.tech ?? null;
    const availability = breakdown.availability ?? breakdown.avail ?? null;
    const quality = breakdown.quality ?? breakdown.qual ?? null;
    const tech = technical != null ? Math.round(Number(technical) * 100) : '-';
    const avail = availability != null ? Math.round(Number(availability) * 100) : '-';
    const qual = quality != null ? Math.round(Number(quality) * 100) : '-';
    const total = candidateScore(cand);
    const breakdownEl = $('#sd-score-breakdown');
    if (breakdownEl) {
      breakdownEl.innerHTML = `
        <div>총점 <strong>${h(total)}</strong></div>
        <div style="display:flex;gap:14px;margin-top:8px;font-size:13px;color:var(--text-muted);">
          <span>기술 ${h(tech)}</span><span>가용 ${h(avail)}</span><span>품질 ${h(qual)}</span>
        </div>`;
    }

    // 매칭 사유 — renderReason 재활용으로 색상 분류 정합 유지.
    const reasons = Array.isArray(cand.reasons) ? cand.reasons : [];
    const reasonEl = $('#sd-match-reasons');
    if (reasonEl) {
      reasonEl.innerHTML = reasons.length
        ? reasons.map(r => renderReason(r)).join('')
        : '<span style="color:var(--text-muted);font-size:13px;">매칭 사유 정보 부재</span>';
    }

    // 보유 장비 — equipment_summary 의 전체 목록 노출 (renderEquipmentSummary 는 3 건 제한).
    const summary = Array.isArray(cand.equipment_summary) ? cand.equipment_summary : [];
    const equipEl = $('#sd-equipment-list');
    if (equipEl) {
      equipEl.innerHTML = summary.length
        ? summary.map(eq => {
            const name = eq.category_name_ko || eq.category_code || '-';
            const rep = eq.representative_model || '-';
            const count = eq.count != null ? `${eq.count}대` : '-';
            return `
              <div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);font-size:13px;">
                <span><strong>${h(name)}</strong> ${h(count)}</span>
                <span style="color:var(--text-muted);">${h(rep)}</span>
              </div>`;
          }).join('')
        : '<span style="color:var(--text-muted);font-size:13px;">장비 정보 부재</span>';
    }

    // 평점 + 납기 — matching summary 와 동일 field (avg_rating / review_count / avg_lead_days).
    const rating = cand.avg_rating != null ? Number(cand.avg_rating).toFixed(1) : '-';
    const reviewCount = cand.review_count || cand.rating_count || 0;
    const leadDays = cand.avg_lead_days != null
      ? cand.avg_lead_days
      : (cand.availability_info && cand.availability_info.estimated_lead_days);
    const ratingEl = $('#sd-rating-lead');
    if (ratingEl) {
      ratingEl.innerHTML = `
        <div>평점 <strong>★ ${h(rating)}</strong> <span style="color:var(--text-muted);">(${h(reviewCount)} 리뷰)</span></div>
        <div style="margin-top:6px;">예상 납기 <strong>${leadDays != null ? h(leadDays) + '일' : '-'}</strong></div>`;
    }

    modal.classList.add('open');
  }

  function updateTimeline(status) {
    const order = ['contracting', 'ordered', 'in_production', 'inspection', 'shipped', 'delivered'];
    const index = Math.max(0, order.indexOf(status));
    $$('.pt-step').forEach((step, i) => {
      step.classList.remove('done', 'active');
      if (i < index) step.classList.add('done');
      else if (i === index) step.classList.add('active');
    });
    $$('.pt-line').forEach((line, i) => line.classList.toggle('done', i < index));
  }

  function renderOrderActions(order, user) {
    const transitions = {
      buyer: {
        contracting: ['ordered', 'cancelled'],
        ordered: ['cancelled'],
        in_production: ['cancelled', 'disputed'],
        inspection: ['in_production', 'disputed'],
        shipped: ['delivered', 'disputed'],
        delivered: ['completed', 'disputed'],
      },
      supplier: {
        contracting: ['ordered'],
        ordered: ['in_production'],
        in_production: ['inspection', 'disputed'],
        inspection: ['shipped', 'disputed'],
        delivered: ['disputed'],
      },
    };
    const targets = (transitions[user.role] && transitions[user.role][order.status]) || [];
    if (!targets.length || $('.imma-order-actions')) return;
    const timeline = $('.process-timeline');
    if (!timeline) return;
    const box = document.createElement('div');
    box.className = 'card imma-order-actions';
    box.style.margin = '16px 0';
    box.innerHTML = `
      <div class="flex-between" style="gap:12px;flex-wrap:wrap;">
        <div><strong>실 API 상태 전이</strong><div style="font-size:12px;color:var(--text-muted);margin-top:4px;">현재 상태: ${h(order.status)}</div></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">${targets.map(t => `<button type="button" class="btn-outline" data-next-status="${h(t)}" style="font-size:12px;padding:7px 10px;">${h(t)}</button>`).join('')}</div>
      </div>`;
    timeline.insertAdjacentElement('afterend', box);
    $$('button[data-next-status]', box).forEach(btn => btn.addEventListener('click', async () => {
      window.imma.setLoading(btn, true, '변경 중...');
      try {
        await window.imma.apiJson(`/api/orders/${encodeURIComponent(order.order_id)}/status`, { method: 'PUT', body: { status: btn.dataset.nextStatus } });
        window.imma.toast('주문 상태를 변경했습니다.', 'success');
        window.location.reload();
      } catch (err) {
        window.imma.toast(err.message, 'error');
      } finally {
        window.imma.setLoading(btn, false);
      }
    }));
  }

  async function initOrderManagement() {
    const user = await window.imma.requireRole(['buyer', 'supplier']);
    window.imma.renderSessionHeader();
    const rfqId = window.imma.getQueryParam('rfq_id');
    const orderId = window.imma.getQueryParam('order_id') || scopedGet(['current_order_id']);

    if (orderId) {
      try {
        const order = await window.imma.apiJson(`/api/orders/${encodeURIComponent(orderId)}`);
        const meta = $$('.order-meta .meta-field');
        if (meta[0]) {
          const v = meta[0].querySelector('.meta-value');
          if (v) v.innerHTML = `${h(shortId(order.order_id))} <button class="copy-btn">복사</button>`;
          text(meta[0].querySelector('.meta-sub'), `발주일: ${order.created_at ? order.created_at.slice(0, 10) : '-'}`);
        }
        if (meta[1]) text(meta[1].querySelector('.meta-value'), order.company_name || '-');
        if (meta[2]) text(meta[2].querySelector('.meta-value'), window.imma.formatCurrency(order.total_price, order.currency_code || 'KRW'));
        if (meta[3]) text(meta[3].querySelector('.meta-value'), order.promised_delivery_date || '-');
        const badge = $('.payment-status .badge');
        if (badge) badge.textContent = order.status === 'contracting' ? '계약 진행 중' : order.status;
        updateTimeline(order.status);
        renderOrderActions(order, user);
      } catch (err) {
        window.imma.toast(err.message, 'error');
      }
      return;
    }

    let currentOrderIdForPayment = null;

    if (rfqId && user.role === 'buyer') {
      let pollingId = null;

      async function refreshRfqState() {
        try {
          const [quotesData, notifications] = await Promise.all([
            window.imma.apiJson(`/api/rfq/${encodeURIComponent(rfqId)}/quotes`),
            window.imma.apiJson('/api/notifications?unread_only=false').catch(() => []),
          ]);
          const quotes = quotesData.quotes || [];

          // 수락 받음 배지 — supplier_accepted 알림 영역 (본 RFQ 한정)
          const acceptedEvents = (notifications || []).filter(n =>
            n.event_type === 'supplier_accepted' && n.reference_id === rfqId
          );
          renderAcceptanceBadge(acceptedEvents.length > 0, acceptedEvents[0]);

          if (quotes.length) {
            renderQuoteCard(quotes[0]);
            if (pollingId) { clearInterval(pollingId); pollingId = null; }
          } else {
            renderWaitingCard();
          }
        } catch (err) { /* silent — 폴링 영역 */ }
      }

      function renderAcceptanceBadge(accepted, evt) {
        let badge = $('#imma-accept-badge');
        if (!accepted) { if (badge) badge.remove(); return; }
        if (badge) return;
        badge = document.createElement('div');
        badge.id = 'imma-accept-badge';
        badge.style.cssText = 'margin:0 0 12px;padding:10px 14px;background:#dcfce7;border:1px solid #86efac;border-radius:8px;display:flex;align-items:center;gap:10px;font-size:13px;font-weight:700;color:#166534;line-height:1.4;';
        const msg = (evt && evt.message) || '가공업체가 작업을 수락했습니다';
        badge.innerHTML = `<i class="ri-check-double-line" style="font-size:18px;flex-shrink:0;"></i><span>✓ ${h(msg)}</span>`;
        const pageWrap = $('.page-wrap') || document.body;
        pageWrap.insertBefore(badge, pageWrap.firstChild);
      }

      function renderWaitingCard() {
        if ($('.imma-quote-card-real')) return;
        if ($('.imma-quote-empty')) return;
        const emptyBox = document.createElement('div');
        emptyBox.className = 'card imma-quote-empty';
        emptyBox.style.margin = '0 0 24px';
        emptyBox.style.padding = '24px';
        emptyBox.style.textAlign = 'center';
        emptyBox.innerHTML = `
          <div style="font-size:36px; color:var(--text-muted); margin-bottom:10px;"><i class="ri-time-line"></i></div>
          <div style="font-size:15px; font-weight:800; color:#111; margin-bottom:6px;">견적 작성 대기 중</div>
          <div style="font-size:13px; color:var(--text-muted); line-height:1.6;">가공업체가 작업정보를 회신하면 자동으로 갱신됩니다.</div>`;
        const pageWrap = $('.page-wrap') || document.body;
        const acceptBadge = $('#imma-accept-badge');
        if (acceptBadge) acceptBadge.insertAdjacentElement('afterend', emptyBox);
        else pageWrap.insertBefore(emptyBox, pageWrap.firstChild);
      }

      async function renderQuoteCard(quote) {
        // 대기 카드 제거
        const wait = $('.imma-quote-empty');
        if (wait) wait.remove();
        if ($('.imma-quote-card-real')) return;

        const { certification, post_treatment, memo } = parseQuoteAssumptions(quote.assumptions);
        const card = document.createElement('div');
        card.className = 'card imma-quote-card-real';
        card.style.cssText = 'margin:0 0 24px;padding:24px;';
        card.innerHTML = `
          <div style="font-size:15px; font-weight:800; color:#111; margin-bottom:16px; display:flex; align-items:center; gap:8px;">
            <i class="ri-mail-check-line" style="color:#166534;font-size:18px;"></i>
            <span>가공업체 회신 도착 — ${h(quote.company_name || '가공업체')}</span>
          </div>
          <div style="display:grid; grid-template-columns:140px 1fr; gap:10px 16px; font-size:13px;">
            <div style="color:var(--text-muted); font-weight:700;">예상 납기</div><div><strong>${h(quote.proposed_delivery_date || (quote.estimated_lead_days ? quote.estimated_lead_days + '일' : '-'))}</strong></div>
            <div style="color:var(--text-muted); font-weight:700;">견적 금액</div><div><strong style="font-size:15px; color:#111;">${h(window.imma.formatCurrency(quote.total_price, 'KRW'))}</strong></div>
            <div style="color:var(--text-muted); font-weight:700;">품질 인증</div><div>${h(certification || '-')}</div>
            <div style="color:var(--text-muted); font-weight:700;">후처리·조립</div><div>${h(post_treatment || '-')}</div>
            <div style="color:var(--text-muted); font-weight:700;">작업 메모</div><div style="white-space:pre-wrap;">${h(memo || '-')}</div>
          </div>
          <div style="margin-top:20px; display:flex; gap:12px; justify-content:flex-end;">
            <button type="button" class="btn-primary imma-confirm-order" style="font-size:14px; padding:10px 20px;"><i class="ri-shopping-cart-line"></i> 이 견적으로 발주 확정</button>
          </div>`;
        const pageWrap = $('.page-wrap') || document.body;
        const acceptBadge = $('#imma-accept-badge');
        if (acceptBadge) acceptBadge.insertAdjacentElement('afterend', card);
        else pageWrap.insertBefore(card, pageWrap.firstChild);
        card.querySelector('.imma-confirm-order').addEventListener('click', async (ev) => {
          const btn = ev.currentTarget;
          window.imma.setLoading(btn, true, '발주 생성 중...');
          try {
            const order = await window.imma.apiJson('/api/orders', { method: 'POST', body: { quote_id: quote.quote_id } });
            scopedSet(['current_order_id'], order.order_id);
            currentOrderIdForPayment = order.order_id;
            window.location.href = `/order-management?order_id=${encodeURIComponent(order.order_id)}`;
          } catch (err) {
            window.imma.toast(err.message, 'error');
          } finally {
            window.imma.setLoading(btn, false);
          }
        });
      }

      await refreshRfqState();
      // 폴링 — 5 초 주기 (견적 도착 시 자동 중단)
      pollingId = setInterval(refreshRfqState, 5000);
    }

    // 결제하기 버튼 hook — order_id query 영역 전달 (payment-success 동적 hydrate 영역)
    const payBtn = $('#pay-btn');
    if (payBtn) {
      payBtn.addEventListener('click', () => {
        const oid = orderId || currentOrderIdForPayment || scopedGet(['current_order_id']);
        const url = oid ? `/payment-success?order_id=${encodeURIComponent(oid)}` : '/payment-success';
        window.location.href = url;
      });
    }
  }

  // ── 신호 토큰 분류 (Cortex §4.8 정합) ──
  function classifyReason(reason) {
    if (typeof reason !== 'string') return { kind: 'neutral', priority: 0, label: null };
    const s = reason.trim();
    if (s.startsWith('[INFO_CATEGORY_FALLBACK]')) return { kind: 'info', priority: 2, label: '카테고리 폴백' };
    if (s.startsWith('[INFO_PARENT_FALLBACK]')) return { kind: 'info', priority: 2, label: '부모 공정 폴백' };
    if (s.startsWith('[WARN_EQUIPMENT_CAPABILITY_MISSING]')) return { kind: 'warn', priority: 4, label: '장비 검증 필요' };
    if (s.startsWith('[공정 달성범위 의심·재질override]')) return { kind: 'warn', priority: 4, label: '재질 override' };
    if (s.startsWith('[공정 달성범위 의심]')) return { kind: 'warn', priority: 4, label: '달성범위 의심' };
    if (s.startsWith('[공정순서 위반]')) return { kind: 'danger', priority: 5, label: '공정순서 위반' };
    if (s.startsWith('[공정순서 권장위반]')) return { kind: 'warn', priority: 3, label: '공정순서 권장위반' };
    if (s.startsWith('[unsupported]')) return { kind: 'danger', priority: 5, label: '미지원' };
    if (/매칭|충족|범위 내|보유/.test(s)) return { kind: 'positive', priority: 1, label: null };
    return { kind: 'neutral', priority: 0, label: null };
  }

  function cleanReason(reason) {
    if (typeof reason !== 'string') return '';
    return reason
      .replace(/^\[INFO_CATEGORY_FALLBACK\]\s*/, '')
      .replace(/^\[INFO_PARENT_FALLBACK\]\s*/, '')
      .replace(/^\[WARN_EQUIPMENT_CAPABILITY_MISSING\]\s*/, '')
      .replace(/^\[공정 달성범위 의심·재질override\]\s*/, '')
      .replace(/^\[공정 달성범위 의심\]\s*/, '')
      .replace(/^\[공정순서 위반\]\s*/, '')
      .replace(/^\[공정순서 권장위반\]\s*/, '')
      .replace(/^\[unsupported\]\s*/, '')
      .trim();
  }

  function renderReason(reason) {
    const cls = classifyReason(reason);
    const text = cleanReason(reason);
    const colorMap = {
      'positive': 'background:#ecfdf3;color:#027a48;',
      'info':     'background:#eff6ff;color:#1d4ed8;',
      'warn':     'background:#fffaeb;color:#b54708;',
      'danger':   'background:#fef3f2;color:#b42318;',
      'neutral':  'background:#f2f4f7;color:#344054;',
    };
    const style = colorMap[cls.kind] || colorMap['neutral'];
    const label = cls.label ? `<strong>${window.imma.escapeHtml(cls.label)}:</strong> ` : '';
    return `<span class="imma-match-chip" style="${style}padding:4px 8px;border-radius:999px;font-size:11px;font-weight:600;margin:2px;display:inline-block;">${label}${window.imma.escapeHtml(text)}</span>`;
  }

  function bindLogout() {
    $$('.logout-btn').forEach(btn => {
      if (btn.dataset.immaHooked === 'true') return;
      btn.dataset.immaHooked = 'true';
      btn.addEventListener('click', () => window.imma.logout('manual'));
    });
  }

  async function initSupplierDashboard() {
    const user = await window.imma.requireRole('supplier');
    window.imma.renderSessionHeader();
    bindLogout();
    const greeting = $('.page-wrap p[style*="font-size:14px"]');
    if (greeting && user.company_name) greeting.textContent = `👋 안녕하세요, ${user.company_name} ${user.name || user.contact_name || ''}님! 오늘도 안전한 하루 되세요.`;
    text($('.u-name'), user.company_name || user.name || '공급사');
    try {
      const [matchData, notifications] = await Promise.all([
        window.imma.apiJson('/api/company/matches'),
        window.imma.apiJson('/api/notifications?unread_only=false').catch(() => []),
      ]);
      const matches = matchData.matches || [];
      const orderEvents = (notifications || []).filter(n => n.event_type === 'order_confirmed' && n.reference_type === 'order');
      const kpiCards = $$('.kpi-card');
      setCardValue(kpiCards[0], matchData.count || matches.length);
      setCardValue(kpiCards[1], orderEvents.length);
      text($('.section-title-row .count-badge'), String(matchData.count || matches.length));
      if (matches.length) {
        const enriched = await enrichMatches(matches, 5);
        const tbody = $('.card .data-table tbody');
        if (tbody) {
          tbody.innerHTML = enriched.map(m => {
            const part = m.rfq_part || {};
            return `<tr>
              <td class="td-primary">RFQ-${h(shortId(m.rfq_id))}</td>
              <td>${h(part.part_name || m.part_name || '-')}</td>
              <td>${h(processText(part) !== '-' ? processText(part) : (m.processes || '-'))}</td>
              <td>${h(part.quantity || '-')}</td>
              <td>${h((m.rfq && m.rfq.requested_delivery_date) || '-')}</td>
              <td><span class="badge badge-yellow">${h(m.supplier_response || 'pending')}</span></td>
              <td><a href="/supplier-rfq-detail?rfq_id=${encodeURIComponent(m.rfq_id)}" class="btn-primary" style="padding:6px 14px;font-size:12px;">요청 상세</a></td>
            </tr>`;
          }).join('');
        }
      }
    } catch (err) {
      window.imma.toast(err.message, 'error');
    }
  }

  function bindWorkbenchScrollSpy() {
    const nav = $('.mw-side-nav');
    if (!nav || nav.dataset.immaScrollSpy === 'true') return;
    const scroller = $('.mw-app-main') || document.scrollingElement || document.documentElement;
    const sectionIds = ['rfq', 'reply', 'orders', 'production', 'capacity', 'delivery', 'reviews'];
    const sections = sectionIds
      .map(id => ({ id, el: document.getElementById(id) }))
      .filter(s => s.el);
    if (!sections.length) return;
    nav.dataset.immaScrollSpy = 'true';

    const navLinks = $$('a', nav);
    function linkForSection(id) {
      const exact = navLinks.find(a => {
        const href = a.getAttribute('href') || '';
        return href === `#${id}` || href === `/supplier-workbench#${id}`;
      });
      if (exact) return exact;
      if (id === 'rfq') {
        return navLinks.find(a => {
          const href = a.getAttribute('href') || '';
          return href === '/supplier-workbench' || href === '#rfq';
        }) || null;
      }
      return null;
    }

    function activate(id) {
      navLinks.forEach(a => a.classList.remove('active'));
      const target = linkForSection(id);
      if (target) target.classList.add('active');
    }

    activate('rfq');

    const observer = new IntersectionObserver(entries => {
      const visible = entries
        .filter(e => e.isIntersecting)
        .map(e => ({ id: e.target.id, top: e.boundingClientRect.top }));
      if (!visible.length) return;
      visible.sort((a, b) => a.top - b.top);
      activate(visible[0].id);
    }, {
      root: scroller === document.scrollingElement || scroller === document.documentElement ? null : scroller,
      rootMargin: '-80px 0px -60% 0px',
      threshold: 0,
    });
    sections.forEach(s => observer.observe(s.el));

    navLinks.forEach(a => {
      const href = a.getAttribute('href') || '';
      const match = href.match(/#([a-zA-Z0-9_-]+)$/);
      if (!match) return;
      const id = match[1];
      if (!sectionIds.includes(id)) return;
      a.addEventListener('click', () => {
        setTimeout(() => activate(id), 0);
      });
    });
  }

  async function initSupplierWorkbench() {
    const user = await window.imma.requireRole('supplier');
    window.imma.renderSessionHeader();
    bindLogout();
    bindWorkbenchScrollSpy();
    text($('.mw-side-user strong'), user.company_name || user.name || '공급사');

    // #orders 영역 — order_confirmed 알림 영역 폴링 + 발주 확인 수락 row hydrate
    let currentOrderForAccept = null;
    let ordersPollingId = null;
    async function refreshOrdersSection() {
      const row = $('#order-accept-row');
      const metaEl = $('#order-accept-meta');
      const checkbox = $('#order-accept-check');
      if (!row || !metaEl || !checkbox) return;
      try {
        const notifications = await window.imma.apiJson('/api/notifications?unread_only=false').catch(() => []);
        const orderEvents = (notifications || []).filter(n =>
          n.event_type === 'order_confirmed' && n.reference_type === 'order'
        );
        if (!orderEvents.length) return;  // 대기 영역 유지
        // 가장 최근 발주 영역 조회
        const latestOrderId = orderEvents[0].reference_id;
        if (currentOrderForAccept && currentOrderForAccept.order_id === latestOrderId) return;
        const order = await window.imma.apiJson(`/api/orders/${encodeURIComponent(latestOrderId)}`);
        currentOrderForAccept = order;
        const shortPo = (order.order_id || '').slice(0, 8).toUpperCase();
        const totalDisplay = window.imma.formatCurrency(order.total_price, order.currency_code || 'KRW');
        metaEl.innerHTML = `PO-${h(shortPo)} · ${h(order.company_name || '클라이언트')} · ${h(totalDisplay)}`;
        // 이미 ordered 이상 상태면 체크 + disabled
        if (order.status && order.status !== 'contracting') {
          checkbox.checked = true;
          checkbox.disabled = true;
        } else {
          checkbox.disabled = false;
        }
      } catch (err) { /* silent — 폴링 영역 */ }
    }

    // 발주 확인 수락 체크 hook — 한 번만 부착
    const acceptCheckbox = $('#order-accept-check');
    if (acceptCheckbox && acceptCheckbox.dataset.immaHooked !== 'true') {
      acceptCheckbox.dataset.immaHooked = 'true';
      acceptCheckbox.addEventListener('change', async () => {
        if (!acceptCheckbox.checked) return;
        if (!currentOrderForAccept) {
          window.imma.toast('수락할 발주 영역 부재 — 폴링 대기 중', 'warning');
          acceptCheckbox.checked = false;
          return;
        }
        acceptCheckbox.disabled = true;
        try {
          // 1. order status 영역 contracting → ordered 전이
          await window.imma.apiJson(`/api/orders/${encodeURIComponent(currentOrderForAccept.order_id)}/status`, {
            method: 'PUT',
            body: { status: 'ordered' },
          });
          // 2. 작업 (job) 생성
          await window.imma.apiJson('/api/jobs', {
            method: 'POST',
            body: {
              order_id: currentOrderForAccept.order_id,
              part_name: currentOrderForAccept.part_name || '가공 작업',
              quantity: currentOrderForAccept.quantity || 1,
            },
          });
          window.imma.toast('발주 수락 완료 — 작업 생성됨', 'success');
          // #production 영역 자동 스크롤
          const productionSection = document.getElementById('production');
          if (productionSection) {
            setTimeout(() => productionSection.scrollIntoView({ behavior: 'smooth', block: 'start' }), 300);
          }
        } catch (err) {
          window.imma.toast(err.message, 'error');
          acceptCheckbox.checked = false;
          acceptCheckbox.disabled = false;
        }
      });
    }

    let currentMatches = [];
    async function respond(matchRunId, response) {
      try {
        await window.imma.apiJson(`/api/match-candidates/${encodeURIComponent(matchRunId)}/${encodeURIComponent(user.id)}/respond`, { method: 'PUT', body: { response } });
        window.imma.toast(response === 'accepted' ? '수락 완료 — 견적 작성 영역으로 이동합니다.' : '응답이 저장되었습니다.', 'success');
        await refresh();
        // 수락 시 #reply 영역으로 자동 스크롤 (견적 작성 UX 연결)
        if (response === 'accepted') {
          const replySection = document.getElementById('reply');
          if (replySection) {
            setTimeout(() => replySection.scrollIntoView({ behavior: 'smooth', block: 'start' }), 300);
          }
        }
      } catch (err) {
        window.imma.toast(err.message, 'error');
      }
    }

    async function refresh() {
      const data = await window.imma.apiJson('/api/company/matches');
      currentMatches = await enrichMatches(data.matches || [], 5);
      const kpis = $$('.mw-kpi-value');
      if (kpis[0]) kpis[0].textContent = `${data.count || currentMatches.length}건`;
      const badge = $('#rfq .mw-badge');
      if (badge) badge.textContent = `신규 ${data.count || currentMatches.length}건`;
      const tbody = $('#rfq .mw-table tbody');
      if (tbody && currentMatches.length) {
        tbody.innerHTML = currentMatches.map(m => {
          const part = m.rfq_part || {};
          const due = (m.rfq && m.rfq.requested_delivery_date) || '-';
          const condition = `${processText(part) !== '-' ? processText(part) : (m.processes || '-')} · ${partMaterial(part) !== '-' ? partMaterial(part) : (m.material || '-')} · ${part.quantity || '-'} EA`;
          return `<tr>
            <td><div class="mw-id">RFQ-${h(shortId(m.rfq_id))}</div><div class="mw-list-meta">${h(part.part_name || m.part_name || '-')}</div></td>
            <td>${h(condition)}</td>
            <td>${h(m.total_score == null ? '-' : `AI ${candidateScore(m)}`)}</td>
            <td>${h(due)}</td>
            <td><div class="mw-row-actions">
              <button class="btn-primary accept-match" data-match-run-id="${h(m.match_run_id)}" style="padding:7px 10px;font-size:12px;">수락</button>
              <button class="btn-outline decline-match" data-match-run-id="${h(m.match_run_id)}" style="padding:7px 10px;font-size:12px;">거절</button>
            </div></td>
          </tr>`;
        }).join('');
        $$('.accept-match', tbody).forEach(btn => btn.addEventListener('click', () => respond(btn.dataset.matchRunId, 'accepted')));
        $$('.decline-match', tbody).forEach(btn => btn.addEventListener('click', () => respond(btn.dataset.matchRunId, 'declined')));
      }
    }

    try {
      await refresh();
      const sendBtn = $('#reply .mw-row-actions .btn-primary');
      if (sendBtn && sendBtn.dataset.immaHooked !== 'true') {
        sendBtn.dataset.immaHooked = 'true';
        sendBtn.addEventListener('click', async e => {
          e.preventDefault();
          const match = currentMatches.find(m => m.supplier_response === 'accepted') || currentMatches[0];
          if (!match) {
            window.imma.toast('수신 매칭이 없습니다.', 'warning');
            return;
          }
          const payload = quotePayloadFromWorkbench(match, user);
          if (!payload) {
            window.imma.toast('견적 금액을 입력해주세요.', 'warning');
            return;
          }
          window.imma.setLoading(sendBtn, true, '발송 중...');
          try {
            if (match.supplier_response !== 'accepted') {
              await window.imma.apiJson(`/api/match-candidates/${encodeURIComponent(match.match_run_id)}/${encodeURIComponent(user.id)}/respond`, { method: 'PUT', body: { response: 'accepted' } });
            }
            await window.imma.apiJson('/api/quote', { method: 'POST', body: payload });
            window.imma.toast('견적이 제출되었습니다.', 'success');
            await refresh();
          } catch (err) {
            window.imma.toast(err.message, 'error');
          } finally {
            window.imma.setLoading(sendBtn, false);
          }
        });
      }
    } catch (err) {
      window.imma.toast(err.message, 'error');
    }

    // #orders 영역 초기 hydrate + 폴링 5 초 주기
    await refreshOrdersSection();
    ordersPollingId = setInterval(refreshOrdersSection, 5000);
  }

  // 장비 카테고리 → 추정 재질 카테고리 자동 추천 매핑.
  // 시연용 정적 dict — equipment_category_catalog 의 카테고리 코드를 기준으로 작성한다.
  const EQUIPMENT_TO_MATERIAL_HINT = {
    cnc_lathe:                ['carbon_steel', 'alloy_steel', 'stainless_steel', 'aluminum_alloy', 'copper_alloy', 'free_cutting_steel', 'tool_steel'],
    general_lathe:            ['carbon_steel', 'alloy_steel', 'stainless_steel', 'aluminum_alloy', 'copper_alloy', 'free_cutting_steel'],
    machining_center_3axis:   ['carbon_steel', 'alloy_steel', 'stainless_steel', 'aluminum_alloy', 'copper_alloy', 'tool_steel'],
    machining_center_5axis:   ['carbon_steel', 'alloy_steel', 'stainless_steel', 'aluminum_alloy', 'copper_alloy', 'tool_steel'],
    mill_turn:                ['carbon_steel', 'alloy_steel', 'stainless_steel', 'aluminum_alloy', 'copper_alloy', 'tool_steel'],
    drilling_machine:         ['carbon_steel', 'alloy_steel', 'stainless_steel', 'aluminum_alloy'],
    boring_machine:           ['carbon_steel', 'alloy_steel', 'stainless_steel', 'gray_cast_iron', 'ductile_cast_iron'],
    cnc_router:               ['aluminum_alloy', 'engineering_plastic', 'composite_insulation'],
    surface_grinder:          ['carbon_steel', 'alloy_steel', 'stainless_steel', 'tool_steel'],
    cylindrical_grinder:      ['carbon_steel', 'alloy_steel', 'stainless_steel', 'tool_steel'],
    internal_grinder:         ['carbon_steel', 'alloy_steel', 'stainless_steel', 'tool_steel'],
    edm_sinker:               ['tool_steel', 'alloy_steel', 'carbon_steel', 'stainless_steel'],
    edm_wire:                 ['tool_steel', 'alloy_steel', 'carbon_steel', 'stainless_steel'],
    hobbing_machine:          ['carbon_steel', 'alloy_steel', 'stainless_steel'],
    heat_treatment_furnace:   ['carbon_steel', 'alloy_steel', 'stainless_steel', 'tool_steel'],
    welding_equipment:        ['carbon_steel', 'stainless_steel', 'alloy_steel', 'aluminum_alloy'],
    laser_cutting_machine:    ['carbon_steel', 'stainless_steel', 'aluminum_alloy', 'sheet_steel'],
    plasma_cutting_machine:   ['carbon_steel', 'stainless_steel', 'aluminum_alloy', 'sheet_steel'],
    waterjet_cutting_machine: ['carbon_steel', 'stainless_steel', 'aluminum_alloy', 'sheet_steel', 'composite_insulation', 'tool_steel'],
    press_brake:              ['carbon_steel', 'stainless_steel', 'aluminum_alloy', 'sheet_steel'],
    press_machine:            ['carbon_steel', 'stainless_steel', 'aluminum_alloy', 'sheet_steel'],
    casting_foundry:          ['gray_cast_iron', 'ductile_cast_iron', 'cast_steel', 'stainless_cast_steel', 'aluminum_alloy', 'copper_alloy'],
  };

  function onboardingProgressText(detail) {
    return `장비 ${detail.equipmentCount} / 재질 ${detail.materialCount} / BRN ${detail.hasBrn ? '입력됨' : '부재'} / region ${detail.hasRegion ? '입력됨' : '부재'}`;
  }

  function applyOnboardingStatus(detail) {
    const badge = $('#onboarding-status-badge');
    const bannerBadge = $('#onboarding-banner-badge');
    const banner = $('#onboarding-banner');
    const progress = $('#onboarding-progress');
    const status = detail.status || 'draft';
    if (badge) { badge.textContent = status; badge.dataset.status = status; }
    if (bannerBadge) { bannerBadge.textContent = status; bannerBadge.dataset.status = status; }
    if (banner) banner.classList.toggle('is-verified', status === 'verified');
    if (progress) progress.textContent = onboardingProgressText(detail);
  }

  function renderEquipmentList(equipment) {
    const list = $('#equipment-list');
    if (!list) return;
    if (!equipment || equipment.length === 0) {
      list.innerHTML = '<div class="equipment-empty">등록된 장비가 없습니다. 아래에서 첫 장비를 추가해주세요.</div>';
      return;
    }
    list.innerHTML = equipment.map(eq => {
      const meta = [eq.equipment_category_code, eq.manufacturer, eq.model_name]
        .filter(Boolean).join(' · ');
      return `<div class="equipment-row">
        <div>
          <div class="eq-name">${h(eq.display_name || '(이름 없음)')}</div>
          <div class="eq-meta">${h(meta || '카탈로그 정보 없음')}</div>
        </div>
        <span class="mw-badge ${eq.status === 'running' ? 'green' : 'yellow'}">${h(eq.status || '-')}</span>
      </div>`;
    }).join('');
  }

  function renderMaterialChips(categories, lockedSet, pendingSet, autoHintSet) {
    // 선택 상태를 Set 으로 source of truth 유지 — DOM innerHTML 재구성에도 pending/autoHint 가 다시 반영됨.
    // locked: DB 저장 완료 (✓ disabled). pending: 저장 전 사용자 선택 또는 자동 추천 (selected 시각).
    // autoHint: pending 중 *(자동)* 라벨 표시 대상.
    const wrap = $('#material-categories');
    if (!wrap) return;
    const locked = lockedSet instanceof Set ? lockedSet : new Set(lockedSet || []);
    const pending = pendingSet instanceof Set ? pendingSet : new Set(pendingSet || []);
    const autoHints = autoHintSet instanceof Set ? autoHintSet : new Set(autoHintSet || []);

    wrap.innerHTML = categories.map(c => {
      const code = c.category_code;
      const isLocked = locked.has(code);
      const isPending = !isLocked && pending.has(code);
      const isAutoHint = isPending && autoHints.has(code);
      const classes = ['ob-chip'];
      if (isLocked) classes.push('is-locked');
      if (isPending) classes.push('selected');
      const label = `${c.category_name_ko}${isLocked ? ' ✓' : ''}`;
      const ariaPressed = isLocked ? 'true' : (isPending ? 'true' : 'false');
      const autoAttr = isAutoHint ? ' data-auto-selected="true"' : '';
      const lockedAttr = isLocked ? ' disabled' : '';
      return `<button type="button" class="${classes.join(' ')}" data-code="${h(code)}"${autoAttr}${lockedAttr} aria-pressed="${ariaPressed}">${h(label)}</button>`;
    }).join('');

    $$('.ob-chip', wrap).forEach(chip => {
      if (chip.classList.contains('is-locked')) return;
      chip.addEventListener('click', () => {
        const code = chip.dataset.code;
        if (!code) return;
        if (pending.has(code)) {
          pending.delete(code);
          autoHints.delete(code);
        } else {
          pending.add(code);
          autoHints.delete(code);
        }
        renderMaterialChips(categories, locked, pending, autoHints);
      });
    });
  }

  function renderProcessChips(processes, lockedSet) {
    const wrap = $('#process-list');
    if (!wrap) return;
    wrap.innerHTML = processes.map(p => {
      const locked = lockedSet.has(p.process_code);
      const label = `${p.process_name_ko || p.process_code}${locked ? ' ✓' : ''}`;
      return `<button type="button" class="ob-chip${locked ? ' is-locked' : ''}" data-code="${h(p.process_code)}" ${locked ? 'disabled' : ''}>${h(label)}</button>`;
    }).join('');
    $$('.ob-chip', wrap).forEach(chip => {
      if (chip.classList.contains('is-locked')) return;
      chip.addEventListener('click', () => chip.classList.toggle('selected'));
    });
  }

  function computeMaterialCount(materialsArr) {
    // company_material_capabilities 는 specific_material + material_category 양쪽 영역.
    // _check_onboarding 은 count(*) > 0 검사이므로 어떤 scope_type 이든 1 행 이상이면 충족.
    return (materialsArr || []).length;
  }

  function computeOnboardingDetail(companyDetail) {
    const site = companyDetail.site || {};
    return {
      status: companyDetail.onboarding_status || 'draft',
      equipmentCount: (companyDetail.equipment || []).length,
      materialCount: computeMaterialCount(companyDetail.materials),
      hasBrn: Boolean(companyDetail.business_registration_no),
      hasRegion: Boolean(site.region),
    };
  }

  async function initSupplierSettings() {
    const user = await window.imma.requireRole('supplier');
    window.imma.renderSessionHeader();
    bindLogout();
    text($('.mw-side-user strong'), user.company_name || user.name || '공급사');

    // 기본 회사 정보 채움
    const basicName = $('#basic-company-name');
    const basicContact = $('#basic-contact-name');
    const basicPhone = $('#basic-phone');
    const basicEmail = $('#basic-email');
    if (basicName) basicName.value = user.company_name || '';
    if (basicContact) basicContact.value = user.contact_name || user.name || '';
    if (basicPhone) basicPhone.value = user.phone || '';
    if (basicEmail) basicEmail.value = user.email || '';

    // 캐시: 카탈로그 + 현재 상태
    let materialCategories = [];
    let processCatalog = [];
    let companyDetail = null;
    // 자동 매핑된 공정 코드 (장비 등록 시 자동 생성) — 비활성 처리 대상
    const autoLockedProcesses = new Set();
    // 재질 chip 선택 상태 source of truth — DOM innerHTML 재구성에도 보존
    let lockedMaterialCodes = new Set();         // DB 저장 완료 (✓ disabled)
    const pendingMaterialCodes = new Set();      // 저장 전 사용자 선택 또는 장비 자동 추천
    const autoHintMaterialCodes = new Set();     // pending 중 *(자동)* 라벨 표시 대상

    async function refreshCompany() {
      try {
        companyDetail = await window.imma.apiJson(`/api/company/${encodeURIComponent(user.id)}`);
      } catch (err) {
        window.imma.toast(`업체 정보 불러오기 실패: ${err.message}`, 'error');
        return;
      }
      renderEquipmentList(companyDetail.equipment || []);
      // 재질 — DB 저장 완료 카테고리는 lockedMaterialCodes 갱신.
      // pending/autoHint 영역에서 locked 중복 제거 (저장 완료된 영역은 더 이상 pending 부재).
      lockedMaterialCodes = new Set(
        (companyDetail.materials || [])
          .filter(m => m.scope_type === 'material_category' && m.material_category_code)
          .map(m => m.material_category_code)
      );
      lockedMaterialCodes.forEach(code => {
        pendingMaterialCodes.delete(code);
        autoHintMaterialCodes.delete(code);
      });
      renderMaterialChips(materialCategories, lockedMaterialCodes, pendingMaterialCodes, autoHintMaterialCodes);

      // 현재 등록된 공정 — auto_generated 영역 추정: equipment_process_capabilities 와 일치하는 공정
      (companyDetail.processes || []).forEach(p => autoLockedProcesses.add(p.process_code));
      renderProcessChips(processCatalog, autoLockedProcesses);
      const procHint = $('#process-current-hint');
      if (procHint) {
        const procCount = (companyDetail.processes || []).length;
        procHint.textContent = procCount > 0
          ? `현재 ${procCount}개 공정이 등록되어 있습니다 (장비 자동 매핑 + 추가 등록 합산). ✓ 표시는 이미 등록되어 비활성화된 공정입니다.`
          : '아직 등록된 공정이 없습니다. 장비를 등록하면 공정이 자동 추가되며, 외주/수작업 공정만 여기서 추가합니다.';
      }

      // 사업자 정보 채움
      const brn = $('#brn');
      const region = $('#region');
      const city = $('#city');
      const address = $('#address');
      const rep = $('#representative-name');
      const postal = $('#postal-code');
      const site = companyDetail.site || {};
      if (brn) brn.value = companyDetail.business_registration_no || '';
      if (region) region.value = site.region || '';
      if (city) city.value = site.city || '';
      if (address) address.value = site.address_line1 || '';
      if (rep) rep.value = companyDetail.representative_name || '';
      if (postal) postal.value = site.postal_code || '';

      // 기본 회사 정보 보강 — /api/me 응답에 phone/email 부재이므로 /api/company/{id} 응답으로 채움
      const primaryContact = (companyDetail.contacts || []).find(c => c.is_primary) || (companyDetail.contacts || [])[0];
      if (basicPhone && !basicPhone.value) basicPhone.value = companyDetail.main_phone || (primaryContact && primaryContact.phone) || '';
      if (basicEmail && !basicEmail.value) basicEmail.value = companyDetail.main_email || (primaryContact && primaryContact.email) || '';
      if (basicContact && !basicContact.value && primaryContact) basicContact.value = primaryContact.contact_name || '';

      applyOnboardingStatus(computeOnboardingDetail(companyDetail));
    }

    // 카탈로그 6 종 fetch (선택지 API). 인증 불필요이나 토큰 있어도 무방.
    try {
      const [equipCats, matCats, procs] = await Promise.all([
        window.imma.apiJson('/api/equipment-categories'),
        window.imma.apiJson('/api/material-categories'),
        window.imma.apiJson('/api/processes'),
      ]);
      const equipSelect = $('#equip-category');
      if (equipSelect) {
        equipCats.forEach(c => {
          const opt = document.createElement('option');
          opt.value = c.equipment_category_code;
          opt.textContent = c.category_name_ko;
          equipSelect.appendChild(opt);
        });
      }
      // material/process catalog 캐시
      materialCategories = matCats.filter(c => c.category_code !== 'other');
      processCatalog = procs;
    } catch (err) {
      window.imma.toast(`카탈로그 로드 실패: ${err.message}`, 'error');
    }

    await refreshCompany();

    // URL hash #onboarding 스크롤
    if (window.location.hash === '#onboarding') {
      const target = $('#onboarding');
      if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    // 장비 카테고리 변경 → 모델 fetch
    const equipCategorySel = $('#equip-category');
    const equipModelSel = $('#equip-model');
    if (equipCategorySel && equipModelSel) {
      equipCategorySel.addEventListener('change', async () => {
        equipModelSel.innerHTML = '<option value="">모델 (선택)</option>';
        const cat = equipCategorySel.value;
        if (!cat) return;
        try {
          const models = await window.imma.apiJson(`/api/equipment-models?category=${encodeURIComponent(cat)}`);
          models.forEach(m => {
            const opt = document.createElement('option');
            opt.value = m.model_id;
            opt.textContent = `${m.manufacturer || ''} ${m.model_name || ''}`.trim() || m.model_id;
            equipModelSel.appendChild(opt);
          });
        } catch (err) {
          window.imma.toast(`모델 목록 로드 실패: ${err.message}`, 'error');
        }
      });
    }

    // 장비 추가 버튼
    const equipAddBtn = $('#equip-add-btn');
    if (equipAddBtn && equipAddBtn.dataset.immaHooked !== 'true') {
      equipAddBtn.dataset.immaHooked = 'true';
      equipAddBtn.addEventListener('click', async () => {
        const categoryCode = equipCategorySel ? equipCategorySel.value : '';
        const modelId = equipModelSel ? equipModelSel.value : '';
        const displayName = ($('#equip-display-name') || {}).value || '';
        if (!categoryCode || !displayName.trim()) {
          window.imma.toast('장비 카테고리와 명칭은 필수입니다.', 'warning');
          return;
        }
        window.imma.setLoading(equipAddBtn, true, '등록 중...');
        try {
          const result = await window.imma.apiJson('/api/equipment', {
            method: 'POST',
            body: {
              company_id: user.id,
              equipment_category_code: categoryCode,
              model_id: modelId || null,
              display_name: displayName.trim(),
            },
          });
          // 자동 매핑된 공정 코드를 잠금 set 에 추가
          (result.auto_generated_processes || []).forEach(pc => autoLockedProcesses.add(pc));
          // 추정 재질 자동 추천 — DOM 직접 조작 폐기. Set 영역에 기록 후 즉시 renderMaterialChips 재호출.
          // 직후 await refreshCompany() 호출이 와도 pendingMaterialCodes 가 Set 영역에 보존되어 selected 시각 유지.
          const hints = EQUIPMENT_TO_MATERIAL_HINT[categoryCode] || [];
          let hintAdded = 0;
          hints.forEach(code => {
            if (!lockedMaterialCodes.has(code) && !pendingMaterialCodes.has(code)) {
              pendingMaterialCodes.add(code);
              autoHintMaterialCodes.add(code);
              hintAdded += 1;
            }
          });
          if (hintAdded > 0) {
            renderMaterialChips(materialCategories, lockedMaterialCodes, pendingMaterialCodes, autoHintMaterialCodes);
          }
          const procCount = (result.auto_generated_processes || []).length;
          const msgParts = [`장비 등록됨`];
          if (procCount > 0) msgParts.push(`공정 ${procCount}종 자동 매핑`);
          if (hintAdded > 0) msgParts.push(`추정 재질 ${hintAdded}종 자동 추천`);
          window.imma.toast(msgParts.join(' · '), 'success');
          // 입력 초기화
          if (equipModelSel) equipModelSel.innerHTML = '<option value="">모델 (선택)</option>';
          if (equipCategorySel) equipCategorySel.value = '';
          const dnEl = $('#equip-display-name');
          if (dnEl) dnEl.value = '';
          await refreshCompany();
        } catch (err) {
          window.imma.toast(err.message, 'error');
        } finally {
          window.imma.setLoading(equipAddBtn, false);
        }
      });
    }

    // 재질 저장 버튼
    const materialSaveBtn = $('#material-save-btn');
    if (materialSaveBtn && materialSaveBtn.dataset.immaHooked !== 'true') {
      materialSaveBtn.dataset.immaHooked = 'true';
      materialSaveBtn.addEventListener('click', async () => {
        // pendingMaterialCodes Set + DOM .selected 양면 수집 — refresh 도중 누락 영역 방어.
        const domSelected = $$('#material-categories .ob-chip.selected:not(.is-locked)')
          .map(c => c.dataset.code)
          .filter(Boolean);
        const selectedCodes = Array.from(new Set([...pendingMaterialCodes, ...domSelected]))
          .filter(code => code && !lockedMaterialCodes.has(code));
        if (selectedCodes.length === 0) {
          window.imma.toast('추가할 재질 카테고리를 1개 이상 선택해주세요.', 'warning');
          return;
        }
        window.imma.setLoading(materialSaveBtn, true, '저장 중...');
        try {
          const result = await window.imma.apiJson('/api/material-capability', {
            method: 'POST',
            body: { company_id: user.id, categories: selectedCodes },
          });
          await refreshCompany();
          // POST 후 lockedMaterialCodes 가 갱신된 상태 — 미반영 여부 점검
          const notPersisted = selectedCodes.filter(code => !lockedMaterialCodes.has(code));
          if (notPersisted.length > 0) {
            window.imma.toast(
              `재질 ${selectedCodes.length - notPersisted.length}/${selectedCodes.length}종만 서버 반영됨 — 미반영 항목은 선택 상태 유지`,
              'warning'
            );
          } else {
            window.imma.toast(`재질 ${selectedCodes.length}종 추가 (status: ${result.onboarding_status})`, 'success');
          }
          if (result.onboarding_status === 'verified') {
            window.imma.toast('온보딩 완료 — 매칭 노출이 가능합니다.', 'success');
          }
        } catch (err) {
          window.imma.toast(err.message, 'error');
        } finally {
          window.imma.setLoading(materialSaveBtn, false);
        }
      });
    }

    // 추가 공정 저장 버튼
    const processSaveBtn = $('#process-save-btn');
    if (processSaveBtn && processSaveBtn.dataset.immaHooked !== 'true') {
      processSaveBtn.dataset.immaHooked = 'true';
      processSaveBtn.addEventListener('click', async () => {
        const selected = $$('#process-list .ob-chip.selected:not(.is-locked)').map(c => c.dataset.code);
        if (selected.length === 0) {
          window.imma.toast('추가할 공정을 1개 이상 선택해주세요.', 'warning');
          return;
        }
        const serviceMode = ($('#process-service-mode') || {}).value || 'in_house';
        window.imma.setLoading(processSaveBtn, true, '등록 중...');
        try {
          const result = await window.imma.apiJson('/api/process-capability', {
            method: 'POST',
            body: {
              company_id: user.id,
              processes: selected.map(pc => ({ process_code: pc, service_mode: serviceMode })),
            },
          });
          window.imma.toast(`공정 ${selected.length}종 추가 (status: ${result.onboarding_status})`, 'success');
          await refreshCompany();
        } catch (err) {
          window.imma.toast(err.message, 'error');
        } finally {
          window.imma.setLoading(processSaveBtn, false);
        }
      });
    }

    // 사업자 정보 저장 버튼
    const businessSaveBtn = $('#business-save-btn');
    if (businessSaveBtn && businessSaveBtn.dataset.immaHooked !== 'true') {
      businessSaveBtn.dataset.immaHooked = 'true';
      businessSaveBtn.addEventListener('click', async () => {
        const brnVal = ($('#brn') || {}).value || '';
        const regionVal = ($('#region') || {}).value || '';
        const cityVal = ($('#city') || {}).value || '';
        const addressVal = ($('#address') || {}).value || '';
        const repVal = ($('#representative-name') || {}).value || '';
        const postalVal = ($('#postal-code') || {}).value || '';
        const basicContactVal = ($('#basic-contact-name') || {}).value || '';
        const basicPhoneVal = ($('#basic-phone') || {}).value || '';
        const basicEmailVal = ($('#basic-email') || {}).value || '';

        const payload = { company_id: user.id };
        if (brnVal.trim()) payload.business_registration_no = brnVal.trim();
        if (regionVal) payload.region = regionVal;
        if (cityVal.trim()) payload.city = cityVal.trim();
        if (addressVal.trim()) payload.address = addressVal.trim();
        if (repVal.trim()) payload.representative_name = repVal.trim();
        if (postalVal.trim()) payload.postal_code = postalVal.trim();
        if (basicPhoneVal.trim()) payload.main_phone = basicPhoneVal.trim();
        // 담당자 contact 영역 (UPSERT) — 가입 시 입력한 담당자 이름을 primary contact 로 사용
        if (basicContactVal.trim()) {
          payload.contact_name = basicContactVal.trim();
          payload.role_title = '대표 담당자';
          payload.contact_phone = basicPhoneVal.trim() || null;
          payload.contact_email = basicEmailVal.trim() || null;
        }

        window.imma.setLoading(businessSaveBtn, true, '저장 중...');
        try {
          const result = await window.imma.apiJson('/api/company/profile', { method: 'PUT', body: payload });
          await refreshCompany();
          if (result.onboarding_status === 'verified') {
            window.imma.toast('온보딩 완료 — 매칭 노출이 가능합니다.', 'success');
          } else {
            window.imma.toast(`저장됨 (status: ${result.onboarding_status})`, 'success');
          }
        } catch (err) {
          window.imma.toast(err.message, 'error');
        } finally {
          window.imma.setLoading(businessSaveBtn, false);
        }
      });
    }
  }

  async function initSupplierRfqDetail() {
    const user = await window.imma.requireRole('supplier');
    window.imma.renderSessionHeader();
    text($('.u-name'), user.company_name || user.name || '공급사');
    const rfqId = window.imma.getQueryParam('rfq_id');
    if (!rfqId) return;
    try {
      const rfq = await window.imma.apiJson(`/api/rfq/${encodeURIComponent(rfqId)}`);
      const metaSpans = $$('.rfq-title-row > span');
      text(metaSpans[1], rfq.rfq_no || `RFQ-${shortId(rfq.rfq_id)}`);
      text(metaSpans[3], `요청일 ${rfq.created_at ? rfq.created_at.slice(0, 10) : '-'}`);
      const part = firstPart(rfq);
      const vals = $$('.spec-table .spec-val');
      text(vals[0], part.part_name || '-');
      text(vals[1], processText(part));
      text(vals[2], partMaterial(part));
      // surface_treatment는 실제 /api/rfq 응답에 없으므로 기존 demo 값을 유지한다.
      text(vals[4], part.quantity ? `${part.quantity} EA` : '-');
      text(vals[5], partTolerance(part));
      text(vals[6], rfq.requested_delivery_date || '-');
    } catch (err) {
      window.imma.toast(err.message, 'error');
    }
  }

  async function initAdminDashboard() {
    await window.imma.requireAdmin();
    window.imma.renderSessionHeader();
    bindLogout();
    try {
      const pending = await window.imma.apiJson('/api/admin/companies/pending');
      const btn = $('a[href="/admin-control-center"]');
      if (btn && !btn.querySelector('.imma-admin-pending-badge') && pending.length) {
        btn.innerHTML += ` <span class="badge badge-yellow imma-admin-pending-badge" style="margin-left:6px;">${h(pending.length)} pending</span>`;
      }
    } catch (err) {
      window.imma.toast(err.message, 'error');
    }
  }

  async function initAdminControlCenter() {
    await window.imma.requireAdmin();
    window.imma.renderSessionHeader();
    bindLogout();
    try {
      const pending = await window.imma.apiJson('/api/admin/companies/pending');
      const databaseCard = $('#database');
      if (!databaseCard || databaseCard.querySelector('.imma-pending-companies')) return;
      const area = document.createElement('div');
      area.className = 'mw-list mt-16 imma-pending-companies';
      area.innerHTML = pending.length ? pending.map(c => `
        <div class="mw-list-item soft">
          <div>
            <div class="mw-list-title">${h(c.company_name)}</div>
            <div class="mw-list-meta">${h(c.main_email || c.email || '-')} · ${h(c.region || '-')} · ${h(c.onboarding_status || 'pending')}</div>
          </div>
          <div class="mw-row-actions">
            <button class="btn-primary verify-company" data-id="${h(c.company_id)}" style="padding:7px 10px;font-size:12px;">승인</button>
            <button class="btn-outline reject-company" data-id="${h(c.company_id)}" style="padding:7px 10px;font-size:12px;">반려</button>
          </div>
        </div>`).join('') : '<div class="mw-list-item soft"><div><div class="mw-list-title">검수 대기 업체 없음</div><div class="mw-list-meta">신규 supplier 가입 후 여기에 표시됩니다.</div></div></div>';
      databaseCard.appendChild(area);
      $$('.verify-company', area).forEach(btn => btn.addEventListener('click', async () => {
        window.imma.setLoading(btn, true, '승인 중...');
        try {
          await window.imma.apiJson(`/api/admin/companies/${encodeURIComponent(btn.dataset.id)}/verify`, { method: 'PUT' });
          window.imma.toast('승인되었습니다.', 'success');
          btn.closest('.mw-list-item').remove();
        } catch (err) {
          window.imma.toast(err.message, 'error');
        } finally {
          window.imma.setLoading(btn, false);
        }
      }));
      $$('.reject-company', area).forEach(btn => btn.addEventListener('click', async () => {
        const reason = window.prompt('반려 사유', '자료 보완 필요') || '';
        window.imma.setLoading(btn, true, '반려 중...');
        try {
          await window.imma.apiJson(`/api/admin/companies/${encodeURIComponent(btn.dataset.id)}/reject`, { method: 'PUT', body: { reason } });
          window.imma.toast('반려되었습니다.', 'success');
          btn.closest('.mw-list-item').remove();
        } catch (err) {
          window.imma.toast(err.message, 'error');
        } finally {
          window.imma.setLoading(btn, false);
        }
      }));
    } catch (err) {
      window.imma.toast(err.message, 'error');
    }
  }

  async function initClientFulfillment() {
    await window.imma.requireRole('buyer');
    window.imma.renderSessionHeader();
  }

  async function initPaymentSuccess() {
    await window.imma.requireRole('buyer');
    window.imma.renderSessionHeader();
  }

  async function initSupplierMessages() {
    await window.imma.requireRole('supplier');
    window.imma.renderSessionHeader();
    bindLogout();
  }

  async function initAdminOperations() {
    await window.imma.requireAdmin();
    window.imma.renderSessionHeader();
    bindLogout();
  }

  function initSearchSuppliers() {
    window.imma.renderSessionHeader();
    $$('[data-demo-action="supplier-detail"]').forEach(card => {
      if (card.dataset.immaHooked === 'true') return;
      card.dataset.immaHooked = 'true';
      card.style.cursor = 'pointer';
      card.addEventListener('click', () => {
        const name = (card.querySelector('.company-name, .sup-title, h3, h4') || {}).textContent || '업체';
        window.imma.toast(`${name.trim()} 상세는 시연 다음 단계에 제공됩니다.`, 'info');
      });
    });
  }

  function initSupport() {
    window.imma.renderSessionHeader();
    $$('[data-demo-action="support-ticket"]').forEach(card => {
      if (card.dataset.immaHooked === 'true') return;
      card.dataset.immaHooked = 'true';
      card.style.cursor = 'pointer';
      card.addEventListener('click', () => window.imma.toast('1:1 문의는 시연 다음 단계에 제공됩니다.', 'info'));
    });
  }

  function initHowToUse() {
    window.imma.renderSessionHeader();
  }

  function initProcessFlow() {
    window.imma.renderSessionHeader();
  }

  async function route() {
    const path = window.location.pathname.replace(/\/$/, '') || '/';
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
    if (path === '/supplier-messages') return initSupplierMessages();
    if (path === '/admin-ui') return initAdminDashboard();
    if (path === '/admin-control-center') return initAdminControlCenter();
    if (path === '/admin-operations') return initAdminOperations();
    if (path === '/client-fulfillment') return initClientFulfillment();
    if (path === '/payment-success') return initPaymentSuccess();
    if (path === '/search-suppliers') return initSearchSuppliers();
    if (path === '/support') return initSupport();
    if (path === '/how-to-use') return initHowToUse();
    if (path === '/process-flow') return initProcessFlow();
    window.imma.renderSessionHeader();
  }

  document.addEventListener('DOMContentLoaded', () => {
    Promise.resolve(route()).catch(err => {
      console.error(err);
      if (window.imma && window.imma.toast) window.imma.toast(err.message || '페이지 초기화 실패', 'error');
    });
  });
})();
