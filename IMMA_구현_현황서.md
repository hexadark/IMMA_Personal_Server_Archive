# IMMA 구현 현황서

---

## 1. 프로젝트 개요

IMMA(Intelligent Manufacturing Matching Agent)는 제조업 발주자가 도면을 업로드하면, VLM이 도면에서 재질/공정/치수/공차를 추출하고, Neo4j 제조 지식 그래프와 PostgreSQL 업체 DB에 대조하여 최적의 가공 업체를 자동 매칭하는 플랫폼이다. 매칭 후 견적-발주-생산 관리-납품-리뷰까지 전체 제조 발주 라이프사이클을 FastAPI 서버가 관리한다. UI는 동일 origin에서 정적 파일로 서빙되며, 21개 HTML이 공용 인증·API·UI 유틸 4종(`auth.js` / `imma-api.js` / `imma-ui-utils.js` / `imma-phase1-pages.js`) 위에서 buyer·supplier·admin 3개 역할 흐름을 실 API와 연결한다.

### 기술 스택

| 구성요소 | 기술 |
|---|---|
| 관계형 DB | PostgreSQL 15+ (imma 스키마, pgcrypto/citext/pg_trgm 확장) |
| 그래프 DB | Neo4j 5.x (493노드/1015관계) |
| 백엔드 서버 | FastAPI (62개 엔드포인트, UI 서빙 21개 라우트) |
| 매칭 파이프라인 | Python (parse → resolve → match → response) |
| GraphRAG 변환 | LangGraph ReAct Agent + Gemini 3 Flash Preview (thinking_level=low) |
| VLM | Replicate API (이미지 → V.B raw JSON) |
| 도구 | Neo4j Cypher 직접 쿼리 4개 @tool |
| 프론트엔드 공용 JS | `imma-ui-utils.js` (toast/loading/패널 렌더), `auth.js` (세션·로그인·role guard), `imma-api.js` (Authorization 자동 주입 fetch), `imma-phase1-pages.js` (페이지별 실 API 라우팅), `admin-menu.js` (admin 3개 페이지 사이드바 + Demo UI 배지) |

---

## 2. 파이프라인 아키텍처

### 전체 흐름

```
도면 이미지 → /vlm/analyze-upload (Replicate) → drawings.vlm_result_jsonb
    → /api/match-v2 (drawing_id) → graphrag_transform.py (LangGraph + Neo4j 4개 도구)
        → 구조화된 JSON (parts[])
            → parse.py → resolve.py → match.py → response.py
                → match_runs / match_candidates 영구 저장 + 알림 발송
```

### 모듈별 역할

| 파일 | 역할 |
|---|---|
| `pipeline_runner.py` | 진입점. DB 저장 → parse → resolve → match → response 체이닝. client_quantity fallback — 단부품 RFQ(`len(vlm_parts) == 1`)이며 `vp.quantity == 1` 인 경우에 한해 `raw["order_quantity"]` 입력값으로 덮어씀. 다부품 RFQ는 VLM quantity 보존 |
| `parse.py` | JSON → VlmPart 변환. null/타입 방어, ±IT 환산, ▽→Ra fallback, 나사 오탐 방지 |
| `resolve.py` | 재질 7단계 해소(코드→alias→규격접미사→템퍼접미사→카테고리텍스트→LIKE→regex), 일반공차 fallback, 형상 분류, 호환성/피드백 검증 |
| `match.py` | SQL 하드필터, 장비 검증, 공정 순서 검증. 카테고리 확장, parent fallback 안전 제한. 외주 공정(FAIL_OPEN_PROCESSES)은 하드필터 SQL에서 제외하여 업체 외주망에 위임. `_populate_equipment_summary`가 후보 리스트에 카테고리별 보유 장비 수 + 대표 모델을 부착 (`equipment` JOIN `equipment_category_catalog` LEFT JOIN `equipment_model_catalog`, status IN ('running','idle'), year_made DESC로 대표 모델 선정) |
| `response.py` | 매칭 결과 JSON 조립. ontology_warnings 합류. 사내 공정과 외주 공정을 분리 표시. 후보 객체에 `equipment_summary` 필드 전달 |
| `lookup.py` | 정적 지식 단일 원천. STAGE_TO_CODES 54항목, PRECISION 11개, INTERMEDIATE 12개, NON_MACHINING 11개, FAIL_OPEN_PROCESSES 8개, CATEGORY_TEXT_TO_CODE 51항목, PROC_NORMALIZE 5항목, `SAFE_PARENT_FALLBACK={turning_rough, turning_finish, milling_rough, milling_finish}` |
| `config.py` (pipeline) | DB 접속 정보, 룩업 JSON 경로, 환경변수 (DATABASE_URL 우선 + 개별 변수 fallback) |
| `db.py` | psycopg2 단건 연결, 트랜잭션 컨텍스트매니저, 쿼리 헬퍼 (커넥션 풀은 routers/deps.py의 SQLAlchemy engine) |
| `setup_db.py` | DDL 실행, seed 데이터 (재질 68개 + alias + mock 업체 19개 + admin/buyer/supplier seed 계정), MV 갱신 |
| `graphrag_transform.py` | VLM raw → 스키마 변환. Gemini 3 Flash Preview, thinking_level=low, temperature=1.0, timeout=120s. `routers/matching.py`가 `/api/match-v2`에 `drawing_id`만 들어오면 `drawings.vlm_result_jsonb`를 읽어 `transform_vlm_raw`를 호출하여 자동 변환. SYSTEM_PROMPT 항목 17·18로 part_name 추출 우선순위(title_block → 본문 → 빈 문자열, view.name 제외)와 원문 보존 정책(part_name·material.raw_text·referenced_standards 원문 유지, post_treatment 만 한국어) 명시 |
| `routers/vlm.py` | `/vlm/analyze-upload`: buyer 인증 + multipart 이미지 → Replicate VLM API 호출 → V.B raw JSON → `drawings`에 INSERT(file_sha256 + buyer_id) → drawing_id 반환. 동기 `def` 정의로 FastAPI thread pool 위탁. Replicate sync requests + time.sleep 영역이 async event loop 를 freeze 시키지 않도록 `image.file.read()` 동기 영역 사용 |
| `routers/config.py` | `/api/config/health` admin 전용. `DATABASE_URL`·`JWT_SECRET`·`REPLICATE_API_TOKEN`·`REPLICATE_MODEL_VERSION`·`GEMINI_API_KEY`·`NEO4J_URI` 설정 여부(boolean)와 `jwt_secret_is_default` 플래그만 반환. 민감정보 값은 노출하지 않음 |
| `routers/deps.py` | 공통 DB engine / SCHEMA / JWT 유틸 / 인증 의존성. `JWT_SECRET=imma-dev-secret` 기본값을 사용한 상태에서 `ENV` 또는 `RAILWAY_ENVIRONMENT`가 production을 가리키거나 `RAILWAY_PROJECT_ID`가 설정되어 있으면 startup 시점에 `RuntimeError`로 fail-fast |
| `models.py` | VlmPart(unsupported 포함), ResolvedPart(ontology_warnings 포함), MatchCandidate, MatchResponse |

