# IMMA UI ↔ 백엔드 연결 계획서 v3

작성 기준은 `fas_analysis.zip`의 현재 소스와 1라운드 검증 결과, 그리고 확정된 P0/P1/P2·결정점이다. 이 문서는 Phase 1 발표 시연을 바로 구현할 수 있도록 UI 연결 대상, 백엔드 소규모 보강, 데이터 흐름, 검증 기준을 하나의 실행 명세로 정리한다.

---

## 1. 컨텍스트와 범위

### 1.1 프로젝트 정체

IMMA는 제조업 발주자가 도면을 업로드하면 AI 분석 결과를 기반으로 가공 가능 업체를 자동 매칭하고, 공급사가 매칭 요청을 수락한 뒤 견적을 제출하며, 발주자가 견적을 선택해 주문으로 전환하는 플랫폼이다. 현재 백엔드는 FastAPI, PostgreSQL, Neo4j, Replicate VLM, GraphRAG 기반 매칭 파이프라인을 갖추고 있다. UI는 `machhub_ui` 정적 HTML/CSS/JS로 구성되어 있고, 지금은 발표용 시뮬레이션 레이어가 실제 API 흐름 위에 덮여 있다.

### 1.2 Phase 1 목표

Phase 1의 목표는 5/18 발표에서 buyer, supplier, admin 3개 역할이 같은 IMMA 백엔드를 바라보는 것을 보여주는 것이다. 핵심 시연 흐름은 다음 순서를 따른다.

1. buyer 로그인 또는 회원가입
2. buyer 도면 업로드 또는 시연용 fixture 분석 결과 사용
3. `/api/match-v2`로 RFQ 생성과 업체 매칭 실행
4. buyer가 매칭 결과를 확인하고 후보를 UI에서 선택 표시
5. supplier가 `/api/company/matches`에서 매칭 요청을 확인하고 수락
6. supplier가 `/api/quote`로 견적 제출
7. buyer가 `/api/rfq/{rfq_id}/quotes`로 견적을 비교하고 `/api/orders`로 발주
8. supplier가 `/api/notifications?unread_only=false`에서 `order_confirmed` 알림을 보고 주문 상세로 진입
9. buyer와 supplier가 `/api/orders/{order_id}/status`로 상태 전이를 시연
10. admin이 신규 supplier의 pending 목록을 보고 승인 또는 반려

### 1.3 Phase 1에서 남기는 시뮬레이션

Phase 1에서는 결제, 메시징, supplier 검색 정밀 필터, admin RFQ/Order/KPI 관제 전체, 실시간 WebSocket/SSE를 실 API로 구현하지 않는다. 이 영역은 UI에 “데모/준비 중” 상태를 명확히 표시하고, 실제 API 흐름을 방해하지 않는 정적 또는 read-only 컴포넌트로 둔다.

### 1.4 Phase 1 백엔드 변경 범위

백엔드 변경은 작은 보강으로 제한한다. 필수 변경은 다음 네 곳이다.

- `routers/matching.py`: `/api/match-v2` 응답에 `match_run_id`, 후보별 `rank_no`, 후보별 `rfq_part_id`를 넣고, `_save_match_history()` 실패 시 fail-fast 처리한다.
- `routers/signup.py`: supplier 가입에서 `name`은 담당자명으로, `company_name`은 회사명으로 분리 소비한다.
- `routers/rfqs.py`: `/rfqs` 목록 응답에 `status`를 추가한다.
- `routers/deps.py`: 배포 환경에서 `JWT_SECRET=imma-dev-secret` 기본값을 쓰면 서버가 시작되지 않게 한다.

보조 변경은 다음 두 곳이다.

- `main.py`: CORS 기본 origin에서 외부 더미 백엔드 도메인을 제거한다.
- `routers/admin.py` 또는 별도 라우터: `/api/config/health`를 추가하는 경우 admin 전용으로 두고 boolean만 반환한다. Phase 1 UI는 이 endpoint에 의존하지 않는다.

### 1.5 Phase 1 UI 변경 범위

UI 변경은 실제로 로드되는 HTML inline script와 `site-actions.js` 분리에 집중한다. `client.js`, `supplier.js`, `app.js`, `app_unified.js`, `shared-state.js`는 Phase 1 런타임에서 사용하지 않는 잔재로 취급한다. Phase 1 구현자는 이 파일들을 고쳐서 흐름을 연결하지 않는다. 제거는 Phase 2에서 한다.

---

## 2. 현 상태 진단

### 2.1 UI 파일 구성

`machhub_ui`에는 현재 파일 36개가 있다. HTML은 21개, JS는 6개, CSS는 6개, 영상 1개, 기타 정적 자원이 있다. 기존 문서의 “39개 파일” 표기는 현재 zip 기준과 맞지 않다.

HTML 21개는 다음과 같다.

| 파일 | Phase 1 역할 |
|---|---|
| `landing.html` | 로그인, 역할별 진입, public header 상태 표시 |
| `client-register.html` | buyer 가입 |
| `supplier-register.html` | supplier 가입, 자동 로그인, 온보딩 API 호출 |
| `client-dashboard.html` | buyer RFQ 목록·간단 KPI |
| `quote-request.html` | buyer 도면 업로드, VLM 진행도, match-v2 실행 |
| `matching.html` | buyer 매칭 결과 표시, 후보 선택 표시 |
| `order-management.html` | buyer 견적 비교·발주, buyer/supplier 주문 상태 전이 |
| `client-fulfillment.html` | Phase 1 read-only 데모 영역 |
| `payment-success.html` | Phase 1 결제 완료 데모 영역 |
| `supplier-dashboard.html` | supplier 매칭·알림 요약 |
| `supplier-workbench.html` | supplier 매칭 수락/거절, 견적 제출, 주문 발견 |
| `supplier-rfq-detail.html` | supplier 매칭 상세·견적 폼 보조 화면 |
| `supplier-settings.html` | supplier 프로필·역량 등록 |
| `supplier-messages.html` | Phase 1 메시징 데모 영역 |
| `search-suppliers.html` | Phase 1 검색 데모 영역 |
| `admin-dashboard.html` | admin 진입·가드, 일부 더미 KPI |
| `admin-operations.html` | pending supplier 승인/반려 실 API |
| `admin-control-center.html` | admin 설정/관제 데모 영역 |
| `how-to-use.html` | public 안내 + header 로그인 상태 |
| `process-flow.html` | public 안내 + header 로그인 상태 |
| `support.html` | public 안내 + header 로그인 상태 |

### 2.2 활성 JS 기준

현재 HTML들은 거의 모두 `/static/site-actions.js?v=20260509-demo-flow-15`를 로드한다. `client.js`, `supplier.js`, `app.js`는 실제 HTML에서 로드되지 않으므로 Phase 1 구현 대상이 아니다.

`site-actions.js`는 단순 toast 유틸이 아니라 실 페이지를 시뮬레이션으로 바꾸는 본체다. `applyScenarioDemo()`는 `/quote-request`, `/matching-ui`, `/order-management`, `/supplier-workbench`, `/admin-ui` 등 대부분의 연결 대상 페이지에 scenario 텍스트 치환과 header rewrite를 적용한다(`machhub_ui/site-actions.js:1005-1043`). `applyQuoteScenario()`는 quote submit 링크를 `/client-fulfillment#ai`로 바꾼다(`machhub_ui/site-actions.js:1290-1332`). 전역 submit handler는 모든 폼 submit을 `preventDefault()`하고 demo route로 넘긴다(`machhub_ui/site-actions.js:2067-2073`). 전역 click handler도 일반 button click을 막고 demo handler로 넘긴다(`machhub_ui/site-actions.js:2075-2102`).

따라서 Phase 1에서는 `site-actions.js`를 그대로 두고 API만 붙이면 안 된다. 먼저 demo 본체를 분리하거나 실모드에서 확실히 비활성화해야 한다.

### 2.3 백엔드 인증 상태

백엔드 인증은 HS256 JWT 기반이다. `routers/deps.py:35-39`는 `JWT_SECRET`, `HS256`, 24시간 만료, `HTTPBearer(auto_error=False)`를 정의한다. `/api/login`은 buyer와 supplier를 순차 조회해 `{access_token, token_type, user}`를 반환한다(`routers/auth.py:18-88`). `/api/me`는 JWT payload 검증 후 DB 표시 정보를 보강해 `{id, login_id, role, name?, company_name?}`을 반환한다. buyer는 `buyers.buyer_name/company_name`, supplier는 `companies.company_name`과 primary contact, admin은 `admins.name`을 사용한다(`routers/auth.py:94-101`). `/api/admin/login`은 admins 테이블을 조회하고 토큰 payload의 role을 `admin`으로 넣는다(`routers/admin.py:32-86`).

UI는 localStorage의 user만 믿지 않고 `/api/me`로 세션을 확인해야 한다. localStorage는 화면 깜빡임을 줄이는 캐시일 뿐이다.

### 2.4 백엔드 signup 상태

`/signup`은 buyer와 supplier 모두 토큰을 반환하지 않는다. 성공 시 `message`와 `user`만 반환한다(`routers/signup.py:83-94`, `113-124`). 따라서 가입 직후 UI는 `/api/login`을 다시 호출해야 한다.

현재 supplier 가입은 `data.name`을 companies.company_name에 저장한다(`routers/signup.py:51-68`). Phase 1에서는 `name`과 `company_name`을 분리한다. UI는 담당자명과 회사명을 각각 입력받고, 백엔드는 `company_name`을 회사명으로 저장하며 `name`은 `representative_name` 또는 기본 contact에 반영한다.

### 2.5 백엔드 매칭 응답 상태

`/api/match-v2`는 buyer 또는 admin만 호출할 수 있다(`routers/matching.py:190-194`). `drawing_id`가 들어오면 도면 소유권을 확인하고, `parts`가 없으면 저장된 `vlm_result_jsonb`를 GraphRAG로 변환한다(`routers/matching.py:204-241`). 파이프라인 실행 결과는 `parts[*].candidates`, `recommended_candidates`, `conditional_candidates`를 포함한다(`pipeline/response.py:173-197`). `pipeline_runner.py`는 part-level `rfq_part_id`를 `result["parts"][i]["rfq_part_id"]`에 넣는다(`pipeline/pipeline_runner.py:330-333`).

문제는 buyer-facing 후보 객체에 `match_run_id`, `rank_no`, 후보별 `rfq_part_id`가 없다는 점이다. supplier 응답 endpoint는 `/api/match-candidates/{match_run_id}/{company_id}/respond`이므로, buyer 화면이 후보 선택이나 supplier 흐름과 연결되려면 응답 보강이 필요하다.

### 2.6 백엔드 견적·주문 상태

supplier 견적 제출은 `/api/quote`다. 이 endpoint는 supplier JWT를 요구하며, 해당 supplier가 매칭을 `accepted`로 응답했을 때만 견적을 허용한다(`routers/quotes.py:20-56`). 첫 견적이 들어오면 RFQ는 `open → quoted`로 자동 전이된다(`routers/quotes.py:99-113`).

buyer 발주는 `/api/orders`다. 이 endpoint는 `quote_id`를 받아 주문을 만들고, 선택된 견적을 `accepted`, 나머지 견적을 `rejected`, RFQ를 `ordered`로 바꾼다(`routers/orders.py:35-155`). 주문 생성 직후 상태는 `contracting`이다. supplier에게는 `event_type='order_confirmed'`, `reference_type='order'`, `reference_id=order_id` 알림이 생성된다(`routers/orders.py:138-148`).

주문 목록 endpoint인 `GET /api/orders`는 없다. supplier는 `/api/notifications?unread_only=false`를 조회하고, `order_confirmed` 알림의 `reference_id`를 사용해 `GET /api/orders/{order_id}`로 들어간다(`routers/notifications.py:21-92`, `routers/orders.py:163-218`). 상태 전이는 `PUT /api/orders/{order_id}/status`이며 권한 매트릭스는 `routers/orders.py:225-253`에 있다.

### 2.7 백엔드 admin 상태

Phase 1 admin 실연결 범위는 pending supplier 승인/반려다. 필요한 endpoint는 이미 존재한다.

