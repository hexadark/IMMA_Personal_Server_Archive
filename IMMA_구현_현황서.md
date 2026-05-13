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
| `pipeline_runner.py` | 진입점. DB 저장 → parse → resolve → match → response 체이닝 |
| `parse.py` | JSON → VlmPart 변환. null/타입 방어, ±IT 환산, ▽→Ra fallback, 나사 오탐 방지 |
| `resolve.py` | 재질 7단계 해소(코드→alias→규격접미사→템퍼접미사→카테고리텍스트→LIKE→regex), 일반공차 fallback, 형상 분류, 호환성/피드백 검증 |
| `match.py` | SQL 하드필터, 장비 검증, 공정 순서 검증. 카테고리 확장, parent fallback 안전 제한. 외주 공정(FAIL_OPEN_PROCESSES)은 하드필터 SQL에서 제외하여 업체 외주망에 위임 |
| `response.py` | 매칭 결과 JSON 조립. ontology_warnings 합류. 사내 공정과 외주 공정을 분리 표시 |
| `lookup.py` | 정적 지식 단일 원천. STAGE_TO_CODES 54항목, PRECISION 11개, INTERMEDIATE 12개, NON_MACHINING 11개, FAIL_OPEN_PROCESSES 8개, CATEGORY_TEXT_TO_CODE 51항목, PROC_NORMALIZE 5항목, `SAFE_PARENT_FALLBACK={turning_rough, turning_finish, milling_rough, milling_finish}` |
| `config.py` (pipeline) | DB 접속 정보, 룩업 JSON 경로, 환경변수 (DATABASE_URL 우선 + 개별 변수 fallback) |
| `db.py` | psycopg2 단건 연결, 트랜잭션 컨텍스트매니저, 쿼리 헬퍼 (커넥션 풀은 routers/deps.py의 SQLAlchemy engine) |
| `setup_db.py` | DDL 실행, seed 데이터 (재질 68개 + alias + mock 업체 19개 + admin/buyer/supplier seed 계정), MV 갱신 |
| `graphrag_transform.py` | VLM raw → 스키마 변환. Gemini 3 Flash Preview, thinking_level=low, temperature=1.0, timeout=120s. `routers/matching.py`가 `/api/match-v2`에 `drawing_id`만 들어오면 `drawings.vlm_result_jsonb`를 읽어 `transform_vlm_raw`를 호출하여 자동 변환 |
| `routers/vlm.py` | `/vlm/analyze-upload`: buyer 인증 + multipart 이미지 → Replicate VLM API 호출 → V.B raw JSON → `drawings`에 INSERT(file_sha256 + buyer_id) → drawing_id 반환 |
| `routers/config.py` | `/api/config/health` admin 전용. `DATABASE_URL`·`JWT_SECRET`·`REPLICATE_API_TOKEN`·`REPLICATE_MODEL_VERSION`·`GEMINI_API_KEY`·`NEO4J_URI` 설정 여부(boolean)와 `jwt_secret_is_default` 플래그만 반환. 민감정보 값은 노출하지 않음 |
| `routers/deps.py` | 공통 DB engine / SCHEMA / JWT 유틸 / 인증 의존성. `JWT_SECRET=imma-dev-secret` 기본값을 사용한 상태에서 `ENV` 또는 `RAILWAY_ENVIRONMENT`가 production을 가리키거나 `RAILWAY_PROJECT_ID`가 설정되어 있으면 startup 시점에 `RuntimeError`로 fail-fast |
| `models.py` | VlmPart(unsupported 포함), ResolvedPart(ontology_warnings 포함), MatchCandidate, MatchResponse |

### 프론트엔드 공용 JS