### 프론트엔드 공용 JS

| 파일 | 역할 |
|---|---|
| `machhub_ui/imma-ui-utils.js` | toast 스택, `setLoading`, `escapeHtml`, `formatCurrency`/`formatDate`, `getQueryParam`, role label·display name 헬퍼. `renderSessionHeader`는 로그인 상태일 때 기존 헤더의 `.btn-login`/`.btn-signup` CTA에 `display:none`을 부여하여 세션 박스와의 중복 노출을 차단한다. 별도 패널 생성(`ensurePanel`/`setPanelContent`)은 제거되어 있다 |
| `machhub_ui/auth.js` | `imma_access_token` / `imma_user` localStorage 관리, UTF-8 안전 JWT payload decode, `getUser`·`setSession`·`clearSession`, user-scoped key 생성기(`scopedKey`), `redirectForRole`, single-flight `verifySession()` (`/api/me` 2차 검증), `requireRole`/`requireAdmin`, `login()` (buyer/supplier/admin 분기 endpoint) |
| `machhub_ui/imma-api.js` | `fetchRaw`/`apiJson`/`apiForm` wrapper. JWT가 있으면 `Authorization: Bearer` 자동 주입, 네트워크 오류는 `NETWORK_ERROR` 코드로 격리, 401 응답은 single-flight `imma.logout('unauthorized')`로 redirect |
| `machhub_ui/imma-phase1-pages.js` | path 기반 라우터. landing 로그인, buyer/supplier 가입, buyer 대시보드·견적 요청·매칭·발주 관리, supplier 대시보드·작업대·설정·RFQ 상세, admin 대시보드·업체 검수 페이지를 각각 실 API 흐름으로 초기화한다. 각 페이지 route별 init 함수가 *기존 디자인 HTML의 form/button/table/stat DOM을 직접 조회하여 실 API 결과를 hydrate*하며, 별도 패널을 prepend 하지 않는다. VLM 진행도 단계(0/30/90/180/240/300초)·504/502/timeout fixture fallback·notifications 기반 supplier order 발견 흐름도 여기에 모인다 |
| `machhub_ui/admin-menu.js` | admin-dashboard/admin-control-center/admin-operations 사이드바를 렌더. 메뉴 하단에 "Demo UI · 일부 데이터는 시연용 샘플입니다" 배지를 출력 |

### GraphRAG 도구

| 도구 | 역할 |
|---|---|
| `lookup_material` | 재질명 → alias/코드/fuzzy 3단계 매칭 (toUpper case-insensitive) |
| `lookup_compatibility` | 카테고리 → 호환 공정 조회 (unsuitable 포함 전체 반환) |
| `lookup_sequence` | MUST_PRECEDE/RECOMMENDED_BEFORE 순서 규칙 조회 |
| `lookup_tolerance` | 공정별 IT/Ra 달성 범위 조회 |

### 프롬프트 제약

- 허용 공정 34개 enum, 허용 카테고리 14개 한글명 (other 제외, stainless_cast_steel 포함)
- 재질분류 힌트 (POM→플라스틱, SUM24L→쾌삭강, FCD→구상흑연주철, FR-4→복합재 등)
- outer_diameter/hole_diameter 분리, ▽→Ra 변환, post_treatment 한글 필수
- 비전도성 재질 EDM 차단, GDT는 required_processes에 미추가
- unsupported: 허용 목록 밖 재질/공정 → unsupported=true + 도구 조회 근거 필수
- part_name 추출 우선순위 (항목 17): title_block 의 Part_Name/Part_Title/Description 최우선 → 본문 최상위 부품명 라벨 → 빈 문자열. view.name(예: "C部詳細", "正面図", "Section A-A")은 부분 상세 뷰 명칭이므로 part_name 으로 채택 금지. "(추정)" 부착 금지
- 원문 보존 정책 (항목 18): part_name·material.raw_text·referenced_standards 는 도면 원문 그대로 유지(자의적 한국어 번역 금지). post_treatment 만 항목 9·9-1에 따라 한국어로 작성

### E2E 성능

10건 전부 정상. 평균 10.5초, 최대 22.6초. 실 VLM 경로는 Replicate cold start 영향으로 추가 지연이 발생하며, UI는 0/30/90/180/240/300초 6단계 진행도와 504/timeout fixture fallback으로 이를 흡수한다.

---

## 3. 온톨로지 검증 계층

ontology_warnings 리스트에 경고를 누적하여 최종 응답의 warnings에 합류. is_valid 판정에는 영향 없음(정보성 경고). UI 측에서는 `imma-phase1-pages.js`의 `classifyReason`/`cleanReason`/`renderReason`이 `[INFO_*]`·`[WARN_*]`·`[공정순서 위반]`·`[unsupported]` 신호를 색상 칩으로 시각화한다.

### 3.1 재질-공정 호환성 검증

`_check_material_process_compatibility()` — category_code + required_processes → MATERIAL_PROCESS_COMPATIBILITY 224행/14카테고리 대조. limited 시 경고.

### 3.2 공정 순서 검증

`check_process_sequence()` — required_processes + post_treatment → PROCESS_SEQUENCE_CONSTRAINTS 22규칙 대조.
- absolute_rule 9행, recommended 11행, cannot_run_concurrently 2행(비활성화)
- stock_preparation 스킵, applies_to 조건 필터링, 빈 set 규칙 스킵

### 3.3 도면 피드백

`_check_drawing_feedback()` — 7개 규칙:
1. IT≤6 + 정밀 공정 부재
2. Ra≤0.4 + 호닝/래핑 부재
3. 외형 치수 전부 미추출
4. turning + 외경 미추출
5. 열처리 + 경도 미기재
6. GDT + IT/Ra 미추출
7. 공정별 달성 공차 사전경고 (정밀 공정 존재 시 억제)

---

## 4. Neo4j 지식 그래프

### 스키마

| 노드 | 수 | 주요 속성 |
|------|---|----------|
| MaterialCategory | 15 | code, name_ko |
| Material | 68 | code, name_ko, jis_code, 물성(tensile/yield/hardness/corrosion 등) |
| MaterialAlias | 270 | text |
| Process | 81 | code, name_ko, IT/Ra 범위 |
| EquipmentModel | 59 | model_id, manufacturer, category_code |

| 관계 | 수 | 방향 |
|------|---|------|
| ALIAS_OF | 276 | MaterialAlias → Material |
| COMPATIBLE_WITH | 224 | MaterialCategory → Process |
| ALTERNATIVE_TO | 181 | Material → Material |
| CAPABLE_OF | 108 | EquipmentModel → Process |
| INCLUDES | 101 | Process(추상) → Process(구체) |
| BELONGS_TO | 68 | Material → MaterialCategory |
| RECOMMENDED_BEFORE | 32 | Process → Process |
| CHILD_OF | 14 | Process → Process |
| MUST_PRECEDE | 9 | Process → Process |
| CANNOT_RUN_CONCURRENTLY | 2 | Process → Process |