- `POST /api/admin/login`: 관리자 로그인(`routers/admin.py:32-86`)
- `GET /api/admin/companies/pending`: 검수 대기 업체 목록(`routers/admin.py:94-127`)
- `PUT /api/admin/companies/{company_id}/verify`: 업체 승인(`routers/admin.py:135-178`)
- `PUT /api/admin/companies/{company_id}/reject`: 업체 반려(`routers/admin.py:186-252`)

`GET /api/admin/rfqs`, `GET /api/admin/orders`도 존재하지만 Phase 1에서는 KPI와 관제 표를 더미로 유지한다. admin 실연결 범위가 넓어지면 시연 리스크가 커지므로 pending + verify/reject만 실 API로 확정한다.

---

## 3. 연결 아키텍처

### 3.1 스크립트 소유권

Phase 1의 스크립트 소유권은 다음처럼 고정한다.

| 계층 | 파일 | 역할 |
|---|---|---|
| 공용 UI 유틸 | `imma-ui-utils.js` | toast, loading overlay, modal helper, safe DOM helper |
| 인증 | `auth.js` | token 저장, user 저장, UTF-8 안전 JWT payload decode, logout, role guard, `/api/me` 검증 |
| API wrapper | `imma-api.js` | `apiJson`, `apiForm`, 표준 에러 처리, 동시 401 redirect 제어 |
| 페이지 실 API | 각 HTML inline script 또는 신규 page script | 페이지별 API 호출과 DOM 렌더링 |
| 데모 본체 | `site-actions-demo.js` | Phase 2 이전 보관용. Phase 1 실모드에서는 로드하지 않음 |

`site-actions.js`는 Phase 1 실 페이지에서 로드하지 않는다. 기존 `<script src="/static/site-actions.js?...">`는 모든 HTML에서 제거하고, 아래 순서로 대체한다.

```html
<script>
  window.__imma_realmode__ = true;
</script>
<script src="/static/imma-ui-utils.js"></script>
<script src="/static/auth.js"></script>
<script src="/static/imma-api.js"></script>
```

admin 페이지는 기존 `admin-menu.js`를 유지할 수 있지만, demo rewrite를 유발하는 `site-actions.js`는 제거한다. `admin-menu.js`가 단순 메뉴 렌더링 이상을 수행하면 실모드 guard를 추가한다.

### 3.2 demo script 분리 원칙

선호 방식은 `site-actions.js`를 아래처럼 분리하는 것이다.

- `imma-ui-utils.js`: toast, notification UI, simple modal, `escapeHtml`, `setButtonLoading`, `formatCurrency`, `formatDate`, `readQuery`, `writeScopedState` 같은 실 API에도 필요한 함수만 둔다.
- `site-actions-demo.js`: scenario 텍스트 치환, demo header rewrite, preview mode, demo localStorage, dead-link handler, global submit/click intercept, file-input 시뮬레이션을 모두 둔다.

Phase 1 실모드에서 금지되는 동작은 다음과 같다.

- 전역 `document.addEventListener('submit', ...)`에서 `preventDefault()` 후 demo route로 보내는 동작
- 전역 click handler에서 일반 button click을 막는 동작
- scenario 텍스트 치환
- header DOM rewrite
- file-input 시뮬레이션
- `/client-fulfillment#ai` 같은 demo redirect 강제
- `immaDemo*` localStorage key 생성

대안 방식으로 early return을 쓰는 경우에도 파일 최상단에서 다음 조건을 만족해야 한다.

```js
(function () {
  const REALMODE = window.__imma_realmode__ === true;

  // toast 등 공용 유틸만 먼저 window.immaUi에 등록한다.
  window.immaUi = window.immaUi || {};
  window.immaUi.toast = window.immaUi.toast || function toast(message, type = 'info') {
    // 공용 toast 구현
  };

  if (REALMODE) {
    // demo mutation, global submit/click intercept, scenario header rewrite가 절대 등록되지 않아야 한다.
    return;
  }

  // 여기 아래에만 기존 demo 본체를 둔다.
})();
```

다만 실수 가능성을 줄이려면 early return보다 파일 분리를 우선한다.

### 3.3 API base URL

Phase 1 발표 환경에서는 UI와 API가 같은 FastAPI 앱에서 제공된다. `main.py`는 `/static`으로 UI 정적 파일을 제공하고, `/client`, `/quote-request`, `/matching-ui`, `/order-management`, `/supplier-workbench`, `/admin-ui` 등 HTML 라우트를 같은 origin에서 서빙한다(`main.py:31-153`). API wrapper는 기본적으로 상대 경로를 사용한다.

```js
window.IMMA_API_BASE = window.IMMA_API_BASE || '';
```

외부 URL인 `https://fas-production-c5f2.up.railway.app`는 UI 코드, CORS 기본값, 문서에서 제거한다. `main.py:18-29`의 `_DEFAULT_ALLOWED_ORIGINS`는 다음처럼 바꾼다.

```python
_DEFAULT_ALLOWED_ORIGINS = "http://localhost:8000,http://127.0.0.1:8000"
_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", _DEFAULT_ALLOWED_ORIGINS).split(",")
    if o.strip()
]
```

발표 배포 도메인이 있으면 `ALLOWED_ORIGINS` 환경변수로 넣는다. 외부 더미 백엔드는 기본 whitelist에 두지 않는다.

### 3.4 인증 흐름

UI는 다음 키만 전역 인증 상태로 사용한다.

- `imma_token`
- `imma_user`

이 두 키는 사용자 전환 시 항상 초기화된다. 업무 상태는 user-scoped key에 저장한다.

`auth.js`는 localStorage user를 1차 캐시로 읽고, 보호 페이지에서는 `/api/me`로 2차 검증한다. JWT exp는 클라이언트에서 decode해 만료가 명백한 경우 즉시 logout한다. 서버 검증이 최종 기준이다.

### 3.5 페이지 가드 정책

| 페이지 | 요구 role | 미인증 처리 | role 불일치 처리 |
|---|---|---|---|
| public 안내 페이지 | 없음 | header만 guest 표시 | 없음 |
| `client-dashboard.html` | buyer | `/`로 이동 후 로그인 toast | role home으로 이동 |
| `quote-request.html` | buyer | `/` | role home |
| `matching.html` | buyer | `/` | role home |
| `order-management.html` | buyer 또는 supplier | `/` | role home |
| `client-fulfillment.html` | buyer | `/` | role home |
| `payment-success.html` | buyer | `/` | role home |
| `supplier-dashboard.html` | supplier | `/` | role home |
| `supplier-workbench.html` | supplier | `/` | role home |
| `supplier-rfq-detail.html` | supplier | `/` | role home |
| `supplier-settings.html` | supplier | `/` | role home |
| `supplier-messages.html` | supplier | `/` | role home |
| `admin-dashboard.html` | admin | `/` | role home |
| `admin-operations.html` | admin | `/` | role home |
| `admin-control-center.html` | admin | `/` | role home |

admin token의 payload role은 `admin`으로 통일한다. admins 테이블의 `role='superadmin'`은 DB 권한 표시용이며 UI guard는 `admin`을 사용한다.

---

## 4. Phase 1 작업 명세

### 4.1 신규 공용 파일

#### 4.1.1 `machhub_ui/imma-ui-utils.js`

이 파일에는 실 API 화면에서 안전하게 재사용할 수 있는 유틸만 둔다.

필수 export는 다음과 같다.

```js
window.immaUi = (() => {
  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function toast(message, type = 'info') {
    const safeMessage = escapeHtml(message);
    const container = document.querySelector('[data-toast-container]') || createToastContainer();
    const el = document.createElement('div');
    el.className = `imma-toast imma-toast-${type}`;
    el.innerHTML = safeMessage;
    container.appendChild(el);
    window.setTimeout(() => el.remove(), 4200);
  }

  function createToastContainer() {
    const el = document.createElement('div');
    el.setAttribute('data-toast-container', 'true');
    el.className = 'imma-toast-container';
    document.body.appendChild(el);
    return el;
  }

  function setButtonLoading(button, loading, labelWhenLoading = '처리 중...') {
    if (!button) return;
    if (loading) {
      button.dataset.prevText = button.textContent || '';
      button.disabled = true;
      button.textContent = labelWhenLoading;
    } else {
      button.disabled = false;
      button.textContent = button.dataset.prevText || button.textContent || '';
      delete button.dataset.prevText;
    }
  }

  function formatCurrency(value, currency = 'KRW') {
    if (value === null || value === undefined || value === '') return '-';
    const num = Number(value);
    if (Number.isNaN(num)) return escapeHtml(value);
    return new Intl.NumberFormat('ko-KR', { style: 'currency', currency }).format(num);
  }

  function formatDate(value) {
    if (!value) return '-';
    return String(value).slice(0, 10);
  }

  function readQuery(name) {
    return new URLSearchParams(window.location.search).get(name);
  }

  return { escapeHtml, toast, setButtonLoading, formatCurrency, formatDate, readQuery };
})();
```

#### 4.1.2 `machhub_ui/auth.js`

`auth.js`는 인증 상태 저장과 페이지 guard만 담당한다. API 호출은 `imma-api.js`에서 한다. 단, `/api/me` 검증은 guard에 포함한다.

핵심 구현은 부록 B.1을 그대로 사용한다.

#### 4.1.3 `machhub_ui/imma-api.js`

`imma-api.js`는 `fetch` wrapper다. 모든 페이지는 직접 `fetch()`를 호출하지 않고 `immaApi.apiJson()` 또는 `immaApi.apiForm()`을 쓴다. wrapper는 다음을 처리한다.

- Authorization header 자동 주입
- network error 처리
- JSON이 아닌 응답 처리
- `res.ok === false` 표준 에러 객체화
- 401 single-flight logout redirect
- 403/409/422/502/504별 toast message 생성
- FormData body일 때 Content-Type 자동 설정을 방해하지 않음

### 4.2 인증·회원가입

#### 4.2.1 로그인

`landing.html` 로그인 모달은 buyer/supplier와 admin을 구분한다.

- buyer/supplier: `POST /api/login`
- admin: `POST /api/admin/login`

로그인 성공 시 `immaAuth.saveSession(access_token, user)`를 호출하고 role home으로 이동한다.

```js
const roleHome = {
  buyer: '/client',
  supplier: '/supplier',
  admin: '/admin-ui'
};
window.location.href = roleHome[user.role] || '/';
```

로그인 실패 메시지는 backend detail을 우선 보여준다. 비밀번호 오입력, 존재하지 않는 계정, DB 설정 없음이 구분되어야 한다.

#### 4.2.2 buyer 가입

`client-register.html`은 `/signup` 호출 후 `/api/login`을 호출한다.

요청 body는 다음처럼 맞춘다.

```json
{
  "role": "buyer",
  "login_id": "kim_cheolsu",
  "password": "demo1234",
  "name": "김철수",
  "company_name": "철수정밀",
  "email": "kim@example.com",
  "phone": "010-0000-0000"
}
```

가입 성공 응답에는 token이 없으므로 즉시 자동 로그인한다.

#### 4.2.3 supplier 가입

`supplier-register.html`은 담당자명과 회사명을 분리한다.

요청 body는 다음처럼 맞춘다.

```json
{
  "role": "supplier",
  "login_id": "new_supplier_01",
  "password": "demo1234",
  "name": "박영업",
  "company_name": "신규정밀",
  "email": "sales@example.com",
  "phone": "010-1111-2222"
}
```

백엔드는 `routers/signup.py:51-68`을 수정해 `company_name`을 companies.company_name에 저장하고, `name`을 representative_name 또는 primary contact에 반영한다. 가입 후 UI는 `/api/login`으로 자동 로그인한다. 그 다음 후속 온보딩 API를 인증 상태에서 호출한다.

후속 API는 모두 supplier JWT가 필요하다.

- `PUT /api/company/profile`
- `POST /api/material-capability`
- `POST /api/process-capability`
- `POST /api/equipment`
- 필요 시 `PUT /api/company/availability`

`company_id`는 로그인한 `user.id`를 사용한다. 다른 company_id를 보내면 백엔드가 403을 반환한다(`routers/companies.py:412-424`, `536-555`, `661-679`, `720-734`).

#### 4.2.4 admin 로그인

admin 계정은 `admin/test1234`를 사용한다. `setup_db.py:1053-1068`에 PBKDF2 기반 seed가 이미 있으므로 bcrypt seed 코드를 추가하지 않는다. UI는 `POST /api/admin/login`만 호출한다.