| 파일 | 역할 |
|---|---|
| `machhub_ui/imma-ui-utils.js` | toast 스택, `setLoading`, `escapeHtml`, `formatCurrency`/`formatDate`, `renderSessionHeader`, `ensurePanel`/`setPanelContent`, `getQueryParam`, role label·display name 헬퍼 |
| `machhub_ui/auth.js` | `imma_access_token` / `imma_user` localStorage 관리, UTF-8 안전 JWT payload decode, `getUser`·`setSession`·`clearSession`, user-scoped key 생성기(`scopedKey`), `redirectForRole`, single-flight `verifySession()` (`/api/me` 2차 검증), `requireRole`/`requireAdmin`, `login()` (buyer/supplier/admin 분기 endpoint) |
| `machhub_ui/imma-api.js` | `fetchRaw`/`apiJson`/`apiForm` wrapper. JWT가 있으면 `Authorization: Bearer` 자동 주입, 네트워크 오류는 `NETWORK_ERROR` 코드로 격리, 401 응답은 single-flight `imma.logout('unauthorized')`로 redirect |
| `machhub_ui/imma-phase1-pages.js` | path 기반 라우터. landing 로그인, buyer/supplier 가입, buyer 대시보드·견적 요청·매칭·발주 관리, supplier 대시보드·작업대·설정·RFQ 상세, admin 대시보드·업체 검수 페이지를 각각 실 API 흐름으로 초기화. VLM 진행도 단계(0/30/90/180/240/300초)·504/502/timeout fixture fallback·notifications 기반 supplier order 발견 흐름도 여기에 모인다 |
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

### 엔드포인트 (62개)

| 카테고리 | 수 | 핵심 |
|---|---|---|
| 인증/가입 | 3 | `/api/login`, `/api/me`, `/signup` |
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

buyer는 `login_id, name, email, password` 필수, 결과는 `buyers` 단일 INSERT. supplier는 `login_id, name, company_name, email, password` 필수이며 `name`(담당자명)과 `company_name`(회사명)을 분리 소비한다. `companies`에 `onboarding_status='submitted'`로 INSERT, 동일 트랜잭션에서 `company_contacts`에 primary contact(contact_name=`name`, role_title=`가입 담당자`, is_primary=true, receives_rfq=true)를 추가하고, `company_availability_snapshot`에 `overall_status='available'` 행을 보장한다. 응답에는 토큰이 없으며 UI는 즉시 `/api/login`을 다시 호출한다.

### RFQ·매칭·견적 응답

- `GET /rfqs` (`routers/rfqs.py`) — buyer 본인 RFQ 목록. 응답 행에 `status`, `rfq_no`, `order_quantity`, `budget_amount`, `budget_currency` 포함.
- `POST /api/match-v2` — buyer 또는 admin만 호출 가능. `drawing_id` 전달 시 도면 소유권 검증 + `parts` 미제공 시 `vlm_result_jsonb`를 `transform_vlm_raw`로 자동 변환한다. 파이프라인 실행 후 `_save_match_history()`로 `match_runs` 1행과 `match_candidates` N행을 저장하고, supplier에게 `match_request`, buyer에게 `match_completed` 알림을 발송한다. 저장 실패 시 라우터는 500으로 fail-fast 응답한다("매칭 결과 저장 또는 supplier 전송에 실패했습니다. 다시 실행해 주세요."). 응답 후보 객체는 mutate되어 `match_run_id`, `rank_no`, `rfq_part_id`, `technical_score`, `availability_score`, `quality_score`, `total_score`, `availability_info`를 포함한다. `score_lookup` 키는 `(company_id, rfq_part_id)` 복합으로 구성되며, 단부품 결과에 한해 `(company_id, '')`로의 fallback이 허용된다.
- `PUT /api/match-candidates/{match_run_id}/{company_id}/respond` — supplier만 호출 가능. JWT의 company_id와 path의 company_id 일치 검증. 응답 후 buyer에게 `supplier_accepted` 또는 `supplier_declined` 알림 발송.
- `POST /api/quote` — supplier만 호출 가능. `match_candidates.supplier_response='accepted'` 검증 후 견적 INSERT. 첫 견적 도착 시 RFQ `open → quoted` 자동 전이. buyer에게 `quote_received` 알림.
- `GET /api/rfq/{rfq_id}/quotes` — buyer/supplier/admin 역할별 분기. buyer는 본인 RFQ 전체 견적, supplier는 본인 견적만, admin은 전체 견적. line_items는 Phase 1 응답에서 제외(Phase 2 영역).

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