전체: **493노드, 1015관계**

시드: `pipeline/seed_neo4j.py` (환경변수 NEO4J_URI 대응, 기본값 localhost:7687)

---

## 5. 서버 (FastAPI)

### 구조

```
fas_analysis/
├── main.py              ← FastAPI app + UI 라우트 21개 + CORS 화이트리스트
├── machhub_ui/          ← 프론트엔드 정적 파일 (HTML 21 + 공용 JS 5 + CSS 2 + intro 영상)
├── pipeline/            ← 매칭 파이프라인 .py (db, config, lookup, models, parse, resolve, match, response, pipeline_runner, graphrag_transform, setup_db, seed_neo4j)
├── routers/             ← API 라우터 16개 .py (auth, signup, companies, rfqs, matching, orders, drawings, quotes, reviews, notifications, admin, catalog, vlm, config + deps)
├── lookup_tables/       ← schema.sql + lookup_data.json + equipment_catalog.json
└── requirements.txt
```

### 엔드포인트 (63개)

| 카테고리 | 수 | 핵심 |
|---|---|---|
| 인증/가입 | 4 | `/api/login`, `/api/me`, `/signup`, `/api/check-login-id` |
| 업체 관리 | 15 | 온보딩, 장비/재질/공정, 스케줄 |
| RFQ | 4 | 목록, 단건 조회, 상태 전이, 보완 입력 (POST /rfq 폐기됨. RFQ 생성은 `/api/match-v2`로 통합) |
| 매칭 | 4 | `/match/{rfq_id}` (v1), `/api/match-v2` (v2), `/api/company/matches`, `/api/match-candidates/.../respond` |
| 발주/생산 | 11 | 상태 전이, 검수, 납품 |
| 견적/리뷰 | 4 | 견적 제출, 리뷰 |
| 알림 | 3 | 조회, 읽음 |
| 관리자 | 6 | `/api/admin/login`, pending 목록, verify/reject, admin RFQ/orders 목록 |
| 카탈로그 | 6 | 공정/재질/장비 목록 |
| 도면 | 4 | 업로드, 다운로드 |
| VLM | 1 | `/vlm/analyze-upload` Replicate VLM 분석 |
| 운영 설정 | 1 | `/api/config/health` admin 전용. 환경변수 set 여부(boolean) + `jwt_secret_is_default` 플래그 |
| 헬스체크 | 1 | `/api/health` |

### 인증

login_id 기반 JWT(HS256, 24시간). `routers/deps.py`의 `_create_token` payload는 `{sub, login_id, role, exp}`. `JWT_SECRET`이 기본값 `imma-dev-secret`이면서 `ENV in {production, prod}`이거나 `RAILWAY_ENVIRONMENT` / `RAILWAY_PROJECT_ID`가 설정된 환경에서는 `routers/deps.py`가 import 시점에 `RuntimeError`를 던져 서버 startup이 실패한다. CORS 기본값은 `http://localhost:8000,http://127.0.0.1:8000`이며, 배포 도메인은 `ALLOWED_ORIGINS` 환경변수로 명시 주입한다 (외부 더미 백엔드 도메인은 기본값에서 제외).

#### `/api/login`

buyers → companies 순차 조회. buyer 일치 시 `name=buyer_name`, `company_name=company_name`이 응답 user에 포함. supplier 일치 시 `name=primary contact name` 또는 `company_name` fallback, `company_name=companies.company_name`, `contact_name=primary contact`가 포함. 응답 shape:

```json
{
  "access_token": "...",
  "token_type": "bearer",
  "user": { "id": "...", "login_id": "...", "role": "buyer|supplier", "name": "...", "company_name": "...", "contact_name": "..." }
}
```

#### `/api/admin/login`

`routers/admin.py`. admins 테이블에서 login_id 조회 → `_verify_password` → role=`admin` JWT 발급. 응답 user에 `name`과 `admin_role`(DB 컬럼) 포함. UI guard는 토큰 payload의 `admin`을 사용한다.

#### `/api/me`

JWT 검증 후 buyer/supplier/admin 각각에 맞춰 DB를 다시 읽어 `{id, login_id, role, name, company_name, contact_name}`을 반환한다. buyer는 `buyers.buyer_name/company_name`, supplier는 `companies.company_name`과 primary `company_contacts.contact_name`, admin은 `admins.name`을 사용한다. UI는 localStorage user를 1차 캐시로 두고, 보호 페이지에서는 `auth.js`의 single-flight `verifySession()`이 `/api/me`로 2차 검증한다.

#### `/signup`

buyer는 `login_id, name, email, password` 필수, 결과는 `buyers` 단일 INSERT. supplier는 `login_id, name, company_name, email, password` 필수이며 `name`(담당자명)과 `company_name`(회사명)을 분리 소비한다. `companies`에 `onboarding_status='draft'`로 INSERT, 동일 트랜잭션에서 `company_contacts`에 primary contact(contact_name=`name`, role_title=`가입 담당자`, is_primary=true, receives_rfq=true)를 추가하고, `company_availability_snapshot`에 `overall_status='available'` 행을 보장한다. 응답에는 토큰이 없으며 UI는 즉시 `/api/login`을 다시 호출한 뒤 `/supplier-settings#onboarding` 으로 redirect 한다. supplier 가 §5.4 온보딩 4 카드 영역(장비/재질/공정/사업자)을 모두 충족하면 `_check_onboarding`이 `submitted`로 자동 전환, 이후 admin verify 단계로 진입한다.

#### `/api/check-login-id`

`routers/signup.py`. 가입 전 ID 중복 검사. `login_id` query 단일 파라미터(min_length=1, max_length=64), 인증 부재. `buyers` + `companies` 두 테이블을 UNION ALL 로 조회하여 일치 시 `{"available": false, "reason": "이미 사용 중인 ID 입니다"}`, 미존재 시 `{"available": true}` 반환. 4 자 미만 입력은 서버단에서 `{"available": false, "reason": "ID 는 4 자 이상이어야 합니다"}` 반환. UI 는 `client-register.html`·`supplier-register.html` 의 *중복 확인* 버튼이 이 endpoint 호출.

### RFQ·매칭·견적 응답