### 4.3 5단계 buyer 매칭 흐름

#### 4.3.1 `quote-request.html`: 도면 업로드와 요청 정보 입력

`quote-request.html`은 buyer guard가 걸린다. 페이지 초기화 시 기존 demo submit과 링크 이동 로직은 제거한다. 사용자가 이미지를 선택하면 실제 파일명을 표시하고, 제출 시 다음 순서로 진행한다.

1. 필수 입력 검증: 파일, 수량, 희망 납기, 예산 선택 또는 비워두기 허용 여부
2. VLM 진행도 UI 시작
3. `POST /vlm/analyze-upload` 호출
4. 성공 시 `drawing_id`, `vlm_result_jsonb`, `drawing_no` 수신
5. `POST /api/match-v2` 호출
6. 응답을 user-scoped localStorage에 저장
7. `/matching-ui?rfq_id={rfq_id}`로 이동

현재 `routers/vlm.py`는 `APIRouter(prefix="/vlm")`와 `@router.post("/analyze-upload")`를 함께 사용한다. 따라서 최종 호출 경로는 `/vlm/analyze-upload`이며 UI와 부록 의사코드는 이 최종 경로만 호출한다.

`POST /vlm/analyze-upload` 요청은 FormData다.

```js
const formData = new FormData();
formData.append('image', file);
const vlm = await immaApi.apiForm('/vlm/analyze-upload', formData, { timeoutMs: 310000 });
```

`POST /api/match-v2` 요청은 JSON이다.

```js
const matchPayload = {
  drawing_id: vlm.drawing_id,
  order_quantity: Number(quantityInput.value),
  requested_delivery_date: deliveryDateInput.value || null,
  budget_amount: budgetAmount || null,
  budget_currency: 'KRW',
  general_notes: [{ note: notesInput.value || '' }]
};
const matchResult = await immaApi.apiJson('/api/match-v2', {
  method: 'POST',
  body: matchPayload
});
```

`parts`를 직접 보내지 않는다. `drawing_id`만 보내면 백엔드가 저장된 VLM raw JSON을 GraphRAG로 변환한다(`routers/matching.py:204-241`).

#### 4.3.2 VLM timeout과 fixture fallback

VLM은 Replicate polling을 하며 기본 timeout은 300초다(`routers/vlm.py:112-136`). Phase 1 UI는 300초 초과 또는 504 응답을 받으면 투명하게 fixture로 전환한다.

전환 메시지는 다음 그대로 쓴다.

> AI 분석 시간 초과 — 발표용 사전 분석 결과로 전환합니다.

fixture는 `v_b_export_samples/sample_00015` S45C 펌프 도면이다. 이 도면의 VLM raw JSON은 발표 전 DB `drawings` 테이블에 사전 INSERT되어 있어야 한다. UI fallback은 새 VLM을 호출하지 않고 사전 준비된 `drawing_id`로 `/api/match-v2`를 호출한다.

fallback drawing id는 환경 또는 HTML data attribute로 주입한다.

```html
<body data-demo-fixture-drawing-id="00000000-0000-0000-0000-000000000015">
```

```js
const fixtureDrawingId = document.body.dataset.demoFixtureDrawingId;
if (!fixtureDrawingId) {
  throw new Error('fixture drawing_id가 설정되지 않았습니다');
}
const matchResult = await immaApi.apiJson('/api/match-v2', {
  method: 'POST',
  body: {
    drawing_id: fixtureDrawingId,
    order_quantity,
    requested_delivery_date,
    budget_currency: 'KRW',
    general_notes: [{ note: 'VLM timeout fixture fallback' }]
  }
});
```

#### 4.3.3 `matching.html`: 매칭 결과 표시

`matching.html`은 user-scoped localStorage에서 `imma:{user_id}:{rfq_id}:match_result`를 읽는다. 없으면 `/api/rfq/{rfq_id}`로 RFQ 상세를 보여주되, 후보 결과는 “매칭 결과가 없습니다. 다시 실행하세요.”로 표시한다. Phase 1에서는 match result 상세 조회 API가 없으므로 localStorage 보존이 중요하다.

렌더링 규칙은 다음과 같다.

- `result.parts`를 항상 `forEach`로 순회한다.
- part가 `status === 'rejected'`이면 `rejection_reason`, `missing_fields`, `message`, `match_input.warnings`를 표시한다.
- part가 valid면 `recommended_candidates`를 먼저 표시하고, `conditional_candidates`를 접힌 영역으로 표시한다.
- 모든 삽입값은 `escapeHtml()`을 통과한다.
- `match_reasons`는 token map으로 배지화한다.
- `candidate.rank_no`, `candidate.total_score`, `candidate.match_run_id`, `candidate.rfq_part_id`를 표시한다.
- 후보 선택은 백엔드 호출 없이 시각적 강조와 localStorage 메모만 한다.

후보 선택 state key는 다음과 같다.

```text
imma:{user_id}:{rfq_id}:selected_candidate
```

값은 다음 shape를 사용한다.

```json
{
  "company_id": "...",
  "company_name": "...",
  "match_run_id": "...",
  "rfq_part_id": "...",
  "rank_no": 1,
  "selected_at": "2026-05-13T...Z"
}
```

이 선택은 supplier 알림이나 match_candidates 상태를 바꾸지 않는다. 실제 발주 선택은 견적 비교 후 `/api/orders`에서 `quote_id`로 결정된다.

#### 4.3.4 `client-dashboard.html`: buyer RFQ 목록

`client-dashboard.html`은 buyer guard를 적용한다. `/rfqs`를 호출해 최근 RFQ 목록을 표시한다. Phase 1에서는 `routers/rfqs.py:32-74`를 수정해 목록 응답에 `status`를 포함한다. KPI는 이 status로 간단 계산한다.

- open: 요청 진행 중
- quoted: 견적 도착
- ordered: 발주 완료
- cancelled/closed: 종료

정밀 KPI, 납기 통계, 결제 통계는 Phase 2로 미룬다.

#### 4.3.5 `order-management.html`: 견적 비교와 발주

buyer가 `/order-management?rfq_id={rfq_id}`로 들어오면 다음을 수행한다.

1. `GET /api/rfq/{rfq_id}`로 RFQ 상세 표시
2. `GET /api/rfq/{rfq_id}/quotes`로 견적 목록 표시
3. 견적이 없으면 “supplier 수락 및 견적 제출을 기다리는 중” 표시
4. 견적이 있으면 `total_price`, `estimated_lead_days`, `proposed_delivery_date`, `assumptions`, `status`만 표시
5. 부품별 line item breakdown은 Phase 2로 미룬다. 현재 `GET /api/rfq/{rfq_id}/quotes`는 `quote_line_items`를 반환하지 않는다(`routers/quotes.py:182-220`).
6. buyer가 견적을 선택하면 `POST /api/orders`에 `{quote_id}`를 보낸다.
7. 성공 응답의 `order_id`를 user-scoped localStorage에 저장하고 주문 상세 섹션을 연다.

발주 생성 후 key는 다음과 같다.

```text
imma:{user_id}:current_order_id
imma:{user_id}:{rfq_id}:order_id
```

### 4.4 supplier 흐름

#### 4.4.1 `supplier-dashboard.html`

supplier guard를 적용한다. 초기에는 다음 API를 병렬 호출한다.

- `GET /api/company/matches`
- `GET /api/notifications/unread-count`
- `GET /api/notifications?unread_only=false`

대시보드 KPI는 간단한 count만 보여준다. 정밀 매출·납기·품질 KPI는 Phase 2다.

#### 4.4.2 `supplier-workbench.html`: 매칭 요청 수락/거절

`supplier-workbench.html`은 `GET /api/company/matches`를 호출한다. 응답 shape는 다음과 같다(`routers/matching.py:632-684`).

```json
{
  "count": 1,
  "matches": [
    {
      "match_run_id": "...",
      "rfq_id": "...",
      "part_name": "...",
      "material": "S45C",
      "processes": "turning, milling",
      "total_score": 0.86,
      "rank_no": 1,
      "supplier_response": "pending",
      "responded_at": null,
      "created_at": "..."
    }
  ]
}
```

수락 버튼은 다음 endpoint를 호출한다.

```js
await immaApi.apiJson(`/api/match-candidates/${matchRunId}/${user.id}/respond`, {
  method: 'PUT',
  body: { response: 'accepted' }
});
```

거절 버튼은 `response: 'declined'`를 보낸다. 이미 응답한 매칭은 버튼을 비활성화한다. backend는 중복 응답을 400으로 막는다(`routers/matching.py:728-733`).

#### 4.4.3 supplier 견적 제출

견적 폼은 accepted 상태인 match만 활성화한다. `POST /api/quote`는 supplier JWT와 본인 `company_id`를 요구한다. 요청 body는 다음과 같다.

```json
{
  "rfq_id": "...",
  "company_id": "로그인한 supplier user.id",
  "total_price": 12500000,
  "estimated_lead_days": 14,
  "proposed_delivery_date": "2026-06-15",
  "validity_until": "2026-06-01",
  "assumptions": "도면 기준 S45C 소재, 열처리 제외",
  "line_items": [
    {
      "rfq_part_id": "...",
      "process_code": "turning",
      "description": "선삭 및 밀링 가공",
      "quantity": 10,
      "unit_price": 1250000,
      "line_total": 12500000,
      "notes": "Phase 1 단부품 기준"
    }
  ]
}
```

Phase 1 UI는 line_items를 단부품 기준으로 보내되, buyer 견적 비교 화면에는 breakdown을 표시하지 않는다.

#### 4.4.4 supplier order discovery

supplier의 발주 발견 방식은 부록 B.6 `loadSupplierOrdersFromNotifications()`를 단일 기준으로 사용한다. Phase 1에서는 `GET /api/orders` 목록 endpoint를 만들지 않고, `/api/notifications?unread_only=false`에서 `event_type='order_confirmed'`, `reference_type='order'`, `reference_id`가 있는 알림만 필터링한다. 필터링된 `reference_id`를 order_id로 보고 `GET /api/orders/{order_id}`를 호출한다.

#### 4.4.5 주문 상태 전이

상태 전이는 backend matrix와 동일하게 표시한다.

| 현재 상태 | 가능한 다음 상태 | 허용 role |
|---|---|---|
| contracting | ordered | buyer, supplier |
| contracting | cancelled | buyer, admin |
| ordered | in_production | supplier |
| ordered | cancelled | buyer, admin |
| in_production | inspection | supplier |
| in_production | cancelled | buyer, admin |
| in_production | disputed | buyer, supplier |
| inspection | shipped | supplier |
| inspection | in_production | buyer |
| inspection | disputed | buyer, supplier |
| shipped | delivered | buyer |
| shipped | disputed | buyer, supplier |
| delivered | completed | buyer |
| delivered | disputed | buyer, supplier |

UI는 role과 current status를 보고 버튼을 렌더링한다. backend가 최종 검증하므로 UI에 없는 전이도 backend에서 막힌다.

### 4.5 admin 흐름

#### 4.5.1 admin Phase 1 범위

admin은 pending supplier 승인/반려만 실연결한다. RFQ/Order/KPI 관제는 더미로 둔다. 화면에는 “Phase 1에서는 업체 승인/반려만 실데이터입니다”라는 작은 안내 문구를 둔다.

#### 4.5.2 `admin-dashboard.html`

admin guard를 적용한다. pending count는 `GET /api/admin/companies/pending`로 가져올 수 있다. KPI 카드 대부분은 demo value를 유지하되 demo global script 없이 정적 값으로 렌더링한다.

#### 4.5.3 `admin-operations.html`

초기화 시 `GET /api/admin/companies/pending` 호출. 각 행에 승인/반려 버튼을 둔다.

승인:

```js
await immaApi.apiJson(`/api/admin/companies/${companyId}/verify`, { method: 'PUT' });
```

반려:

```js
await immaApi.apiJson(`/api/admin/companies/${companyId}/reject`, {
  method: 'PUT',
  body: { reason }
});
```

성공하면 해당 행을 제거하거나 status badge를 갱신하고 목록을 다시 불러온다.

### 4.6 외부 URL 일괄 치환 영역의 재정의

Phase 1에서는 “외부 URL을 같은 path로 바꾼다”가 아니라 “시뮬레이션 flow를 IMMA flow로 재작성한다”가 기준이다. 실제 로드되지 않는 `client.js`의 더미 payload는 구현 대상이 아니다.

