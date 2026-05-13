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
    const dueDate = $('#reply input[type="date"]') && $('#reply input[type="date"]').value;
    const amountInput = $('#reply input:not([type]), #reply input[type="text"], #reply .mw-input:not([type])');
    const amount = numberOnly(amountInput && amountInput.value) || 6250000;
    const note = $('#reply textarea') ? $('#reply textarea').value : 'Phase 1 견적';
    const rfqPartId = match.rfq_part && match.rfq_part.rfq_part_id;
    const quantity = match.rfq_part && match.rfq_part.quantity ? Number(match.rfq_part.quantity) : 1;
    return {
      rfq_id: match.rfq_id,
      company_id: user.id,
      total_price: amount,
      estimated_lead_days: 7,
      proposed_delivery_date: dueDate || null,
      assumptions: note || 'Phase 1 견적',
      line_items: [{
        rfq_part_id: rfqPartId || null,
        process_code: match.processes || null,
        description: match.part_name || 'Phase 1 견적',
        quantity,
        unit_price: quantity > 0 ? Math.round(amount / quantity) : amount,
        line_total: amount,
        notes: note || null,
      }],
    };
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

  async function initClientRegister() {
    window.imma.renderSessionHeader();
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
        window.imma.toast('가입되었습니다.', 'success');
        window.location.href = '/supplier';
      } catch (err) {
        window.imma.toast(err.message, 'error');
      } finally {
        window.imma.setLoading(btn, false);
      }
    });
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
    } catch (err) {
      window.imma.toast(err.message, 'error');
    }
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

      // pipeline_runner.py 가 우선 lookup 하는 client_notes 키로 통일 전달.
      // 공정·공차·envelope·GDT 는 도면 분석이 단일 원천이므로 parts 명시 전달은 수행하지 않는다.
      const payload = {
        drawing_id: drawingId,
        order_quantity: orderQuantity,
        requested_delivery_date: dueDate,
        budget_amount: budgetAmount,
        budget_currency: 'KRW',
        client_notes: {
          material: materialInput,
          delivery_region: regionSelect && regionSelect.value ? regionSelect.value : null,
          certifications: certInput && certInput.value ? certInput.value : null,
          post_treatment_request: postTreatmentRequest,
          notes: noteInput && noteInput.value ? noteInput.value : null,
          vlm_fallback_used: Boolean(scopedGet([drawingId, 'vlm_fallback_used'], false)),
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

  async function initMatching() {
    await window.imma.requireRole('buyer');
    window.imma.renderSessionHeader();
    const rfqId = window.imma.getQueryParam('rfq_id') || scopedGet(['current_rfq_id']);
    if (!rfqId) return;

    try {
      const rfq = await window.imma.apiJson(`/api/rfq/${encodeURIComponent(rfqId)}`);
      const part = firstPart(rfq);
      const values = $$('.rfq-summary-card .rfq-value');
      text(values[0], rfq.rfq_no || shortId(rfq.rfq_id));
      text(values[1], part.part_name || '도면 기반 부품');
      text(values[2], processText(part));
      text(values[3], part.quantity || rfq.order_quantity || '-');
      text(values[4], rfq.created_at ? rfq.created_at.slice(0, 10) : '-');
    } catch (err) {
      console.warn('RFQ summary 조회 실패', err);
    }

    const result = scopedGet([rfqId, 'match_result']);
    const candidates = getMatchCandidates(result).slice(0, 3);
    const rows = $$('.supplier-row');
    rows.forEach((row, index) => {
      const item = candidates[index];
      if (!item) return;
      const cand = item.cand;
      const h4 = row.querySelector('.s-info h4');
      const badge = h4 && h4.querySelector('.ai-badge');
      if (h4) {
        h4.textContent = `${cand.company_name || '업체명 없음'} `;
        if (badge) {
          badge.textContent = `AI ${candidateScore(cand)}`;
          h4.appendChild(badge);
        }
      }
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
          window.imma.toast(`${payload.company_name || '후보'}를 표시했습니다.`, 'success');
        });
      }
      const checkbox = row.querySelector('.s-checkbox input');
      if (checkbox) {
        checkbox.addEventListener('change', () => {
          if (checkbox.checked) scopedSet([rfqId, 'selected_candidate'], payload);
        });
      }
    });

    const proceedBtn = $('.compare-box a[href="/order-management"]');
    if (proceedBtn) proceedBtn.href = `/order-management?rfq_id=${encodeURIComponent(rfqId)}`;
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

    if (rfqId && user.role === 'buyer') {
      try {
        const data = await window.imma.apiJson(`/api/rfq/${encodeURIComponent(rfqId)}/quotes`);
        const quotes = data.quotes || [];
        if (!quotes.length || $('.imma-quote-action')) return;
        const quote = quotes[0];
        const box = document.createElement('div');
        box.className = 'card imma-quote-action';
        box.style.marginBottom = '16px';
        box.innerHTML = `
          <div class="flex-between" style="gap:12px;flex-wrap:wrap;">
            <div><strong>실제 견적 ${h(data.count)}건 수신</strong><div style="font-size:12px;color:var(--text-muted);margin-top:4px;">최저 견적: ${h(quote.company_name)} · ${h(window.imma.formatCurrency(quote.total_price, 'KRW'))}</div></div>
            <button type="button" class="btn-primary" style="font-size:13px;">이 견적으로 발주 생성</button>
          </div>`;
        const pageWrap = $('.page-wrap') || document.body;
        pageWrap.insertBefore(box, $('.order-meta') || pageWrap.firstChild);
        box.querySelector('button').addEventListener('click', async () => {
          const btn = box.querySelector('button');
          window.imma.setLoading(btn, true, '발주 생성 중...');
          try {
            const order = await window.imma.apiJson('/api/orders', { method: 'POST', body: { quote_id: quote.quote_id } });
            scopedSet(['current_order_id'], order.order_id);
            window.location.href = `/order-management?order_id=${encodeURIComponent(order.order_id)}`;
          } catch (err) {
            window.imma.toast(err.message, 'error');
          } finally {
            window.imma.setLoading(btn, false);
          }
        });
      } catch (err) {
        window.imma.toast(err.message, 'error');
      }
    }
  }

  function bindSupplierLogout() {
    $$('.logout-btn').forEach(btn => {
      if (btn.dataset.immaHooked === 'true') return;
      btn.dataset.immaHooked = 'true';
      btn.addEventListener('click', () => window.imma.logout('manual'));
    });
  }

  async function initSupplierDashboard() {
    const user = await window.imma.requireRole('supplier');
    window.imma.renderSessionHeader();
    bindSupplierLogout();
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
    bindSupplierLogout();
    bindWorkbenchScrollSpy();
    text($('.mw-side-user strong'), user.company_name || user.name || '공급사');

    let currentMatches = [];
    async function respond(matchRunId, response) {
      try {
        await window.imma.apiJson(`/api/match-candidates/${encodeURIComponent(matchRunId)}/${encodeURIComponent(user.id)}/respond`, { method: 'PUT', body: { response } });
        window.imma.toast('응답이 저장되었습니다.', 'success');
        await refresh();
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
          window.imma.setLoading(sendBtn, true, '발송 중...');
          try {
            if (match.supplier_response !== 'accepted') {
              await window.imma.apiJson(`/api/match-candidates/${encodeURIComponent(match.match_run_id)}/${encodeURIComponent(user.id)}/respond`, { method: 'PUT', body: { response: 'accepted' } });
            }
            await window.imma.apiJson('/api/quote', { method: 'POST', body: quotePayloadFromWorkbench(match, user) });
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
  }

  async function initSupplierSettings() {
    const user = await window.imma.requireRole('supplier');
    window.imma.renderSessionHeader();
    bindSupplierLogout();
    text($('.mw-side-user strong'), user.company_name || user.name || '공급사');
    const inputs = $$('#capability .mw-input');
    if (inputs[0] && user.company_name) inputs[0].value = user.company_name;
    if (inputs[1] && (user.name || user.contact_name)) inputs[1].value = user.name || user.contact_name;
    if (inputs[2] && user.phone) inputs[2].value = user.phone;
    if (inputs[3] && user.email) inputs[3].value = user.email;
    const saveBtn = $('.mw-top .btn-primary');
    if (saveBtn && saveBtn.dataset.immaHooked !== 'true') {
      saveBtn.dataset.immaHooked = 'true';
      saveBtn.addEventListener('click', async () => {
        window.imma.setLoading(saveBtn, true, '저장 중...');
        try {
          await window.imma.apiJson('/api/company/profile', { method: 'PUT', body: {
            company_id: user.id,
            company_name: inputs[0] ? inputs[0].value : user.company_name,
            main_phone: inputs[2] ? inputs[2].value : null,
            main_email: inputs[3] ? inputs[3].value : null,
            address: inputs[4] ? inputs[4].value : null,
          }});
          window.imma.toast('저장되었습니다.', 'success');
        } catch (err) {
          window.imma.toast(err.message, 'error');
        } finally {
          window.imma.setLoading(saveBtn, false);
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
    bindSupplierLogout();
  }

  async function initAdminOperations() {
    await window.imma.requireAdmin();
    window.imma.renderSessionHeader();
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