- `GET /rfqs` (`routers/rfqs.py`) — buyer 본인 RFQ 목록. 응답 행에 `status`, `rfq_no`, `order_quantity`, `budget_amount`, `budget_currency` 포함.
- `POST /api/match-v2` — buyer 또는 admin만 호출 가능. `drawing_id` 전달 시 도면 소유권 검증 + `parts` 미제공 시 `vlm_result_jsonb`를 `transform_vlm_raw`로 자동 변환한다. 파이프라인 실행 후 `_save_match_history()`로 `match_runs` 1행과 `match_candidates` N행을 저장하고, supplier에게 `match_request`, buyer에게 `match_completed` 알림을 발송한다. 저장 실패 시 라우터는 500으로 fail-fast 응답한다("매칭 결과 저장 또는 supplier 전송에 실패했습니다. 다시 실행해 주세요."). 응답 후보 객체는 mutate되어 `match_run_id`, `rank_no`, `rfq_part_id`, `technical_score`, `availability_score`, `quality_score`, `total_score`, `availability_info`, `equipment_summary`를 포함한다. `equipment_summary`는 `[{"category_code", "category_name_ko", "count", "representative_model"}]` 배열로 후보 업체의 장비 카테고리별 보유 수 + 대표 모델을 전달한다. `score_lookup` 키는 `(company_id, rfq_part_id)` 복합으로 구성되며, 단부품 결과에 한해 `(company_id, '')`로의 fallback이 허용된다.
- `PUT /api/match-candidates/{match_run_id}/{company_id}/respond` — supplier만 호출 가능. JWT의 company_id와 path의 company_id 일치 검증. 응답 후 buyer에게 `supplier_accepted` 또는 `supplier_declined` 알림 발송.
- `POST /api/quote` — supplier만 호출 가능. `match_candidates.supplier_response='accepted'` 검증 후 견적 INSERT. 첫 견적 도착 시 RFQ `open → quoted` 자동 전이. buyer에게 `quote_received` 알림.
- `GET /api/rfq/{rfq_id}/quotes` — buyer/supplier/admin 역할별 분기. buyer는 본인 RFQ 전체 견적, supplier는 본인 견적만, admin은 전체 견적. line_items는 Phase 1 응답에서 제외(Phase 2 영역).

### admin 검수

- `GET /api/admin/companies/pending` — `companies WHERE onboarding_status = 'submitted'` 단일 조건. `verified` 는 승인 완료 영역이므로 pending 목록에서 제외. `company_sites` LEFT JOIN 으로 primary site region 동봉.
- `PUT /api/admin/companies/{company_id}/verify` — 현재 `onboarding_status` 검사 후 `submitted` 일 때만 `verified` 전환 허용. 그 외 상태는 400 응답과 상태별 메시지 분기: `verified` → "이미 승인된 업체입니다", `draft` → "온보딩 미완료 — supplier 가 정보 입력 영역 진행 중", `rejected` → "이미 반려된 업체. 반려 사유 확인 후 재신청 영역".

### 서버↔파이프라인 연동

`routers/matching.py` → `pipeline_runner.py` import → `run_pipeline_from_dict()` 호출 → 매칭 결과 반환 + `_save_match_history()`로 이력/스코어/알림 저장 + 후보 mutate로 supplier 응답 endpoint와 연결할 키(`match_run_id`, `rfq_part_id`, `rank_no`)를 buyer 화면에 함께 내려보낸다.

---

## 6. DB 스키마

### 테이블 (39개)

| 구분 | 테이블 |
|------|--------|
| 카탈로그 (7) | process_catalog, material_category_catalog, materials, material_aliases, equipment_category_catalog, equipment_model_catalog, certification_catalog |
| 업체 (3) | companies, company_sites, company_contacts |
| 역량 (5) | company_material_capabilities, company_process_capabilities, company_material_process_capabilities, equipment, equipment_process_capabilities |
| 가용성 (3) | company_availability_snapshot, company_capacity_calendar, equipment_daily_schedule |
| 발주 (10) | buyers, drawings, rfqs, rfq_parts, rfq_part_processes, quote_responses, quote_line_items, orders, manufacturing_jobs, job_processes |
| 평가 (4) | reviews, company_certifications, company_partners, company_partner_services |
| 매칭 이력 (3) | match_runs, match_candidates, ontology_sync_refs |
| 운영 (4) | notifications, admins, shipments, delivery_images |

### MV

`company_capability_summary` — 하드필터 대상. verified + accepting_orders 업체만. expanded_proc CTE로 process_catalog의 parent-child 관계를 자식→부모 방향으로 확장하여 process_codes에 반영 (예: cylindrical_grinding 보유 → grinding도 포함). 업체 원본 데이터(company_process_capabilities)는 순수 유지하고 MV 계산 시점에만 확장.

### 주요 테이블 상태

- `rfqs`: `order_quantity`(int), `budget_amount`(numeric), `budget_currency`(char(3) default 'KRW') 보유. `quote_due_at` 미사용.
- `rfqs.rfq_no`: `generate_rfq_no()` BEFORE INSERT 트리거가 `RFQ-YYYYMMDD-NNNN` 형식으로 자동 채움.
- `drawings`: `buyer_id`, `file_sha256`, `vlm_result_jsonb` 정합. `vlm_model`, `vlm_model_version`, `extraction_confidence` 미사용 컬럼은 제거된 상태.
- `match_candidates`: `technical_score`, `availability_score`, `quality_score`, `total_score`, `rank_no`, `explanation_jsonb`, `supplier_response` 사용. `price_score`, `vector_similarity_score`, `ontology_score`는 제거된 상태. PK는 `(match_run_id, company_id, rfq_part_id)` 3컬럼.

### 데이터 규모

| 항목 | 수 |
|------|---|
| 재질 카테고리 | 15 |
| 재질 | 68 |
| 재질 별칭 (RDBMS material_aliases) | 219 |
| 재질 별칭 (Neo4j ALIAS_OF) | 276 |
| 공정 | 34 |
| 호환성 매트릭스 | 224행 (14카테고리 × 16공정) |
| 물성 데이터 | 68개 |
| 순서 규칙 | 22 |
| 장비 모델 | 59 |
| 장비 카테고리 | 22 |
| IT/Ra 보정 (override) | 18행 (플라스틱/복합재 재질별) |
| 재질-공정 결합 (CMPC) | 350행 (비가공 공정 제외) |
| mock 업체 | 19 |
| equipment_daily_schedule 시드 | 6120행 (90일) |
| seed 계정 | admin / test1234, buyer `kim_cheolsu` / demo1234, mock supplier 19개 |
| 시연 fixture 도면 | `v_b_export_samples/sample_00015` (S45C 펌프) — `drawings` 테이블에 사전 INSERT, `buyer_id = kim_cheolsu`. VLM 504/timeout 시 UI가 fallback으로 사용 |

---

## 7. 설계 결정