실제 치환 대상은 다음이다.

| 대상 | 기존 성격 | Phase 1 처리 |
|---|---|---|
| HTML script tag의 `site-actions.js` | demo 본체 | 제거하고 `imma-ui-utils.js`, `auth.js`, `imma-api.js`로 교체 |
| `quote-request.html` inline script | 파일명 표시·폼 UX | VLM upload + match-v2 호출로 재작성 |
| `matching.html` inline script | 정적 후보 표시 | match result renderer로 재작성 |
| `supplier-workbench.html` inline/script | 정적 작업대 | company matches, respond, quote, notifications로 재작성 |
| `order-management.html` inline/script | 정적 발주 관리 | quote list, create order, get order, status update로 재작성 |
| `supplier-register.html` inline/script | 가입 UX | signup → login → profile/capability/equipment 호출로 재작성 |
| admin HTML inline/script | 정적 관제 | pending/verify/reject만 실 API로 재작성 |

아래 fas 더미 스키마는 Phase 1에서 사용하지 않는다.

```js
{
  buyer_code: 'B001',
  process: 'milling',
  due_date: '...'
}
```

IMMA match flow는 다음 필드를 사용한다.

```js
{
  drawing_id: 'uuid',
  order_quantity: 10,
  requested_delivery_date: '2026-06-15',
  budget_amount: 5000000,
  budget_currency: 'KRW',
  general_notes: [{ note: '...' }]
}
```

### 4.7 에러·로딩 처리

HTTP 상태별 UI 메시지는 다음처럼 통일한다.

| 상태 | UI 처리 |
|---|---|
| 400 | 입력값 오류. backend detail 표시 |
| 401 | token 제거 후 로그인 페이지로 단일 redirect |
| 403 | 권한 없음. role home으로 이동 또는 현재 화면에서 toast |
| 404 | 대상 없음. 이전 페이지 이동 버튼 표시 |
| 409 | 중복 계정 또는 중복 상태. detail 표시 |
| 422 | body schema 오류. form validation 메시지 표시 |
| 500 | 서버 내부 오류. detail 있으면 표시 |
| 502 | 외부 AI/API 호출 실패. VLM이면 fixture fallback 가능 |
| 504 | VLM timeout. fixture fallback으로 자동 전환 |

동시 401이 여러 API에서 발생해도 redirect는 한 번만 일어난다. `immaApi`가 `logoutRedirectInProgress` flag를 가진다.

### 4.8 localStorage lifecycle

logout 시 다음을 모두 지운다.

- `imma_token`
- `imma_user`
- `imma:{user_id}:*` prefix에 해당하는 모든 key
- 기존 demo key인 `immaDemo*` key

사용자 전환 시 이전 user-scoped key는 새 사용자가 읽을 수 없어야 한다.

### 4.9 다부품 RFQ Phase 1 처리

백엔드는 `parts` 배열과 part-level `rfq_part_id`를 지원한다. Phase 1 UI는 단부품 시연을 기준으로 하지만 다부품 응답에서 깨지지 않아야 한다.

- `matching.html`은 `result.parts.forEach(renderPart)`로 렌더링한다.
- 각 part section은 part name/material/process/quantity/status를 표시한다.
- part별 후보는 read-only로 표시한다.
- 후보 선택은 우선 첫 번째 valid part의 후보만 “시연 선택”으로 허용한다. 다부품 part별 후보 선택과 통합 견적은 Phase 2다.
- rejected part는 후보 카드 대신 rejection panel을 표시한다.

---

## 5. Phase 2 청사진

### 5.1 목록 API

Phase 2에서는 다음 목록 API를 추가한다.

- `GET /api/orders`: buyer/supplier/admin role별 주문 목록
- `GET /api/jobs`: supplier 작업 목록
- `GET /api/quotes`: supplier 본인 견적 목록
- `GET /api/match-runs/{match_run_id}`: 매칭 결과 상세 재조회
- `GET /api/rfq/{rfq_id}/match-results`: buyer가 localStorage 없이 매칭 결과 재조회

Phase 1에서는 localStorage와 notifications를 사용해 이 결손을 우회한다.

### 5.2 결제

Phase 1의 `payment-success.html`은 실제 결제 API를 호출하지 않는다. Phase 2에서 결제 생성, 결제 승인, 환불, 영수증, 주문 상태와 결제 상태 분리를 구현한다.

### 5.3 메시징

`supplier-messages.html`은 Phase 1에서 read-only demo다. Phase 2에서 RFQ별 buyer-supplier 메시지, attachment, unread count, WebSocket/SSE를 추가한다.

### 5.4 supplier 검색

`search-suppliers.html`은 Phase 1에서 정적 검색 체험으로 둔다. Phase 2에서 소재·공정·장비·지역·평점 기반 검색 API를 추가한다.

### 5.5 admin 관제

Phase 2에서 admin RFQ/Order/KPI를 실 데이터로 확장한다. 현재 존재하는 `GET /api/admin/rfqs`, `GET /api/admin/orders`는 Phase 1에서 쓰지 않거나 보조 read-only로만 쓴다.

### 5.6 시뮬레이션 잔재 제거

Phase 2에서 다음 파일을 정리한다.

- `client.js`
- `supplier.js`
- `app.js`
- `app_unified.js`
- `shared-state.js`
- `site-actions-demo.js`

Phase 1에서는 load하지 않으므로 동작에는 영향을 주지 않는다.

---

## 6. 페이지별 변경 명세

### 6.1 HTML 21개

| 파일 | 변경 수준 | 상세 |
|---|---:|---|
| `landing.html` | P0 | `site-actions.js` 제거, auth/api script 로드, 로그인 모달을 `/api/login`·`/api/admin/login`으로 연결, header auth state 표시 |
| `client-register.html` | P0 | buyer signup → login, 중복 ID 409 처리, role home 이동 |
| `supplier-register.html` | P0 | `name`/`company_name` 분리, signup → login → profile/capability/equipment, admin 승인 대기 안내 |
| `client-dashboard.html` | P1 | buyer guard, `/rfqs` 목록, status 기반 간단 KPI, header 정리 |
| `quote-request.html` | P0 | buyer guard, `/vlm/analyze-upload`, VLM progress, fixture fallback, `/api/match-v2`, scoped storage |
| `matching.html` | P0 | buyer guard, parts renderer, candidate card, signal token map, local candidate selection |
| `order-management.html` | P0 | buyer/supplier guard, quote list, order create, notification-derived order detail, status transition |
| `client-fulfillment.html` | P2 | buyer guard, read-only demo, 실 API 방해 없음 |
| `payment-success.html` | P2 | buyer guard, demo success, current order id 표시 가능 |
| `supplier-dashboard.html` | P1 | supplier guard, matches/notifications count |
| `supplier-workbench.html` | P0 | matches list, respond, quote submit, order discovery, status transition |
| `supplier-rfq-detail.html` | P1 | supplier guard, selected match detail, quote form 보조 |
| `supplier-settings.html` | P1 | supplier guard, profile/material/process/equipment/availability 연결 |
| `supplier-messages.html` | P2 | supplier guard, read-only demo, 실 API 방해 없음 |
| `search-suppliers.html` | P2 | public 또는 buyer optional, read-only demo, external URL 제거 |
| `admin-dashboard.html` | P1 | admin guard, pending count 실 API, KPI는 더미 표시 |
| `admin-operations.html` | P0 | admin guard, pending/verify/reject 실 API |
| `admin-control-center.html` | P2 | admin guard, 설정 데모, 실 API 방해 없음 |
| `how-to-use.html` | P2 | public, auth header state 표시 |
| `process-flow.html` | P2 | public, auth header state 표시 |
| `support.html` | P2 | public, auth header state 표시 |

“변경 없음”인 HTML은 없다. 본문 기능이 바뀌지 않는 public/info/demo 페이지도 최소한 `site-actions.js` 제거, `auth.js` 로드, header 로그인 상태 정리는 적용한다.

### 6.2 JS 6개와 신규 JS

| 파일 | Phase 1 처리 |
|---|---|
| `site-actions.js` | 실 페이지에서 load 제거. 필요 시 `site-actions-demo.js`로 rename/split |
| `admin-menu.js` | 유지. demo rewrite가 있으면 realmode guard 추가 |
| `client.js` | Phase 1 미사용. 수정하지 않음 |
| `supplier.js` | Phase 1 미사용. 수정하지 않음 |
| `app.js` | Phase 1 미사용. 수정하지 않음 |
| `app_unified.js` | Phase 1 미사용. 수정하지 않음 |
| `shared-state.js` | Phase 1 미사용. 수정하지 않음 |
| `imma-ui-utils.js` | 신규 |
| `auth.js` | 신규 |
| `imma-api.js` | 신규 |
| `site-actions-demo.js` | 선택 신규. demo 본체 보관용이며 실모드에서 로드하지 않음 |

### 6.3 CSS

기존 CSS는 유지한다. 다음 클래스는 필요한 경우 `imma-common.css`에 추가한다.

- `.imma-toast-container`, `.imma-toast`, `.imma-toast-info`, `.imma-toast-warning`, `.imma-toast-error`, `.imma-toast-success`
- `.imma-loading-overlay`
- `.imma-signal-badge`, `.imma-signal-info`, `.imma-signal-warning`, `.imma-signal-danger`, `.imma-signal-neutral`
- `.imma-candidate-card`, `.imma-candidate-card.is-selected`, `.imma-part-section`, `.imma-rejected-part`
- `.imma-demo-notice`

---

## 7. API 매핑 표

### 7.1 인증 API

| UI | Method | Path | 인증 | 요청 | 응답/처리 |
|---|---|---|---|---|---|
| landing login | POST | `/api/login` | 없음 | `{login_id,password}` | buyer/supplier token 저장 |
| landing admin login | POST | `/api/admin/login` | 없음 | `{login_id,password}` | admin token 저장 |
| 모든 보호 페이지 | GET | `/api/me` | JWT | 없음 | 세션 검증 |
| client-register | POST | `/signup` | 없음 | buyer fields | token 없음. 후속 `/api/login` 필수 |
| supplier-register | POST | `/signup` | 없음 | supplier fields | token 없음. 후속 `/api/login` 필수 |

### 7.2 buyer API

| UI | Method | Path | 인증 | 설명 |
|---|---|---|---|---|
| quote-request | POST | `/vlm/analyze-upload` | buyer | 이미지 VLM 분석, drawings 저장 |
| quote-request | POST | `/api/match-v2` | buyer | RFQ 생성, GraphRAG 매칭, match history 저장 |
| matching | GET | `/api/rfq/{rfq_id}` | buyer/supplier/admin | RFQ 상세 fallback 조회 |
| client-dashboard | GET | `/rfqs` | buyer | 본인 RFQ 목록. Phase 1에서 status 추가 |
| order-management | GET | `/api/rfq/{rfq_id}/quotes` | buyer | 견적 비교. line_items 미포함 |
| order-management | POST | `/api/orders` | buyer | quote_id로 발주 생성 |
| order-management | GET | `/api/orders/{order_id}` | buyer/supplier/admin | 주문 상세 |
| order-management | PUT | `/api/orders/{order_id}/status` | buyer/supplier/admin | 권한 matrix 기반 상태 전이 |
| 공통 | GET | `/api/notifications?unread_only=false` | buyer/supplier/admin | 본인 알림 |

### 7.3 supplier API

| UI | Method | Path | 인증 | 설명 |
|---|---|---|---|---|
| supplier-dashboard/workbench | GET | `/api/company/matches` | supplier | 본인 매칭 요청 목록 |
| supplier-workbench | PUT | `/api/match-candidates/{match_run_id}/{company_id}/respond` | supplier | accepted/declined 응답 |
| supplier-workbench | POST | `/api/quote` | supplier | accepted match에 대한 견적 제출 |
| supplier-workbench | GET | `/api/notifications?unread_only=false` | supplier | order_confirmed 알림 조회 |
| supplier-workbench | GET | `/api/orders/{order_id}` | supplier | 발주 상세 조회 |
| supplier-workbench | PUT | `/api/orders/{order_id}/status` | supplier | ordered→in_production 등 상태 전이 |
| supplier-settings/register | PUT | `/api/company/profile` | supplier | 회사 프로필·주소·담당자 |
| supplier-settings/register | POST | `/api/material-capability` | supplier | 재질 역량 |
| supplier-settings/register | POST | `/api/process-capability` | supplier | 공정 역량 |
| supplier-settings/register | POST | `/api/equipment` | supplier | 장비 등록 |
| supplier-settings | PUT | `/api/company/availability` | supplier | 가용성 |
| supplier-settings | GET | `/api/processes` | 없음 | 공정 catalog |
| supplier-settings | GET | `/api/material-categories` | 없음 | 재질 category |
| supplier-settings | GET | `/api/materials` | 없음 | 재질 catalog |
| supplier-settings | GET | `/api/equipment-categories` | 없음 | 장비 category |
| supplier-settings | GET | `/api/equipment-models` | 없음 | 장비 model |