| 항목 | 결정 | 근거 |
|---|---|---|
| GDT 매칭 | 하드필터 미반영 | false negative 위험. 업체 CMM/검사 데이터 없이 매핑 불가. 향후 소프트 시그널로 |
| 카테고리 fallback | (1) free_cutting_steel → carbon_steel: 완전 inclusion (피삭성 더 좋음). (2) stainless_cast_steel → stainless_steel, cast_steel → carbon_steel: 부분 inclusion. 부분 inclusion 경우 응답 reasons에 `[INFO_CATEGORY_FALLBACK]` 신호로 *주물 결함(기공/표피/개재물) 대응 노하우 별도 확인 필요* 사실 전달 | 가공 능력이 명확히 포괄되는 영역에 한해 fallback. 주물 결함 대응은 단조·압연 가공 노하우와 별개라 신호로 식별 |
| parent fallback (공정 자식→부모) | `SAFE_PARENT_FALLBACK` 화이트리스트 = {turning_rough, turning_finish, milling_rough, milling_finish}. SQL 하드필터(`build_hard_filter_sql`), equipment_verification 두 루프(precision/intermediate), `_compute_availability_score` SQL 세 곳에 일관 적용. 부모로만 매칭된 경우 응답 reasons에 `[INFO_PARENT_FALLBACK]` 신호 부착 | gear_grinding·honing·lapping·cylindrical_grinding 등 grinding 가족 자식은 전용 장비(기어연삭기/호닝머신/래핑머신/원통연삭기)가 분리되어 일반 grinder로 대체 불가. 화이트리스트는 *동일 장비*가 황·정삭을 모두 수행하는 영역에 한정 |
| 비전도성 EDM | 프롬프트 차단 | 플라스틱/복합재에 EDM 배정 금지 |
| unsupported 표시 | rejection_reason 분리 | ontology_warnings의 `[unsupported]` 메시지를 rejection_reason에 반영. UI는 part renderer에서 danger 칩으로 표시 |
| 외주 공정 하드필터 제외 | FAIL_OPEN_PROCESSES(heat_treatment/surface_treatment/casting/welding 등 8개)를 SQL AND 조건에서 제외. 장비 검증 fail-open과 동일 정책을 SQL 단계에도 적용 | 단일 업체 매칭 모델에서 외주 공정 처리는 업체 외주망에 위임. 발주자→업체 일괄 발주 구조에서 외주 가능 여부는 업체가 견적 시점에 판단 |
| MV 자식→부모 확장 | process_catalog의 parent_process_code를 MV CTE에서 자식→부모 방향으로 확장. company_process_capabilities 원본은 순수 유지 | parent-child 관계를 각 레이어(SQL/장비검증/seed)에서 제각각 처리하던 비대칭을 MV 단일 지점으로 해소 |
| mock 데이터 온보딩 정합 | setup_db.py `_insert_company`가 온보딩 API(장비 등록→catalog 자동 매핑→capability 병합)와 동일 흐름을 따름. parent 자동 추가 같은 API 미존재 로직은 제거 | mock ↔ 실제 온보딩 데이터 구조 일치 보장. 장비 카탈로그 59개 모델을 19개 업체에 배분하여 catalog 기반 자동 매핑 검증 |
| 플라스틱/복합재 IT/Ra override | MATERIAL_PROCESS_CAPABILITY_OVERRIDES 18행으로 금속 기준 대신 재질별 보정값 적용 | 금속 기반 공정 공차 기준을 비금속에 그대로 적용하면 달성 불가 판정이 발생 |
| company_material_process_capabilities 결합 테이블 | DDL 완성 + seed 350행 (비가공 공정 제외). match.py 쿼리 연결은 향후 | 재질×공정×업체 3차원 역량을 단일 테이블로 조회하여 매칭 정밀도 향상 |
| VLM 통합 (Replicate + drawing_id 자동 GraphRAG) | `/vlm/analyze-upload`가 Replicate VLM 호출 후 V.B raw JSON을 `drawings`에 저장하고 `drawing_id` 반환. `/api/match-v2`는 body에 `drawing_id`만 있으면 `drawings.vlm_result_jsonb`를 읽어 `transform_vlm_raw`를 자동 호출. 발주자가 parts를 직접 보내면 GraphRAG 생략 | 도면 업로드와 매칭 호출을 분리하면서도 단일 흐름으로 연결. 외부 의존(Replicate) 차단 시에도 CLI/테스트 경로 보존 |
| VLM cold start fixture fallback | UI는 `quote-request.html`에서 0/30/90/180/240/300초 6단계 진행도 메시지를 표시한다. 응답이 504 또는 NETWORK_ERROR로 떨어지면 `imma-phase1-pages.js`의 `createVlmProgress.showFallback()`이 "사전 분석 결과로 계속" + "다시 시도" 버튼을 제공한다. fallback 선택 시 `v_b_export_samples/sample_00015` 도면 ID로 `/api/match-v2`를 호출하며, `general_notes.vlm_fallback_used=true`를 함께 전송한다. 502는 toast 후 사용자 선택, 504/timeout은 fallback UI를 즉시 노출 | Replicate cold start로 인한 발표 시연 리스크를 사용자가 인지 가능한 형태로 흡수. fallback 사실은 RFQ 메모에 영구 기록 |
| 가용성 점수 정밀화 | `_compute_availability_score`가 (1) FAIL_OPEN_PROCESSES 공정 lead 분리, (2) 사내 공정 가능 장비 풀(EPC, 자기/자식 무차별 + `SAFE_PARENT_FALLBACK` 화이트리스트 한정 부모 fallback + EXISTS 중복 제거)로 시간합 계산, (3) `equipment_daily_schedule` 시드 한계 외 납기는 0.7 폴백, (4) 전외주 RFQ 0.9, (5) 신고만 장비 0대 0.3 | 부품 사양 매칭과 별개로 *실제 가용 캐파*가 납기를 충족하는지 계산. 시드 한계 인지 + 외주 공정 비교 대상 제외 + 부모 fallback 정책을 매칭 단계와 일관 |
| `_save_match_history()` fail-fast | 저장 실패 시 `/api/match-v2`가 500으로 응답하고 "매칭 결과 저장 또는 supplier 전송에 실패했습니다. 다시 실행해 주세요."를 내려보낸다. 부분 저장 후 sigh-and-continue 금지 | match 이력과 supplier 알림이 buyer 응답과 결합되어 있어, 부분 실패 시 buyer는 결과를 보지만 supplier가 알림을 받지 못하는 비대칭이 발생. fail-fast로 정합성 강제 |
| 매칭 응답 구조 보강 | 후보 객체에 `match_run_id`, `rank_no`, `rfq_part_id`, `technical_score`, `availability_score`, `quality_score`, `total_score`, `availability_info`를 mutate로 부착. `score_lookup` 키는 `(company_id, rfq_part_id)` 복합, 단부품(part 1개) 결과에 한해 `(company_id, '')` fallback 허용 | buyer 화면이 supplier 응답 endpoint(`/api/match-candidates/{match_run_id}/{company_id}/respond`)와 견적 비교 화면에 동일 객체를 그대로 전달. 다부품 RFQ에서 score 혼선 방지 |
| 보안 정책 (CORS·인증·3-tier 게이팅·JWT_SECRET fail-fast) | (1) CORS 화이트리스트 + credentials=False, 기본값에서 외부 더미 백엔드 도메인 제거. (2) 모든 변형 엔드포인트 role/소유권 검증. (3) `GET /api/company/{id}` 3-tier 게이팅: admin·본인supplier·관계형성된buyer만 BRN/연락처/대표자명/contacts 노출 (관계 = 매칭 accepted 또는 orders 존재). (4) `/companies/buyers` admin-only, 가동률·스케줄 엔드포인트 본인supplier+admin만. (5) `JWT_SECRET=imma-dev-secret`을 production 또는 Railway 배포 환경에서 사용 시 `routers/deps.py`가 startup 시점에 `RuntimeError`로 fail-fast | B2B 마켓플레이스 표준. 익명에는 공개 정보만, 인증 사용자에는 기본 정보, 관계 형성된 buyer에게만 민감 연락 정보. 운영 환경에 dev secret 잔존을 차단 |
| realmode 플래그 + 활성 공용 JS 한정 | 21개 실 페이지 HTML 공통 head에 `window.__imma_realmode__ = true;`를 선언하고 `imma-ui-utils.js` → `auth.js` → `imma-api.js` 순서로 공용 JS를 로드한다. 각 페이지는 body 끝부분에서 `imma-phase1-pages.js`를 로드한다. admin-dashboard·admin-control-center·admin-operations만 추가로 `admin-menu.js`를 로드한다. 비활성 데모 JS(`site-actions.js`/`client.js`/`supplier.js`/`app.js`/`app_unified.js`/`shared-state.js`)와 비활성 CSS(`app_unified.css`/`client.css`/`supplier.css`/`style.css`/`styles.css`)는 디렉토리에서 제거된 상태 | 전역 submit/click intercept·scenario 텍스트 치환·header rewrite·`/client-fulfillment#ai` redirect·`immaDemo*` localStorage 생성이 실 API 흐름을 가로채는 위험을 원천 차단. 활성 코드와 비활성 코드의 *물리적 공존 자체*를 제거하여 컨텍스트 노이즈와 회귀 위험을 동시 해소 |
| 디자인 DOM 직접 hook 방식 | `imma-phase1-pages.js`의 21 페이지 route별 init 함수가 *기존 디자인의 form/button/table/stat DOM을 직접 조회하여 실 API 결과를 hydrate*한다. `imma-ui-utils.js`의 별도 패널 생성 함수(`ensurePanel`/`setPanelContent`)는 제거되어 있으며, 기존 디자인 위에 `.imma-phase1-panel` 회색 패널을 prepend 하지 않는다. `supplier-dashboard.html`/`supplier-rfq-detail.html`은 시작 `<body>` 태그를 보강하여 DOM 파싱 정합을 보장하며, `.dash-sidebar/.dash-nav/.dash-user`·`.mw-side-user`에 `flex-shrink:0` + `overflow` 정합을 부여하여 supplier 4 페이지 사이드바 nav 영역이 잘리지 않도록 한다. 로그인 상태에서는 `imma-ui-utils.js`의 `renderSessionHeader`가 기존 헤더의 `.btn-login`/`.btn-signup` CTA에 `display:none`을 부여하여 세션 박스와의 중복 노출을 차단한다. supplier-rfq-detail 부품 표는 `material_raw_text`/`tightest_tolerance_mm`/`tightest_it_grade` 필드를 사용하며, `surface_treatment` 컬럼은 `/api/rfq` 응답에 부재하므로 기존 데모 값을 그대로 유지한다 | 별도 패널 prepend가 팀원 B 디자인 위에 회색 패널을 덧씌우는 부작용을 발생시키므로, 실 API 결과는 기존 디자인 슬롯에 직접 주입하는 방식으로 수렴. DOM 파싱 정합과 헤더 CTA 중복은 가시적 시각 결함이므로 동일 영역에서 함께 정정 |
| matching 화면 직접 hook 8 영역 | `imma-phase1-pages.js` 의 `initMatching` 이 `matching.html` 의 디자인 슬롯을 직접 조회한다. (1) `#ai-summary-card` — `match_input.material/processes/warnings`·`surface_roughness_ra`·`tightest_it_grade`·`envelope_mm`·`post_treatment_request`·`vlm_fallback_used` 영역 hydrate (`renderAiSummaryCard`). (2) `.rfq-summary-card` — `part_name` 원문 표시 (GraphRAG 원문 보존 정책 정합), 정적 데모 폴백은 "—" 으로 통일. (3) `.supplier-row` 5 행 — `.s-info h4` 업체명 + AI 배지 (점수 분해 tooltip `buildScoreTooltip` 부착), `.loc` 지역 + 평균 견적 응답시간, `.s-rating` 평점 + 리뷰수 + 평균 납기, `.s-strengths` reasons → 신호 토큰 chip (`renderStrengths`+`renderReason`), `.s-price` "견적 도착 후" 정적 메시지, `.s-equip` `equipment_summary` 상위 3 카테고리 (`renderEquipmentSummary`). 후보 5 명까지 hydrate (`getMatchCandidates(result).slice(0, 5)`). (4) `#compare-box` — 후보 3 명 모두 비교, `compare-items`/`compare-table` 3 열 + 행 선택 시 `selectedIndex` 갱신 + `compare-selected-name` 동기화 (`renderCompareSidebar`). (5) `#compare-proceed-btn` — *"이 후보로 견적 받기"* CTA 텍스트 + `/order-management?rfq_id=` href 동적 부여. hydrate 실패 시 `row1` 정적 *selected/checked/"✓ 선택됨"* 영역은 클래스 + checkbox 제거하여 잘못된 사실을 표시하지 않는다. | matching 화면이 발주 흐름의 시작점이므로 모든 시각 영역을 실 API 결과로 직접 hydrate. equipment_summary·신호 토큰·점수 분해는 매칭 사유의 정보 위계를 buyer 에게 동시 전달하는 핵심 영역 |
| matching 후보 상세 modal | `matching.html` 의 self-contained `.imma-modal-overlay#supplier-detail-modal` 영역에 4 절(매칭 정보 / 매칭 사유 / 보유 장비 / 평점+납기) hydrate. `bindSupplierDetailModal`이 각 row 의 첫 번째 `.btn-outline` (*상세 보기*) 클릭에 `openSupplierDetailModal(cand)` 부착, overlay 바깥 클릭/`#supplier-detail-close` 클릭으로 닫힘. 추천/조건부 배지는 `equipment_verified + !equipment_verified_warning` 기반 분류, 점수 분해는 `cand.score_breakdown` 우선·`cand.breakdown` fallback (technical/availability/quality/total 4 값), 매칭 사유는 `renderReason` chip 재활용, 보유 장비는 `equipment_summary` 전체 목록 (matching summary 의 3 건 제한 해제) | matching 화면의 *상세 보기* 버튼이 R8 까지 동작 부재였으므로 후보 정보를 한 화면에 집약. modal 영역은 imma-common.css 부재 영역이라 matching.html 에 self-contained 정의 |
| supplier 온보딩 4 카드 흐름 | `supplier-register.html` 은 1 카드(가입 정보 + 회사명) 로 축약, 2 카드(업체 정보) + BRN/주소 영역 제거. 가입 완료 후 `initSupplierRegister` 가 `/supplier-settings#onboarding` 으로 redirect. `supplier-settings.html` 의 `#onboarding` 영역에 4 카드 신설: (1) 보유 장비 — `/api/equipment-categories` + `/api/equipment-models?category=` 셀렉트 + `POST /api/equipment` + 자동 매핑 공정 lock, (2) 처리 가능 재질 — `/api/material-categories` chip 토글 + `POST /api/material-capability`, EQUIPMENT_TO_MATERIAL_HINT(22 카테고리 dict) 로 장비 등록 시 추정 재질 자동 체크, (3) 추가 공정 — `/api/processes` chip + 장비 자동 매핑 공정 lock 표시 + `POST /api/process-capability` (service_mode: in_house/outsourced/both), (4) 사업자 정보 — BRN + 시·도(17 종) + city + address + 대표자명 + 우편번호 `PUT /api/company/profile`. `applyOnboardingStatus`가 banner badge (draft/submitted/verified/rejected) + 진행도 (`장비 N / 재질 N / BRN 유무 / region 유무`) 갱신. 백엔드 `_check_onboarding` 4 조건(장비/재질/BRN/region) 충족 시 `draft → submitted` 자동 전환 | 명세서 line 224 의 supplier 가입 영역 = 최소 정보로 한정, 온보딩 정보는 별도 4 카드 영역에서 수집. 장비 등록 시 자동 매핑되는 공정·재질을 lock 상태로 노출하여 사용자가 중복 입력하지 않도록 한다 |
| 매칭 사유 신호 chip 시각화 | `imma-phase1-pages.js` 의 `classifyReason`/`cleanReason`/`renderReason` 3 helper가 reasons 토큰을 positive/info/warn/danger/neutral 5 단계로 분류 후 색상 chip 출력. `[INFO_CATEGORY_FALLBACK]`·`[INFO_PARENT_FALLBACK]` → info(`#eff6ff`/`#1d4ed8`), `[WARN_EQUIPMENT_CAPABILITY_MISSING]`·`[공정 달성범위 의심]`·`[공정 달성범위 의심·재질override]`·`[공정순서 권장위반]` → warn(`#fffaeb`/`#b54708`), `[공정순서 위반]`·`[unsupported]` → danger(`#fef3f2`/`#b42318`), `매칭/충족/범위 내/보유` regex 매치 → positive(`#ecfdf3`/`#027a48`), 그 외 → neutral(`#f2f4f7`/`#344054`). priority(0~5) 정렬로 강도 높은 신호가 상단 노출. `renderStrengths`가 사유 4 개까지 priority 정렬 후 chip 출력 | 매칭 사유의 정보 위계 시각화. positive/info 와 warn/danger 의 색 구분으로 buyer 가 카드 한 눈에 위험 신호 식별 가능 |
| 알림 hook 영역 | (1) buyer — `client-dashboard.html` 의 `#recent-notifications` 카드에 `/api/notifications?unread_only=false` 결과를 hydrate. `BUYER_NOTIFICATION_TYPES` dict 로 `quote_received`(견적 도착·녹색) / `supplier_accepted`(매칭 수락·녹색) / `supplier_declined`(매칭 거절·빨강) 3 이벤트만 표시, 상위 6 건 + reference_type 기반 link(`rfq` → `/order-management?rfq_id=`, `order` → `/order-management?order_id=`). (2) supplier — 기존 `loadSupplierOrdersFromNotifications` 영역 그대로(`order_confirmed` 필터) | buyer 입장에서 RFQ 등록 → 매칭 → 견적 도착 전체 흐름의 핵심 이벤트를 대시보드 한 화면에서 확인 가능 |
| 시연 정합 자명 영역 | (1) `quote-request.html` 의 `#q-material-custom` 자유 텍스트 input — inline `display:none` 제거 + JS 에서 `__custom__` 선택 시 노출/그 외 숨김 토글 (`change` 이벤트 hook). (2) `supplier-workbench.html` 의 견적 금액 input — `id="reply-amount"` 부여, `quotePayloadFromWorkbench`가 fallback `6,250,000` 제거하고 빈 값 시 `null` 반환 + 호출측 toast + return. (3) `order-management.html` 의 quotes 0 건 분기 — 정적 demo 영역을 그대로 노출 + `.imma-order-demo-notice` 배지(admin-menu Demo UI 배지 패턴 정합) + `.imma-quote-empty` "견적 대기 중" 안내 카드 prepend 양면 표시. (4) admin 3 페이지 로그아웃 통일 — topbar 로그아웃 제거 + sidebar `.logout-btn` + `bindLogout` 일반화 (supplier 영역과 동일 helper). (5) `client-register.html`·`supplier-register.html` 의 *중복 확인* 버튼에 `#client-check-login-id`/`#supplier-check-login-id` id 부여 + `bindLoginIdCheck` 공용 helper 가 `/api/check-login-id` 호출 | 시연 흐름에서 정적 영역과 실 API 결과가 충돌하던 지점을 영역별로 차폐. demo 배지 + 안내 카드로 사용자가 demo 영역과 실 API 영역을 구분 가능 |
| 인증 fetch wrapper | `imma-api.js`의 `fetchRaw`가 JWT 존재 시 `Authorization: Bearer`를 자동 주입한다. 401 응답은 single-flight `imma.logout('unauthorized')`로 처리하여 동시 다발 401에서도 redirect는 1회만 발생. 네트워크 오류는 `NETWORK_ERROR` 코드로 격리하여 VLM fallback이 504/network 오류를 동일 분기로 처리 가능 | 페이지마다 fetch 호출에 헤더를 수동 부착하던 패턴을 제거. 인증 만료를 한 곳에서 처리하여 race condition 방지 |
| 세션 2차 검증 | `auth.js`의 `verifySession()`이 보호 페이지 진입 시 `/api/me`를 single-flight로 호출한다. localStorage user는 1차 캐시 (화면 깜빡임 방지)이며, 서버 검증 결과를 최종 진실로 둔다. JWT exp 클라이언트 decode로 만료가 명백하면 즉시 logout | 토큰 위변조·만료·서버 측 revoke를 클라이언트 1차 검사만으로 신뢰하지 않음 |
| supplier order 발견 = 알림 기반 | 별도 `/api/orders` 목록 endpoint 없이 `GET /api/notifications?unread_only=false`를 supplier가 조회하고 `event_type='order_confirmed' AND reference_type='order'` 필터 후 `reference_id`로 `GET /api/orders/{order_id}`에 진입한다. `imma-phase1-pages.js`의 `loadSupplierOrdersFromNotifications()`가 상위 5건을 로드 | Phase 1 범위 한정. supplier 입장 주문 목록 endpoint 신설은 Phase 2로 미룸 |
| admin Phase 1 실연결 범위 | `/api/admin/login`, `/api/admin/companies/pending`, `/api/admin/companies/{id}/verify`, `/api/admin/companies/{id}/reject` 네 개만 실 API로 연결. `/api/admin/rfqs`, `/api/admin/orders` 등 KPI/관제 표는 admin-control-center에서 시연 데모 카드(거래 완료 반영 `PO-20260508-001` 등)로 유지. admin-menu.js + imma-common.css의 `.admin-demo-notice` 배지로 "Demo UI · 일부 데이터는 시연용 샘플입니다"를 명시 | admin 실연결 범위가 넓어지면 시연 리스크가 커지므로 pending + verify/reject만 실 API로 확정. 나머지는 발표 슬라이드에서 데모로 명시 |
| localStorage scope | 전역 인증 키는 `imma_access_token`, `imma_user` 두 개. 업무 상태 키는 모두 `imma:{user_id}:...` prefix를 사용한다 (`scopedKey` 헬퍼). logout 시 `clearUserScopedState(user.id)`가 해당 prefix 키를 일괄 삭제 | 동일 브라우저에서 사용자 전환 시 RFQ·order id 누수 방지 |
| 신호 토큰 시각화 매핑 | `imma-phase1-pages.js`의 `classifyReason`/`cleanReason`/`renderReason`이 `[INFO_CATEGORY_FALLBACK]`, `[INFO_PARENT_FALLBACK]`, `[WARN_EQUIPMENT_CAPABILITY_MISSING]`, `[공정 달성범위 의심]`, `[공정 달성범위 의심·재질override]`, `[공정순서 위반]`, `[공정순서 권장위반]`, `[unsupported]` 토큰을 info/warning/danger 칩으로 매핑. 알 수 없는 token은 neutral 칩으로 그대로 표시 | 매칭 사유의 정보 위계(info < warning < danger)를 buyer에게 시각적으로 전달. 미상 token도 숨기지 않아 향후 신호 추가 시 자연 호환 |
| 시연 정합화 | PO 번호 표기는 `PO-20260508-001` 형식으로 통일 (이전 `PO-2025-00123` 형식 제거). `휴먼화` → `휴면화` 표기 정정. admin 페이지는 `admin-menu.js`가 사이드바 하단에 "Demo UI · 일부 데이터는 시연용 샘플입니다" 배지를 렌더, `imma-common.css`의 `.admin-demo-notice`가 스타일을 부여. `admin-control-center.html`은 거래 완료 반영 카드에 `PO-20260508-001`을 명시 노출 | 시연 자료와 UI 상의 PO/문구가 한 가지 형식으로 수렴. 데모성 카드와 실 API 데이터를 사용자가 구분 가능 |
| buyer 후보 선택 = UI 표시 | `matching.html`에서 buyer가 후보를 클릭하면 `imma:{user_id}:{rfq_id}:selected_candidate`에 `{rfq_id, rfq_part_id, company_id, company_name, match_run_id, rank_no}`를 저장하고 카드에 `candidate-selected` 클래스만 부여한다. 실제 발주는 견적 비교 화면(`order-management.html`)에서 quote_id 기반 `/api/orders` 호출로만 발생 | 매칭 후보 선택이 발주를 즉시 의미하지 않음을 UX에 분명히 표시. supplier 견적 제출 후에만 발주 가능한 백엔드 정책과 정합 |

---

## 8. 향후 과제

| 항목 | 비고 |
|---|---|
| VLM 팀 스펙 합의 | V.B schema 확정 후 프롬프트에 입력 구조 매핑 가이드 추가 |
| 복수 파트 도면 | VLM BOM 추출률(30%) 개선 선행 필요 |
| GDT 소프트 시그널 | 리스크 태깅, RFQ 질문 생성, 업체 랭킹 가중치 |
| 실시간 알림 | WebSocket (현재 DB 폴링) |
| Neo4j Cypher 전환 | resolve/match의 인메모리 룩업을 Cypher로 교체 (성능 이점 없어 우선순위 낮음) |
| 프로덕션 배포 | Railway + ngrok(Neo4j 터널) 구성 완료. 고정 도메인은 유료 |
| CMPC match.py 연결 | 결합 테이블 seed 완료(350행). 하드필터에서 결합 쿼리 사용은 향후 |
| 사용자 장비 등록 non_machining | 장비 등록 API에서 열처리/용접/주조 capability 자동 생성 미구현 |
| respond_to_match 부품별 응답 | match_candidates PK가 3컬럼(+rfq_part_id)인데 수락/거절 API WHERE에 rfq_part_id 미포함. 현재 업체 일괄 응답으로 동작하며, 부품별 개별 응답 필요 시 API 경로 + SQL 수정 필요 |
| snapshot↔스케줄 자동 동기화 | company_availability_snapshot과 equipment_daily_schedule 간 자동 갱신 메커니즘 미구현. 현재 수동 보정. 장비 스케줄 변동 시 업체 가용 상태를 자동 반영하는 로직 필요 |
| 외주 capability 시그널링 | heat_treatment/surface_treatment/casting/welding 등 외주 공정은 SQL 하드필터에서 제외하고 업체 견적 단계에 위임. 향후 응답에 "외주망 확인 권장" 정보성 경고, 사내 보유 우대 스코어링 추가 가능 |
| 견적 마감 시각 도입 검토 | 현재 발주자는 납기일(`requested_delivery_date`)만 명시. 향후 견적 비교/결정 마감 시각이 운영상 필요해지면 `rfqs.quote_due_at` 복원 + 마감 후 상태 자동 전이 로직 추가 검토 |
| 다부품 RFQ rfq_part_id 분리 매칭 | `_save_match_history`가 `match_runs.rfq_part_id`를 NULL로 저장. 단일 부품 RFQ는 무영향이나 다부품 RFQ에서 supplier 조회 시 부품과 카르테시안 중복. 부품별 match_runs 분리 또는 rfq_part_id 기록 필요 |
| 매칭 신호의 DB 영구 기록 | `[INFO_PARENT_FALLBACK]`, `[INFO_CATEGORY_FALLBACK]`, `[WARN_EQUIPMENT_CAPABILITY_MISSING]` 등 신호가 응답 JSON으로만 전달되고 `match_candidates.explanation_jsonb`에는 저장되지 않음. supplier 측 매칭 조회 시 fallback 사실 확인 불가. supplier API 응답 구조 결정과 함께 일괄 보강 검토 |
| supplier 발주 목록 endpoint | 현재 supplier는 `/api/notifications?unread_only=false`의 `order_confirmed` 알림을 통해 주문을 발견한다. supplier 입장 주문 목록(`GET /api/orders` supplier 필터) endpoint 신설은 Phase 2 영역 |
| quote line_items 응답 포함 | `quote_responses`에는 line_items가 저장되나 `GET /api/rfq/{rfq_id}/quotes` 응답에서는 제외된 상태. 견적 상세 breakdown 노출은 Phase 2 영역 |
| 결제·메시징·supplier 검색 정밀 필터 | Phase 1에서는 데모 페이지(`payment-success.html`, `supplier-messages.html`, `search-suppliers.html`)로 유지. 실 API 연결은 Phase 2 영역 |
| admin 전체 관제 실연결 | `/api/admin/rfqs`, `/api/admin/orders` 등 KPI/관제 표는 Phase 1에서 admin-control-center 시연 카드로 유지. read-only 실 API 연결과 KPI 산출은 Phase 2 영역 |