### 7.4 admin API

| UI | Method | Path | 인증 | Phase 1 사용 |
|---|---|---|---|---|
| landing/admin | POST | `/api/admin/login` | 없음 | 사용 |
| admin-dashboard/operations | GET | `/api/admin/companies/pending` | admin | 사용 |
| admin-operations | PUT | `/api/admin/companies/{company_id}/verify` | admin | 사용 |
| admin-operations | PUT | `/api/admin/companies/{company_id}/reject` | admin | 사용 |
| admin pages | GET | `/api/admin/rfqs` | admin | Phase 1 더미 유지. 선택 read-only만 허용 |
| admin pages | GET | `/api/admin/orders` | admin | Phase 1 더미 유지. 선택 read-only만 허용 |
| admin/system | GET | `/api/config/health` | admin | 선택. boolean만 반환 |

### 7.5 핵심 시퀀스

#### buyer → match

```text
POST /api/login
GET /api/me
POST /vlm/analyze-upload
POST /api/match-v2
localStorage 저장
/matching-ui?rfq_id=...
```

#### supplier → quote

```text
POST /api/login
GET /api/company/matches
PUT /api/match-candidates/{match_run_id}/{company_id}/respond {accepted}
POST /api/quote
```

#### buyer → order

```text
GET /api/rfq/{rfq_id}/quotes
POST /api/orders {quote_id}
GET /api/orders/{order_id}
PUT /api/orders/{order_id}/status
```

#### supplier → confirmed order

```text
GET /api/notifications?unread_only=false
filter event_type=order_confirmed, reference_type=order
GET /api/orders/{reference_id}
PUT /api/orders/{order_id}/status
```

#### admin → supplier approval

```text
POST /api/admin/login
GET /api/admin/companies/pending
PUT /api/admin/companies/{company_id}/verify
또는
PUT /api/admin/companies/{company_id}/reject {reason}
```

---

## 8. 데이터 모델

### 8.1 localStorage key

전역 인증 key는 두 개만 둔다.

| Key | 값 |
|---|---|
| `imma_token` | JWT access token |
| `imma_user` | `{id, login_id, role}` JSON |

업무 상태 key는 user id를 반드시 포함한다.

| Key | 값 |
|---|---|
| `imma:{user_id}:current_rfq_id` | 현재 buyer RFQ id |
| `imma:{user_id}:{rfq_id}:match_result` | `/api/match-v2` 응답 전체 |
| `imma:{user_id}:{rfq_id}:selected_candidate` | buyer 화면의 시각적 후보 선택 |
| `imma:{user_id}:{rfq_id}:order_id` | 해당 RFQ로 생성된 order id |
| `imma:{user_id}:current_order_id` | 현재 주문 id |
| `imma:{user_id}:last_fixture_used` | VLM fixture fallback 사용 여부 |

기존 demo key는 logout 시 함께 지운다.

- `immaDemoSupplierReplySent`
- `immaDemoProductionShared`
- `immaDemoClientPaid`
- 기타 `immaDemo` prefix key

### 8.2 match-v2 응답 shape

Phase 1 보강 후 `/api/match-v2` 응답은 다음 핵심 필드를 가진다.

```json
{
  "rfq_id": "...",
  "match_run_id": "...",
  "drawing_no": "...",
  "delivery_date": "2026-06-15",
  "parts": [
    {
      "rfq_part_id": "...",
      "status": "matched",
      "match_input": {
        "material": "S45C",
        "required_processes": ["turning", "milling"],
        "warnings": []
      },
      "candidates": [
        {
          "match_run_id": "...",
          "rfq_part_id": "...",
          "rank_no": 1,
          "company_id": "...",
          "company_name": "...",
          "match_reasons": ["[INFO_PARENT_FALLBACK] ..."],
          "material_match_type": "specific_material",
          "best_it_grade": 6,
          "best_ra_um": 1.6,
          "overall_status": "available",
          "avg_rating": 4.7,
          "review_count": 12,
          "next_available_date": "2026-05-20",
          "equipment_verified": true,
          "equipment_verified_warning": null,
          "technical_score": 0.8,
          "availability_score": 1.0,
          "quality_score": 0.94,
          "total_score": 0.902,
          "availability_info": {
            "delivery_feasible": true,
            "estimated_lead_days": 10,
            "available_days": 20
          }
        }
      ],
      "recommended_candidates": [],
      "conditional_candidates": []
    }
  ]
}
```

`recommended_candidates`와 `conditional_candidates`는 `candidates`와 같은 객체 reference를 기반으로 하므로 `_save_match_history()`에서 candidate dict를 mutate하면 세 배열에 같은 보강 필드가 반영된다.

### 8.3 신호 토큰 맵

| token prefix | UI class | 표시명 | 설명 |
|---|---|---|---|
| `[INFO_CATEGORY_FALLBACK]` | info | 재질 카테고리 보완 | cast steel → carbon steel 같은 fallback |
| `[INFO_PARENT_FALLBACK]` | info | 부모 공정 대체 | 세부 공정 대신 부모 공정 역량으로 match |
| `[WARN_EQUIPMENT_CAPABILITY_MISSING]` | warning | 장비 검증 부족 | 공정 가능 장비 증빙 부족 |
| `[공정 달성범위 의심]` | warning | 공정 정밀도 의심 | 업체 주장 정밀도와 요구 정밀도 간 의심 |
| `[공정 달성범위 의심·재질override]` | warning | 재질 override 의심 | 비금속 또는 특수 재질 override 분기 |
| `[공정순서 위반]` | danger | 공정순서 위반 | 필수 공정 순서 위반 |
| `[공정순서 권장위반]` | warning | 권장순서 위반 | 권장 공정 순서 위반 |
| `[unsupported]` | danger | 지원 불가 | rejected part의 rejection_reason |
| 기타 | neutral | 참고 | 알 수 없는 token도 숨기지 않음 |

`[unsupported]`는 보통 candidate `match_reasons`가 아니라 rejected part의 `rejection_reason`에 나타난다. 따라서 part renderer와 candidate renderer가 모두 token map을 사용할 수 있어야 한다.

### 8.4 quote/order 데이터

Phase 1 buyer 견적 비교 화면은 다음 필드만 사용한다.

- `quote_id`
- `company_id`
- `company_name`
- `total_price`
- `estimated_lead_days`
- `proposed_delivery_date`
- `validity_until`
- `assumptions`
- `status`
- `submitted_at`

`quote_line_items`는 Phase 2에서 조회 응답에 포함한다.

---

## 9. VLM cold start 대응

### 9.1 진행도 단계

VLM progress 문구는 전체 문서와 코드에서 다음 하나로 통일한다.

| 시간 | 메시지 |
|---:|---|
| 0~30초 | 도면 업로드 처리 중 |
| 30~90초 | AI 분석 준비 중 (최초 분석은 시간이 걸립니다) |
| 90~180초 | 딥러닝 모델 분석 중 |
| 180~240초 | 최종 추출 중 |
| 240~300초 | 분석이 길어지고 있습니다. 실패 시 사전 분석 결과로 자동 전환됩니다 |
| 300초 초과 | AI 분석 시간 초과 — 사전 분석 결과로 전환합니다 |

### 9.2 fallback 정책

Phase 1에서는 fallback을 숨기지 않는다. 사용자가 보는 UI에 명확히 표시한다. 발표 슬라이드에도 Replicate cold start 한계를 별도로 설명한다.

fallback은 3가지 경우에 실행한다.

1. client timeout이 300초를 넘은 경우
2. backend가 504를 반환한 경우
3. backend가 502를 반환했고 사용자가 재시도 대신 fixture 전환을 선택한 경우

Phase 1 기본값은 504에서 자동 fallback이다. 502는 toast 후 “사전 분석 결과로 계속” 버튼을 제공한다.

### 9.3 fixture 사전 준비

발표 전 DB에 `v_b_export_samples/sample_00015` S45C 펌프 도면의 VLM raw JSON을 넣는다. INSERT는 `drawings` 테이블 스키마에 맞춰 수행한다(`lookup_tables/schema.sql:335-343`). buyer_id는 시연 buyer `kim_cheolsu`의 buyer_id를 사용한다.

예시 SQL은 다음과 같다.

```sql
INSERT INTO imma.drawings
  (drawing_id, buyer_id, drawing_no, file_uri, file_sha256, original_filename, vlm_result_jsonb)
VALUES
  (
    '00000000-0000-0000-0000-000000000015',
    (SELECT buyer_id FROM imma.buyers WHERE login_id = 'kim_cheolsu'),
    'sample_00015',
    'v_b_export_samples/sample_00015',
    'sample_00015_fixture_sha256',
    'sample_00015.png',
    CAST(:vlm_json AS jsonb)
  )
ON CONFLICT (drawing_id) DO UPDATE SET
  buyer_id = EXCLUDED.buyer_id,
  drawing_no = EXCLUDED.drawing_no,
  file_uri = EXCLUDED.file_uri,
  vlm_result_jsonb = EXCLUDED.vlm_result_jsonb;
```

`file_sha256` unique 충돌을 피하려면 fixture sha는 고정 문자열을 쓰거나 실제 파일 sha를 넣는다.

---

## 10. 검증 전략

### 10.1 단위 검증

#### 인증

- `/api/login` 성공 시 token/user 저장
- `/api/admin/login` 성공 시 user.role이 `admin`
- `/api/me` 실패 시 logout
- 만료 token local decode 시 즉시 logout
- 동시 401 3개 발생 시 redirect 1회
- logout 후 `imma:{user_id}:*`와 `immaDemo*` 삭제

#### site-actions 분리

- 실 페이지 HTML에 `site-actions.js` script tag가 없어야 한다.
- 실 페이지에서 폼 submit이 전역 handler에 막히지 않아야 한다.
- `quote-request.html` submit이 `/client-fulfillment#ai`로 이동하지 않아야 한다.
- `matching.html`, `order-management.html`, `supplier-workbench.html`에 scenario text replacement가 없어야 한다.

#### match-v2 응답 보강

- top-level `match_run_id` 존재
- 각 valid part의 candidate에 `match_run_id`, `rank_no`, `rfq_part_id` 존재
- `recommended_candidates`와 `conditional_candidates`에도 동일 필드 존재
- `_save_match_history()` 실패 시 `/api/match-v2`가 500을 반환하고 UI가 “supplier 전송 실패”를 표시

#### supplier signup

- supplier 가입 요청에서 `company_name='신규정밀'`, `name='박영업'`이면 companies.company_name이 `신규정밀`로 저장
- representative_name 또는 contact에 `박영업` 저장
- 가입 응답에 token 없음
- 자동 로그인 후 profile/capability/equipment 호출은 JWT 포함

#### order discovery

- buyer 발주 후 supplier 알림에 `event_type='order_confirmed'`, `reference_type='order'`, `reference_id=order_id` 존재
- supplier가 notifications에서 order_id를 찾고 `GET /api/orders/{order_id}` 조회 성공
- supplier가 `ordered → in_production` 전이를 성공
- buyer가 `shipped → delivered`, `delivered → completed` 전이를 성공

### 10.2 E2E 시나리오

#### 시나리오 1: fixture 기반 buyer 매칭

1. `kim_cheolsu/demo1234` 로그인
2. `/quote-request` 진입
3. fixture drawing id가 설정되어 있는지 확인
4. 실제 VLM 대신 fixture fallback 버튼 또는 timeout fallback 사용
5. `/api/match-v2` 성공
6. `/matching-ui?rfq_id=...` 이동
7. expected snapshot 확인

Expected snapshot은 정확한 업체 순위를 지나치게 고정하지 않는다. GraphRAG와 availability score가 일부 변동할 수 있으므로 허용 범위를 둔다.

| 항목 | 기대값 |
|---|---|
| drawing | `sample_00015` |
| material | S45C 또는 carbon steel 계열로 해소 |
| parts count | 1 이상 |
| valid part | 1 이상 |
| recommended + conditional 후보 | 1 이상 |
| 각 후보 필수 필드 | `company_id`, `company_name`, `match_run_id`, `rfq_part_id`, `rank_no` |
| signal token | 알려진 token map으로 표시 가능. unknown token 숨기지 않음 |
| RFQ 저장 | `/api/rfq/{rfq_id}` 조회 가능 |
| supplier match 저장 | 해당 supplier가 `/api/company/matches`에서 조회 가능 |

#### 시나리오 2: supplier 견적 제출

1. 매칭된 supplier 계정으로 로그인
2. `/supplier-workbench` 진입
3. `GET /api/company/matches`에서 pending match 확인
4. 수락 버튼 클릭
5. `supplier_response='accepted'`로 갱신
6. 견적 폼 제출
7. `/api/quote` 성공
8. buyer 알림 또는 buyer quote list에서 견적 확인

#### 시나리오 3: buyer 발주

1. buyer로 `/order-management?rfq_id=...` 진입
2. `/api/rfq/{rfq_id}/quotes`에서 견적 확인
3. 견적 선택 후 `/api/orders` 호출
4. 응답의 `order_id`, `status='contracting'` 확인
5. `GET /api/orders/{order_id}` 확인
6. 가능한 buyer 상태 전이 버튼만 표시

#### 시나리오 4: supplier 발주 수신

1. supplier로 `/supplier-workbench` 진입
2. notifications에서 `order_confirmed` 필터
3. `reference_id`로 order detail 조회
4. `contracting → ordered` 또는 `ordered → in_production` 중 가능한 버튼 클릭
5. status 갱신 확인

#### 시나리오 5: admin 승인/반려

1. 신규 supplier 가입
2. 자동 로그인 후 온보딩 입력
3. admin으로 로그인
4. `/admin-operations`에서 pending 목록 확인
5. 승인 또는 반려 클릭
6. 목록 갱신과 supplier 알림 확인

### 10.3 회귀 테스트

- 다부품 `parts` 응답에서 UI crash가 없어야 한다.
- rejected part에서 `[unsupported]`가 표시되어야 한다.
- 403 응답에서 사용자 role home으로 안전 이동해야 한다.
- localStorage의 타 사용자 RFQ 결과를 읽지 않아야 한다.
- admin token으로 buyer page에 들어가면 admin home으로 이동해야 한다.
- `JWT_SECRET` 기본값으로 production/Railway 환경을 시작하면 fail-fast 되어야 한다.

---

## 11. 위험 + 완화

### 11.1 활성 JS 오판 위험

`client.js`를 고쳐도 실제 페이지가 바뀌지 않는다. 모든 구현자는 활성 target을 HTML inline script와 신규 공용 JS로 고정한다. `client.js`, `supplier.js`, `app.js`는 Phase 1 TODO에서 제외한다.

### 11.2 `site-actions.js` 충돌 위험

global submit/click prevent가 실 API를 막을 수 있다. 모든 HTML에서 `site-actions.js` script tag를 제거한다. demo 기능이 필요하면 `site-actions-demo.js`로 분리하고 실모드에서는 로드하지 않는다.

### 11.3 match history 저장 실패 위험

`/api/match-v2` 결과는 buyer 화면에 보이지만 `_save_match_history()`가 실패하면 supplier `/api/company/matches`가 비어 있을 수 있다. Phase 1에서는 fail-fast로 바꾼다. 매칭 결과만 보여주고 supplier 전송이 안 된 상태를 성공으로 취급하지 않는다.

### 11.4 admin seed 오류 위험

bcrypt seed 코드를 추가하면 `_verify_password()`의 PBKDF2 형식과 맞지 않는다. `setup_db.py:1053-1068`의 기존 seed를 사용한다. admin 계정은 `admin/test1234`이며 시연 후 비밀번호 변경을 권장한다.

### 11.5 RFQ/quote/order 목록 부족 위험

`GET /api/orders`가 없으므로 supplier 주문 목록은 notifications로 우회한다. buyer 견적 breakdown은 `GET /api/rfq/{rfq_id}/quotes`에 line_items가 없으므로 Phase 2로 미룬다. buyer RFQ 목록은 `/rfqs`에 status를 추가해 최소 KPI를 만든다.

### 11.6 XSS 위험

회사명, match reasons, assumptions, notes, rejection_reason은 모두 사용자 입력 또는 DB 값이다. 모든 DOM 삽입은 `escapeHtml()`을 사용한다. `innerHTML`은 escape된 조각만 조합할 때 사용한다. 가능하면 `textContent`를 우선한다.

### 11.7 VLM cold start 위험

실제 Replicate 분석은 길어질 수 있다. 진행도 UI와 fixture fallback을 투명하게 제공한다. fallback이 작동해도 `/api/match-v2`는 실제 백엔드 GraphRAG/매칭을 사용하므로 시연 흐름은 유지된다.

### 11.8 GraphRAG 비결정성 위험

동일 도면도 category/process 추출 결과가 일부 달라질 수 있다. expected snapshot은 “후보 수와 필수 필드, 토큰 표시 가능성” 중심으로 검증하고, 회사명/순위는 너무 엄격히 고정하지 않는다. 다만 발표 fixture는 사전 저장된 VLM raw JSON을 사용해 변동성을 낮춘다.

### 11.9 JWT secret 기본값 위험

배포 환경에서 기본 secret을 쓰면 토큰 위조 위험이 있다. `ENV=production` 또는 Railway 환경변수가 감지될 때 `JWT_SECRET`이 기본값이면 서버가 시작되지 않게 한다.

### 11.10 admin 실연결 범위 확장 위험

admin RFQ/order/KPI까지 실연결하면 발표 전 검증 범위가 커진다. Phase 1은 pending + verify/reject로 고정한다. 다른 admin 값은 정적 demo임을 UI에 표시한다.

---

## 12. 결정점 + 가정

| 결정점 | 확정값 |
|---|---|
| VLM fallback | 투명 안내. timeout 시 fixture 자동 전환. 메시지는 “AI 분석 시간 초과 — 사전 분석 결과로 전환” |
| fixture 도면 | `v_b_export_samples/sample_00015` S45C 펌프. DB drawings에 사전 INSERT |
| admin Phase 1 | pending + verify/reject만 실연결. RFQ/order/KPI는 더미 유지 |
| seed admin | `admin/test1234`, admins.role은 `superadmin`, JWT role은 `admin` |
| seed buyer | `kim_cheolsu/demo1234` |
| supplier mock | `setup_db.py` 기존 19개 mock 회사 활용 |
| supplier signup 회사명 | 백엔드 수정. `name`은 담당자명, `company_name`은 회사명 |
| buyer 후보 선택 | UI 표시와 localStorage 메모만. 백엔드 변경 없음 |
| 실제 발주 선택 | 견적 비교 후 `POST /api/orders`의 `quote_id`로 결정 |
| `GET /api/orders` | Phase 2에서 추가. Phase 1 supplier는 notifications로 order_id 발견 |
| quote line items 조회 | Phase 2. Phase 1 buyer 화면에는 total 중심 표시 |
| 다부품 | read-only section 표시와 crash 방지. part별 후보 선택/통합 견적은 Phase 2 |

---

## 13. 작업 산출물 목록

### 13.1 신규 파일

- `machhub_ui/imma-ui-utils.js`
- `machhub_ui/auth.js`
- `machhub_ui/imma-api.js`
- 선택: `machhub_ui/site-actions-demo.js`
- 선택: `scripts/seed_fixture_drawing.py` 또는 `pipeline/setup_db.py` 내부 fixture seed 함수

### 13.2 수정 파일

#### UI

- HTML 21개 전체: script tag 교체, auth header state 적용
- `quote-request.html`: VLM/match flow
- `matching.html`: match result renderer
- `supplier-register.html`: signup/onboarding flow
- `supplier-workbench.html`: matches/respond/quote/order notification flow
- `order-management.html`: quote comparison/order/status flow
- `admin-dashboard.html`: admin guard + pending count
- `admin-operations.html`: pending/verify/reject
- `imma-common.css` 또는 기존 CSS: 신규 UI class 추가

#### Backend

- `routers/matching.py`
- `routers/signup.py`
- `routers/rfqs.py`
- `routers/deps.py`
- `main.py`
- 선택: `routers/admin.py` 또는 config router for admin-only health

### 13.3 환경변수

| 변수 | Phase 1 값 |
|---|---|
| `DATABASE_URL` | 필수 |
| `JWT_SECRET` | 배포 환경 필수. 기본값 사용 금지 |
| `ENV` | local/staging/production 중 하나 |
| `ALLOWED_ORIGINS` | 발표 도메인 + localhost |
| `REPLICATE_API_TOKEN` | 실 VLM 사용 시 필수 |
| `REPLICATE_MODEL_VERSION` | 실 VLM 사용 시 필수 |
| `VLM_REPLICATE_TIMEOUT_SEC` | 기본 300. UI fallback 기준과 맞춤 |
| `DEMO_FIXTURE_DRAWING_ID` | 선택. UI data attribute로도 가능 |

### 13.4 착수 가능 판정

이 v3 명세는 Phase 1 작업 착수 가능 수준이다. 구현자가 더 이상 `client.js`를 고쳐야 하는지, match-v2 후보에 어떤 필드가 필요한지, supplier가 주문을 어떻게 찾는지, admin을 어디까지 실연결할지 재문의하지 않아도 된다. 불확실성은 fixture raw JSON의 실제 내용과 발표 도메인 환경변수뿐이며, 이는 구현 중 입력값으로 주입하면 된다.

---

# 부록 A. 핵심 API 응답 샘플

## A.1 `/api/login`

```json
{
  "access_token": "jwt...",
  "token_type": "bearer",
  "user": {
    "id": "buyer-uuid",
    "login_id": "kim_cheolsu",
    "role": "buyer"
  }
}
```

## A.2 `/api/admin/login`

```json
{
  "access_token": "jwt...",
  "token_type": "bearer",
  "user": {
    "id": "admin-uuid",
    "login_id": "admin",
    "role": "admin"
  }
}
```

## A.3 `/api/match-v2` 보강 응답

```json
{
  "rfq_id": "1b94b832-7a5a-47b2-9d66-111111111111",
  "match_run_id": "2c05c943-8b6b-48c3-8e77-222222222222",
  "drawing_no": "sample_00015",
  "delivery_date": "2026-06-15",
  "parts": [
    {
      "rfq_part_id": "3d16da54-9c7c-49d4-8f88-333333333333",
      "status": "matched",
      "match_input": {
        "part_name": "Pump Shaft",
        "material": "S45C",
        "required_processes": ["turning", "milling"],
        "warnings": []
      },
      "candidates": [
        {
          "match_run_id": "2c05c943-8b6b-48c3-8e77-222222222222",
          "rfq_part_id": "3d16da54-9c7c-49d4-8f88-333333333333",
          "rank_no": 1,
          "company_id": "4e27eb65-ad8d-40e5-9a99-444444444444",
          "company_name": "대한정밀",
          "match_reasons": [
            "소재 S45C 가공 이력 보유",
            "[INFO_PARENT_FALLBACK] precision_turning: 업체가 부모 공정 turning 역량 보유"
          ],
          "material_match_type": "specific_material",
          "best_it_grade": 6,
          "best_ra_um": 1.6,
          "overall_status": "available",
          "avg_rating": 4.6,
          "review_count": 8,
          "next_available_date": "2026-05-20",
          "equipment_verified": true,
          "equipment_verified_warning": null,
          "technical_score": 0.667,
          "availability_score": 1.0,
          "quality_score": 0.92,
          "total_score": 0.843,
          "availability_info": {
            "available_from": null,
            "available_days": 25,
            "estimated_lead_days": 10,
            "delivery_feasible": true
          }
        }
      ],
      "recommended_candidates": [
        {
          "match_run_id": "2c05c943-8b6b-48c3-8e77-222222222222",
          "rfq_part_id": "3d16da54-9c7c-49d4-8f88-333333333333",
          "rank_no": 1,
          "company_id": "4e27eb65-ad8d-40e5-9a99-444444444444",
          "company_name": "대한정밀",
          "match_reasons": ["소재 S45C 가공 이력 보유"],
          "equipment_verified": true,
          "equipment_verified_warning": null,
          "total_score": 0.843
        }
      ],
      "conditional_candidates": []
    }
  ]
}
```

## A.4 rejected part 응답

```json
{
  "rfq_id": "...",
  "match_run_id": "...",
  "parts": [
    {
      "rfq_part_id": "...",
      "status": "rejected",
      "match_input": {
        "part_name": "Unsupported Assembly",
        "warnings": ["[unsupported] 용접 구조물은 Phase 1 범위 외"]
      },
      "rejection_reason": "[unsupported] 용접 구조물은 Phase 1 범위 외",
      "missing_fields": [],
      "message": "지원 범위 외 부품",
      "candidates": [],
      "recommended_candidates": [],
      "conditional_candidates": []
    }
  ]
}
```

## A.5 `/api/company/matches`

```json
{
  "count": 1,
  "matches": [
    {
      "match_run_id": "2c05c943-8b6b-48c3-8e77-222222222222",
      "rfq_id": "1b94b832-7a5a-47b2-9d66-111111111111",
      "part_name": "Pump Shaft",
      "material": "S45C",
      "processes": "turning, milling",
      "total_score": 0.843,
      "rank_no": 1,
      "supplier_response": "pending",
      "responded_at": null,
      "created_at": "2026-05-13 10:00:00+09"
    }
  ]
}
```

## A.6 `/api/notifications?unread_only=false`

```json
[
  {
    "notification_id": "...",
    "event_type": "order_confirmed",
    "title": "발주가 확정되었습니다",
    "message": "RFQ ...에 대한 발주가 확정되었습니다. 계약 절차를 진행해 주세요.",
    "reference_id": "order-uuid",
    "reference_type": "order",
    "is_read": false,
    "created_at": "2026-05-13 11:00:00+09"
  }
]
```

---

# 부록 B. UI 컴포넌트 의사코드

## B.1 `auth.js`

```js
(function () {
  const TOKEN_KEY = 'imma_token';
  const USER_KEY = 'imma_user';

  let logoutRedirectInProgress = false;

  function getToken() {
    return localStorage.getItem(TOKEN_KEY);
  }

  function getUser() {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY) || 'null');
    } catch (_) {
      return null;
    }
  }

  function saveSession(token, user) {
    if (!token || !user || !user.id || !user.role) {
      throw new Error('Invalid session payload');
    }
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  }

  function decodeJwtPayload(token) {
    try {
      const payload = token.split('.')[1];
      const normalized = payload.replace(/-/g, '+').replace(/_/g, '/');
      return JSON.parse(decodeURIComponent(escape(window.atob(normalized))));
    } catch (_) {
      return null;
    }
  }

  function isTokenExpired(token) {
    const payload = decodeJwtPayload(token);
    if (!payload || !payload.exp) return false;
    return Date.now() >= payload.exp * 1000;
  }

  function clearUserScopedState(userId) {
    const prefixes = [];
    if (userId) prefixes.push(`imma:${userId}:`);

    const keysToRemove = [];
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i);
      if (!key) continue;
      if (prefixes.some(prefix => key.startsWith(prefix))) keysToRemove.push(key);
      if (key.startsWith('immaDemo')) keysToRemove.push(key);
    }
    keysToRemove.forEach(key => localStorage.removeItem(key));
  }

  function logout(options = {}) {
    const currentUser = getUser();
    clearUserScopedState(currentUser && currentUser.id);
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);

    if (options.redirect !== false && !logoutRedirectInProgress) {
      logoutRedirectInProgress = true;
      const reason = options.reason ? `?reason=${encodeURIComponent(options.reason)}` : '';
      window.location.href = `/${reason}`;
    }
  }

  async function verifySession() {
    const token = getToken();
    if (!token) return null;
    if (isTokenExpired(token)) {
      logout({ reason: 'expired' });
      return null;
    }

    const res = await fetch('/api/me', {
      headers: { Authorization: `Bearer ${token}` }
    });

    if (res.status === 401) {
      logout({ reason: 'expired' });
      return null;
    }
    if (!res.ok) {
      throw new Error(`세션 확인 실패: ${res.status}`);
    }
    const user = await res.json();
    localStorage.setItem(USER_KEY, JSON.stringify(user));
    return user;
  }

  function homeForRole(role) {
    if (role === 'buyer') return '/client';
    if (role === 'supplier') return '/supplier';
    if (role === 'admin') return '/admin-ui';
    return '/';
  }

  async function requireRole(allowedRoles, options = {}) {
    const roles = Array.isArray(allowedRoles) ? allowedRoles : [allowedRoles];
    const token = getToken();
    if (!token) {
      window.location.href = '/';
      return null;
    }

    let user;
    try {
      user = await verifySession();
    } catch (err) {
      window.immaUi?.toast?.(err.message || '세션 확인 중 오류가 발생했습니다', 'error');
      return null;
    }

    if (!user) return null;
    if (!roles.includes(user.role)) {
      window.immaUi?.toast?.('접근 권한이 없습니다', 'warning');
      window.location.href = options.fallback || homeForRole(user.role);
      return null;
    }
    return user;
  }

  function scopedKey(userId, ...segments) {
    return ['imma', userId, ...segments].join(':');
  }

  window.immaAuth = {
    getToken,
    getUser,
    saveSession,
    logout,
    verifySession,
    requireRole,
    homeForRole,
    scopedKey,
    isTokenExpired
  };
})();
```

## B.2 `imma-api.js`

```js
(function () {
  let redirecting401 = false;

  function normalizeBody(body, headers) {
    if (body === undefined || body === null) return undefined;
    if (body instanceof FormData) return body;
    if (body instanceof URLSearchParams) return body;
    headers.set('Content-Type', 'application/json');
    return JSON.stringify(body);
  }

  async function parseResponse(res) {
    const contentType = res.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      return res.json();
    }
    const text = await res.text();
    return text ? { message: text } : null;
  }

  function messageFromErrorPayload(payload, fallback) {
    if (!payload) return fallback;
    if (typeof payload === 'string') return payload;
    if (typeof payload.detail === 'string') return payload.detail;
    if (payload.detail && typeof payload.detail.message === 'string') return payload.detail.message;
    if (typeof payload.message === 'string') return payload.message;
    return fallback;
  }

  async function request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const token = window.immaAuth?.getToken?.();
    if (token) headers.set('Authorization', `Bearer ${token}`);

    const controller = new AbortController();
    const timeoutMs = options.timeoutMs || 60000;
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);

    let res;
    try {
      res = await fetch(`${window.IMMA_API_BASE || ''}${path}`, {
        ...options,
        headers,
        body: normalizeBody(options.body, headers),
        signal: controller.signal
      });
    } catch (err) {
      window.clearTimeout(timer);
      if (err.name === 'AbortError') {
        const e = new Error('요청 시간이 초과되었습니다');
        e.status = 0;
        e.code = 'timeout';
        throw e;
      }
      const e = new Error('네트워크 연결을 확인해 주세요');
      e.status = 0;
      e.cause = err;
      throw e;
    }
    window.clearTimeout(timer);

    const payload = await parseResponse(res);
    if (res.status === 401) {
      if (!redirecting401) {
        redirecting401 = true;
        window.immaAuth?.logout?.({ reason: 'unauthorized' });
      }
      const e = new Error('인증이 만료되었습니다');
      e.status = 401;
      e.payload = payload;
      throw e;
    }

    if (!res.ok) {
      const e = new Error(messageFromErrorPayload(payload, `요청 실패 (${res.status})`));
      e.status = res.status;
      e.payload = payload;
      throw e;
    }
    return payload;
  }

  async function apiJson(path, options = {}) {
    return request(path, options);
  }

  async function apiForm(path, formData, options = {}) {
    return request(path, { ...options, method: options.method || 'POST', body: formData });
  }

  window.immaApi = { request, apiJson, apiForm };
})();
```

## B.3 VLM 진행도 컴포넌트

```js
function createVlmProgress(root) {
  const steps = [
    { at: 0, message: '도면 업로드 처리 중' },
    { at: 30, message: 'AI 분석 준비 중 (최초 분석은 시간이 걸립니다)' },
    { at: 90, message: '딥러닝 모델 분석 중' },
    { at: 180, message: '최종 추출 중' },
    { at: 240, message: '분석이 길어지고 있습니다. 실패 시 사전 분석 결과로 자동 전환됩니다' },
    { at: 300, message: 'AI 분석 시간 초과 — 사전 분석 결과로 전환합니다' }
  ];

  let startedAt = 0;
  let timer = null;

  function render(message, elapsed) {
    root.hidden = false;
    root.querySelector('[data-vlm-message]').textContent = message;
    root.querySelector('[data-vlm-elapsed]').textContent = `${elapsed}초`;
  }

  function start() {
    startedAt = Date.now();
    timer = window.setInterval(() => {
      const elapsed = Math.floor((Date.now() - startedAt) / 1000);
      const current = steps.reduce((acc, step) => elapsed >= step.at ? step : acc, steps[0]);
      render(current.message, elapsed);
    }, 1000);
    render(steps[0].message, 0);
  }

  function stop() {
    if (timer) window.clearInterval(timer);
    timer = null;
  }

  function showFallback() {
    stop();
    render('AI 분석 시간 초과 — 사전 분석 결과로 전환합니다', 300);
  }

  return { start, stop, showFallback };
}
```

## B.4 후보 카드 렌더러

```js
const SIGNALS = [
  { prefix: '[INFO_CATEGORY_FALLBACK]', cls: 'info', label: '재질 카테고리 보완' },
  { prefix: '[INFO_PARENT_FALLBACK]', cls: 'info', label: '부모 공정 대체' },
  { prefix: '[WARN_EQUIPMENT_CAPABILITY_MISSING]', cls: 'warning', label: '장비 검증 부족' },
  { prefix: '[공정 달성범위 의심·재질override]', cls: 'warning', label: '재질 override 의심' },
  { prefix: '[공정 달성범위 의심]', cls: 'warning', label: '공정 정밀도 의심' },
  { prefix: '[공정순서 위반]', cls: 'danger', label: '공정순서 위반' },
  { prefix: '[공정순서 권장위반]', cls: 'warning', label: '권장순서 위반' },
  { prefix: '[unsupported]', cls: 'danger', label: '지원 불가' }
];

function classifyReason(reason) {
  const text = String(reason || '');
  return SIGNALS.find(s => text.startsWith(s.prefix)) || { cls: 'neutral', label: '참고' };
}

function renderReason(reason) {
  const { escapeHtml } = window.immaUi;
  const signal = classifyReason(reason);
  return `
    <span class="imma-signal-badge imma-signal-${signal.cls}" title="${escapeHtml(reason)}">
      ${escapeHtml(signal.label)}
    </span>
  `;
}

function renderCandidateCard(cand, context) {
  const { escapeHtml, formatDate } = window.immaUi;
  const selected = context.selectedCompanyId === cand.company_id ? ' is-selected' : '';
  const reasons = Array.isArray(cand.match_reasons) ? cand.match_reasons : [];
  const score = cand.total_score === null || cand.total_score === undefined
    ? '-'
    : `${Math.round(Number(cand.total_score) * 100)}%`;

  return `
    <article class="imma-candidate-card${selected}" data-company-id="${escapeHtml(cand.company_id)}">
      <header>
        <div>
          <strong>${escapeHtml(cand.company_name)}</strong>
          <span>Rank ${escapeHtml(cand.rank_no || '-')}</span>
        </div>
        <button type="button"
          data-select-candidate="true"
          data-company-id="${escapeHtml(cand.company_id)}"
          data-company-name="${escapeHtml(cand.company_name)}"
          data-match-run-id="${escapeHtml(cand.match_run_id)}"
          data-rfq-part-id="${escapeHtml(cand.rfq_part_id)}"
          data-rank-no="${escapeHtml(cand.rank_no)}">
          후보 표시
        </button>
      </header>
      <dl>
        <dt>종합점수</dt><dd>${escapeHtml(score)}</dd>
        <dt>소재 매칭</dt><dd>${escapeHtml(cand.material_match_type || '-')}</dd>
        <dt>정밀도</dt><dd>IT${escapeHtml(cand.best_it_grade || '-')} / Ra ${escapeHtml(cand.best_ra_um || '-')}µm</dd>
        <dt>가용상태</dt><dd>${escapeHtml(cand.overall_status || '-')}</dd>
        <dt>다음 가능일</dt><dd>${escapeHtml(formatDate(cand.next_available_date))}</dd>
        <dt>평점</dt><dd>${escapeHtml(cand.avg_rating || '-')} (${escapeHtml(cand.review_count || 0)}건)</dd>
      </dl>
      <div class="imma-signal-list">
        ${reasons.map(renderReason).join('')}
      </div>
    </article>
  `;
}
```

## B.5 part renderer

```js
function renderPart(part, context) {
  const { escapeHtml } = window.immaUi;
  const partTitle = part.match_input?.part_name || part.part_name || part.rfq_part_id || '부품';

  if (part.status === 'rejected') {
    const reason = part.rejection_reason || part.message || '매칭 불가';
    return `
      <section class="imma-part-section imma-rejected-part">
        <h3>${escapeHtml(partTitle)}</h3>
        <p>${escapeHtml(reason)}</p>
        <div>${renderReason(reason)}</div>
        <p>누락 필드: ${escapeHtml((part.missing_fields || []).join(', ') || '-')}</p>
      </section>
    `;
  }

  const recommended = part.recommended_candidates || [];
  const conditional = part.conditional_candidates || [];

  return `
    <section class="imma-part-section" data-rfq-part-id="${escapeHtml(part.rfq_part_id)}">
      <h3>${escapeHtml(partTitle)}</h3>
      <h4>추천 후보</h4>
      ${recommended.length ? recommended.map(c => renderCandidateCard(c, context)).join('') : '<p>추천 후보가 없습니다.</p>'}
      <details>
        <summary>조건부 후보 ${conditional.length}개</summary>
        ${conditional.map(c => renderCandidateCard(c, context)).join('')}
      </details>
    </section>
  `;
}
```

## B.6 supplier order discovery

```js
async function discoverSupplierOrders() {
  const notifications = await immaApi.apiJson('/api/notifications?unread_only=false');
  const orderIds = [...new Set(
    notifications
      .filter(n => n.event_type === 'order_confirmed')
      .filter(n => n.reference_type === 'order')
      .map(n => n.reference_id)
      .filter(Boolean)
  )];

  const orders = [];
  for (const orderId of orderIds) {
    const order = await immaApi.apiJson(`/api/orders/${encodeURIComponent(orderId)}`);
    orders.push(order);
  }
  return orders;
}
```

---

# 부록 C. 백엔드 추가 변경 명세

## C.1 `routers/matching.py`: match-v2 응답 보강과 fail-fast

### C.1.1 match history 실패 정책

`routers/matching.py:255-260`의 단순 로그 처리를 fail-fast로 바꾼다.

```python
    if engine is not None:
        try:
            _save_match_history(data, result)
        except Exception:
            logger.exception("match history 저장 실패")
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "match_history_save_failed",
                    "message": "매칭 결과를 supplier에게 전송하지 못했습니다. 다시 시도해 주세요.",
                },
            )
```

### C.1.2 candidate 보강

`_save_match_history()`의 score lookup을 `(rfq_part_id, company_id)` 기준으로 바꾸고, top-level `match_run_id`도 넣는다.

핵심 변경안은 다음과 같다.

```python
        # match_run_id를 응답 최상위에 반영
        pipeline_result["match_run_id"] = str(match_run_id)

        # total_score 내림차순 정렬 → rank_no
        scored_candidates.sort(key=lambda x: x["total_score"], reverse=True)

        for rank, sc in enumerate(scored_candidates, 1):
            sc["rank_no"] = rank
            conn.execute(
                text(f"""
                    INSERT INTO {SCHEMA}.match_candidates
                        (match_run_id, company_id, rfq_part_id, hard_filter_pass,
                         technical_score, availability_score, quality_score,
                         total_score, rank_no, explanation_jsonb,
                         supplier_response)
                    VALUES (:mrid, :cid, CAST(:rpid AS uuid), true,
                            :tech, :avail, :qual,
                            :score, :rank,
                            CAST(:explanation AS JSONB), 'pending')
                    ON CONFLICT (match_run_id, company_id, rfq_part_id) DO NOTHING
                """),
                {
                    "mrid": match_run_id,
                    "cid": sc["company_id"],
                    "rpid": sc.get("_rfq_part_id"),
                    "tech": sc["technical_score"],
                    "avail": sc["availability_score"],
                    "qual": sc["quality_score"],
                    "score": sc["total_score"],
                    "rank": rank,
                    "explanation": json.dumps(sc["explanation"], ensure_ascii=False),
                },
            )
```

응답 mutate 부분은 다음처럼 바꾼다.

```python
        score_lookup = {
            (str(sc.get("_rfq_part_id") or ""), str(sc["company_id"])): sc
            for sc in scored_candidates
        }
        company_lookup = {str(sc["company_id"]): sc for sc in scored_candidates}
        allow_single_part_fallback = len(pipeline_result.get("parts", []) or []) == 1

        for part in pipeline_result.get("parts", []):
            part_rpid = str(part.get("rfq_part_id") or "")
            for cand in part.get("candidates", []):
                cid = str(cand.get("company_code") or cand.get("company_id") or "")
                sc = score_lookup.get((part_rpid, cid))
                if sc is None and allow_single_part_fallback:
                    sc = company_lookup.get(cid)
                cand["match_run_id"] = str(match_run_id)
                cand["rfq_part_id"] = part_rpid or None
                if sc:
                    cand["rank_no"] = sc.get("rank_no")
                    cand["technical_score"] = sc["technical_score"]
                    cand["availability_score"] = sc["availability_score"]
                    cand["quality_score"] = sc["quality_score"]
                    cand["total_score"] = sc["total_score"]
                    cand["availability_info"] = sc["availability_info"]
                else:
                    cand["rank_no"] = None
                    cand["availability_score"] = None
                    cand["availability_info"] = None
```

`recommended_candidates`와 `conditional_candidates`가 `candidates` dict reference를 공유하지 않는 코드로 바뀐 경우에는 세 배열 모두에 같은 mutate를 적용해야 한다. 현재 `pipeline/response.py:188-197` 기준으로는 같은 dict reference를 사용한다.

## C.2 `routers/signup.py`: supplier company_name 분리

`routers/signup.py:20-25`에서 `company_name`을 읽는다.

```python
    login_id = data.get("login_id")
    name = data.get("name")  # 담당자명
    company_name = data.get("company_name")
    email = data.get("email")
    password = data.get("password")
    phone = data.get("phone")
    role = data.get("role", "buyer")
```

필수값 검증은 supplier와 buyer를 나눠 처리한다.

```python
    if not login_id or not name or not email or not password:
        raise HTTPException(status_code=400, detail="login_id, name, email, password are required")

    if role == "supplier" and not company_name:
        raise HTTPException(status_code=400, detail="supplier company_name is required")
```

supplier INSERT는 다음처럼 바꾼다.

```python
                        INSERT INTO {SCHEMA}.companies
                            (login_id, company_name, representative_name, main_email, password_hash,
                             main_phone, status, onboarding_status)
                        VALUES (:login_id, :company_name, :representative_name, :email, :pw_hash,
                                :phone, 'active', 'draft')
                        RETURNING company_id, company_name, main_email,
                                  onboarding_status, created_at
```

params는 다음과 같다.

```python
                    {
                        "login_id": login_id,
                        "company_name": company_name,
                        "representative_name": name,
                        "email": email,
                        "pw_hash": pw_hash,
                        "phone": phone,
                    }
```

응답 user에는 담당자명도 포함한다.

```python
                    "user": {
                        "id": str(company_id),
                        "login_id": login_id,
                        "name": name,
                        "company_name": row[1],
                        "email": row[2],
                        "role": "supplier",
                        "onboarding_status": row[3],
                        "created_at": str(row[4]),
                    }
```

## C.3 `routers/rfqs.py`: `/rfqs` status 추가

`routers/rfqs.py:32-45` SELECT에 `r.status`를 추가한다.

```sql
                r.status                    AS status,
```

`GROUP BY`에도 `r.status`를 추가한다.

```sql
            GROUP BY r.rfq_id, r.rfq_no, r.status, r.buyer_id, rp.material_raw_text, rp.quantity,
                     r.requested_delivery_date, r.order_quantity, r.budget_amount,
                     r.budget_currency, r.general_notes_jsonb, r.created_at
```

반환 객체에 status를 넣는다.

```python
        data.append({
            "id": str(row[0]),
            "rfq_no": row[1],
            "status": row[2],
            "buyer_code": str(row[3]) if row[3] else None,
            "material": row[4],
            "process": row[5],
            "quantity": row[6],
            "due_date": str(row[7]) if row[7] else None,
            "order_quantity": row[8],
            "budget_amount": float(row[9]) if row[9] is not None else None,
            "budget_currency": row[10],
            "note": row[11],
            "created_at": str(row[12]),
        })
```

컬럼 index가 바뀌므로 반환 매핑 전체를 함께 수정한다.

## C.4 `routers/deps.py`: JWT_SECRET fail-fast

`routers/deps.py:35` 주변을 다음처럼 바꾼다.

```python
_DEFAULT_JWT_SECRET = "imma-dev-secret"
JWT_SECRET = os.getenv("JWT_SECRET", _DEFAULT_JWT_SECRET)
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24

_ENV = os.getenv("ENV", "local").lower()
_IS_RAILWAY = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"))
_IS_PRODUCTION_LIKE = _ENV in {"production", "prod"} or _IS_RAILWAY

if _IS_PRODUCTION_LIKE and JWT_SECRET == _DEFAULT_JWT_SECRET:
    raise RuntimeError("JWT_SECRET must be set to a non-default value in production/Railway environment")
```

local 개발에서는 기본값을 허용한다. 발표 배포 환경에서는 반드시 env를 넣는다.

## C.5 `pipeline/setup_db.py`: admin seed 확인

`setup_db.py:1053-1068`의 `_seed_admin()`을 그대로 사용한다. bcrypt 코드를 추가하지 않는다. seed 비밀번호는 `_MOCK_PASSWORD_HASH`가 가리키는 PBKDF2 값이어야 한다. 시연 계정은 다음과 같다.

```text
login_id: admin
password: test1234
email: admin@imma.local
role: superadmin
```

시연 후에는 운영 DB에서 비밀번호를 바꾼다. Phase 1 시연 자체는 기존 seed를 그대로 쓴다.

## C.6 선택: admin-only `/api/config/health`

이 endpoint를 추가할 경우 public으로 두지 않는다. 민감한 env 값을 문자열로 반환하지 않고 boolean만 반환한다.

```python
@router.get("/api/config/health")
def config_health(admin: dict = Depends(get_current_admin)):
    return {
        "database_url_set": bool(os.getenv("DATABASE_URL")),
        "jwt_secret_set": bool(os.getenv("JWT_SECRET")),
        "jwt_secret_default": os.getenv("JWT_SECRET", "imma-dev-secret") == "imma-dev-secret",
        "replicate_token_set": bool(os.getenv("REPLICATE_API_TOKEN")),
        "replicate_model_version_set": bool(os.getenv("REPLICATE_MODEL_VERSION")),
        "allowed_origins_set": bool(os.getenv("ALLOWED_ORIGINS")),
    }
```

Phase 1 UI는 이 endpoint를 호출하지 않아도 된다.

---

## 결론

이 계획서 v3는 Phase 1 작업 착수 가능 수준이다. P0 5건은 작업 구조에 반영되었고, P1 10건은 구현 규칙 또는 Phase 2 이관 규칙으로 확정되었으며, P2 5건은 실 API 방해 제거와 운영 안전 기준에 반영되었다. 구현자는 먼저 `site-actions.js` 분리와 공용 `auth.js`/`imma-api.js` 도입을 끝낸 뒤, match-v2 응답 보강과 supplier order discovery를 연결한다. 그 다음 buyer 도면→매칭, supplier 수락→견적, buyer 발주→supplier 주문 수신, admin 승인/반려 순서로 E2E 검증을 진행한다.
