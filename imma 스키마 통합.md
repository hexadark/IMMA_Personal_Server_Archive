# main.py 개조 내역 (public → imma 스키마 통합)

## 요약

모든 API가 `public` 스키마 대신 `imma` 스키마를 사용하도록 전환한다.  
기존 API 응답 필드명은 최대한 유지하여 프론트엔드 수정 범위를 최소화한다.

이번 개조의 핵심은 단순히 테이블 이름을 바꾸는 것이 아니라, 기존 시연용 `public` 테이블 기반 구조를 `imma` 스키마 중심의 정규화된 서비스/매칭 구조로 전환하는 것이다.

---

## 파일 변경

**삭제: 0개**

**추가: 2개**

| 파일 | 내용 |
|------|------|
| `lookup_tables/schema.sql` | DDL 전체. 32개 테이블 + Materialized View 1개 + seed 데이터 포함 |
| `pipeline/setup_db.py` | DB 초기화 스크립트. 재질 46종, alias 42종, 장비 카탈로그 55개, mock 업체 17개 자동 생성 |

**수정: 1개**

| 파일 | 내용 |
|------|------|
| `main.py` | 모든 주요 API를 `imma` 스키마 기준으로 개조 |

**백업**

기존 main.py는 아래 경로에 백업한다.

`main.py백업용폴더/main.py백업본16.txt`

---

## 엔드포인트별 변경

| 엔드포인트 | 변경 내용 |
|-----------|----------|
| `POST /signup` | `users` 테이블 제거. `role` 파라미터로 분기한다. `buyer`면 `imma.buyers`에 INSERT, `supplier`면 `imma.companies`에 INSERT한다. 가입 필수 입력은 이름, 이메일, 비밀번호로 유지하고, `phone`은 선택값으로 처리한다. `supplier` 가입 시 `onboarding_status='draft'`로 시작하며 `imma.company_availability_snapshot` 기본값을 자동 생성한다. 응답에는 `onboarding_status`를 포함한다. |
| `GET /companies` | `public.companies` 조회에서 `imma.companies LEFT JOIN imma.company_sites` 조회로 변경한다. AS 별칭으로 기존 응답 필드명을 최대한 유지한다. |
| `GET /companies/buyers` | `companies WHERE type='buyer'` 방식에서 `imma.buyers` 단독 조회로 변경한다. |
| `GET /companies/suppliers` | `companies WHERE type='supplier'` 방식에서 `imma.companies JOIN imma.company_sites WHERE status='active'` 방식으로 변경한다. |
| `POST /rfq` | 기존 flat INSERT 1건 방식에서 `imma.rfqs` + `imma.rfq_parts` + `imma.rfq_part_processes` 3테이블 트랜잭션 방식으로 변경한다. |
| `GET /rfqs` | 기존 flat SELECT 방식에서 3테이블 JOIN + `string_agg` + `general_notes_jsonb->>'note'` 기반 조회로 변경한다. |
| `GET /match/{rfq_id}` | 기존 ILIKE 문자열 매칭에서 `imma.company_capability_summary` Materialized View 기반 배열 매칭으로 변경한다. 스코어 계산은 유지하되 `avg_rating` 반영으로 변경한다. |
| `POST /api/match-v2` | 변경 없음. 기존 파이프라인 호출 구조를 유지한다. |
| `POST /vlm-result` | 기존 `vlm_rag_results` 저장 방식에서 `imma.drawings` 저장 방식으로 변경한다. |
| `GET /vlm-results` | 기존 `vlm_rag_results` 조회 방식에서 `imma.drawings` 조회 방식으로 변경한다. |
| `GET /api/reviews` | `public.reviews` 조회에서 `imma.reviews JOIN imma.buyers` 조회로 변경한다. |
| 신규 선택지 6개 | `/api/processes`, `/api/material-categories`, `/api/materials`, `/api/equipment-categories`, `/api/equipment-models`, `/api/health` 추가 |
| **신규 온보딩/CRUD 7개** | 아래 표 참조 |

### 신규 온보딩/CRUD API (7개)

| 엔드포인트 | 역할 | 주요 동작 |
|-----------|------|----------|
| `POST /api/equipment` | 장비 등록 | 카탈로그 model_id 선택 → equipment INSERT + equipment_process_capabilities 자동 생성 + company_process_capabilities 자동 병합. onboarding 체크 + MV REFRESH |
| `POST /api/material-capability` | 재질 역량 등록 | materials/categories 배열로 전달 → company_material_capabilities INSERT. material_code 유효성 사전 검증. onboarding 체크 |
| `POST /api/process-capability` | 추가 공정 등록 | 외주/장비 없는 공정 수동 추가. service_mode, IT/Ra, lead_days 입력 가능 |
| `PUT /api/company/profile` | 사업자정보 업데이트 | companies 컬럼 업데이트 + company_sites UPSERT + company_contacts UPSERT. onboarding 체크 |
| `PUT /api/company/availability` | 가용상태 변경 | company_availability_snapshot UPDATE + MV REFRESH |
| `POST /api/reviews` | 리뷰 작성 | reviews INSERT + MV REFRESH |
| `POST /api/quote` | 견적 회신 | quote_responses INSERT + quote_line_items N행 INSERT |

---

## `POST /signup` 상세 변경

### 변경 요약

기존 `public.users` 기반 회원가입을 제거하고, `role` 값에 따라 `imma.buyers` 또는 `imma.companies`에 직접 가입 정보를 저장한다.

가입 필수 입력값은 **이름, 이메일, 비밀번호**로 유지한다.  
전화번호인 `phone`은 선택값으로 처리하며, 가입 이후 프로필 또는 온보딩 단계에서 추가 입력할 수 있다.

`supplier` 가입 시에는 `imma.companies`에 업체 기본 정보를 저장하고, 동시에 `imma.company_availability_snapshot` 기본값을 자동 생성한다.  
응답에는 프론트엔드가 다음 화면을 판단할 수 있도록 `onboarding_status`를 포함한다.

### Request Body 예시

```json
{
  "name": "A정밀",
  "email": "supplier@example.com",
  "password": "test1234",
  "role": "supplier",
  "phone": "010-1234-5678"
}
```

### 필드 규칙

| 필드 | 필수 여부 | 설명 |
|---|---|---|
| `name` | 필수 | `buyer`는 이름 또는 회사명, `supplier`는 업체명 |
| `email` | 필수 | 로그인 ID로 사용할 이메일 |
| `password` | 필수 | 로그인용 비밀번호 |
| `role` | 선택 | `buyer` 또는 `supplier`. 미전송 시 기본값 `buyer` |
| `phone` | 선택 | 연락처. 가입 이후 프로필 또는 온보딩에서 추가/수정 가능 |

### role 분기 처리

| role | 저장 테이블 | 처리 내용 |
|---|---|---|
| `buyer` | `imma.buyers` | 발주자 계정 생성 |
| `supplier` | `imma.companies` | 가공업체 계정 생성 |

### supplier 가입 시 자동 처리

`supplier`로 가입할 경우 아래 처리를 자동 수행한다.

1. `imma.companies`에 업체 기본 정보 INSERT
2. `onboarding_status = 'draft'`로 설정
3. `status = 'active'`로 설정
4. `imma.company_availability_snapshot` 기본값 자동 생성
5. 응답에 `onboarding_status` 포함

기본 가용 상태 예시는 아래와 같다.

```json
{
  "overall_status": "available",
  "current_utilization_pct": null,
  "min_lead_time_days": null,
  "next_available_date": null
}
```

### buyer 응답 예시

```json
{
  "buyer_id": "uuid-...",
  "name": "홍길동",
  "email": "buyer@example.com",
  "role": "buyer",
  "onboarding_status": "not_required",
  "message": "signup success"
}
```

### supplier 응답 예시

```json
{
  "company_id": "uuid-...",
  "name": "A정밀",
  "email": "supplier@example.com",
  "role": "supplier",
  "onboarding_status": "draft",
  "message": "signup success"
}
```

### 비고

- 발주자는 온보딩 없이 바로 RFQ 생성 가능
- 공급자는 온보딩 완료 후 `onboarding_status = 'verified'`가 되어야 매칭 대상에 포함
- `company_capability_summary` MV는 `status = 'active'` 및 `onboarding_status = 'verified'` 조건을 기준으로 매칭 후보를 구성
- 기존 API 응답 필드명은 최대한 유지하여 프론트엔드 수정 범위를 최소화한다

---

## 로직 변경 핵심

| 이전 v1 | 이후 개조 |
|-----------|-----------|
| public 스키마 직접 조회 | `imma.` 스키마 전체 통일 |
| `users` 테이블로 회원가입 | `buyers` / `companies`에 직접 INSERT. `role` 기준으로 분기하며, `supplier`는 가입 시 `onboarding_status='draft'`로 시작하고 `company_availability_snapshot` 기본값 자동 생성 |
| 재질/공정이 text 컬럼, ILIKE 검색 | 정규화된 FK + 배열 연산자 `@>`, `ANY` 사용 |
| RFQ = 1행 flat | RFQ = `rfqs` + `rfq_parts` + `rfq_part_processes` 3단 정규화 |
| 매칭 스코어에 `avg_lead_days * 3` 감점 | `avg_rating` 가점으로 대체 |
| 매칭 스코어에 `best_tolerance_mm` 가점 | 제거. IT grade로 충분 |
| VLM 결과 별도 테이블 `vlm_rag_results` 사용 | `imma.drawings.vlm_result_jsonb`에 통합 |
| 선택지 API 없음 | 선택지 6개 + 온보딩/CRUD 7개 추가 (총 13개 신규) |
| 온보딩 상태 전환 로직 없음 | `_check_onboarding` 자동 전환 구현 (draft → submitted → verified). rejected 상태는 우회 불가 |
| MV 수동 갱신 | 역량 변경 / 리뷰 / 가용상태 변경 시 자동 REFRESH |
| `companies.company_type` 컬럼 | imma에서 companies = supplier 전용. company_type 컬럼 제거, 응답에 `"supplier"` 하드코딩 |
| `companies.company_scale` 컬럼명 | `company_size`로 통일 (schema.sql 기준) |
| `company_sites.province` 컬럼 | `region` 컬럼 사용으로 변경 |

---

## 프론트엔드 유의사항

| 항목 | 변경 전 | 변경 후 | 영향 |
|------|---------|---------|------|
| ID 타입 | `1, 2, 3` integer | `"uuid-..."` 문자열 | `id`를 숫자로 처리하는 곳 있으면 수정 필요 |
| `POST /signup` | `{"name", "email", "phone"}` | `{"name", "email", "password", "role", "phone"}`. `name`, `email`, `password`는 필수. `role` 미전송 시 기본값 `"buyer"`, `phone`은 선택값 | `supplier` 가입 시 `onboarding_status: "draft"` 반환 및 `company_availability_snapshot` 자동 생성 |
| `POST /rfq`의 `buyer_code` | 문자열 코드 | UUID, `imma.buyers.buyer_id` | buyer 목록에서 받은 UUID를 전달해야 함 |
| `GET /match/{rfq_id}` | `/match/5` 정수 | `/match/uuid-...` UUID | rfqs 목록에서 받은 UUID를 사용 |
| 기존 데이터 | public 스키마 | imma 스키마 새로 시작 | 기존 public 데이터 안 보임 |
| 응답 필드명 | 그대로 | 변경 없음 | `company_code`, `company_name`, `region` 등 기존 필드명 유지 |

---

## DB 초기화 방법

Railway DB에 처음 적용하거나 초기화할 때 아래 명령을 실행한다.

```bash
cd pipeline
python setup_db.py
```

실행 순서는 아래와 같다.

1. `schema.sql` DDL 실행  
   - 32 테이블 + MV 생성
2. 재질 마스터 46종 + alias 42종 seed
3. 장비 카탈로그 55개 모델 로드
4. mock 업체 17개 생성  
   - 역량 + 장비 + 리뷰 포함
5. Materialized View REFRESH

환경변수 설정 예시는 아래와 같다.

```bash
export IMMA_DB_HOST=shortline.proxy.rlwy.net
export IMMA_DB_PORT=51309
export IMMA_DB_NAME=railway
export IMMA_DB_USER=postgres
export IMMA_DB_PASSWORD=<비밀번호>
```

---

## imma 스키마 데이터 현황

`setup_db.py` 실행 후 기준.

| 테이블 | 행 수 | 비고 |
|--------|------|------|
| `companies` | 17 | mock 업체. A정밀~Q범용선반 |
| `company_material_capabilities` | 80 | FK 정규화 |
| `company_process_capabilities` | 77 | IT/Ra/크기 포함 |
| `equipment` | 60 | 카탈로그 55개 모델 기반 |
| `equipment_process_capabilities` | 122 | 장비별 공정 능력 |
| `reviews` | 88 | mock 리뷰 |
| `materials` | 46 | KS 재질 코드 마스터 |
| `material_aliases` | 42 | JIS/legacy 매핑 |
| `equipment_model_catalog` | 55 | 실제 제조사 장비 스펙 |
| `process_catalog` | 26 | 공정 계층. parent 포함 |
| `material_category_catalog` | 10 | 재질 카테고리 |
| `equipment_category_catalog` | 20 | 장비 카테고리 |

---

## schema.sql 변경사항

| 변경 | 내용 |
|------|------|
| `users` 테이블 | 삭제. 로그인을 buyers/companies에서 직접 처리 |
| `buyers.email` | nullable → **NOT NULL** |
| `buyers.password_hash` | 신규 추가, **NOT NULL** |
| `buyers.region`, `buyers.company_scale` | 신규 추가 (nullable) |
| `companies.main_email` | nullable → **NOT NULL UNIQUE** |
| `companies.login_password_hash` | 신규 추가, **NOT NULL** |
| `company_sites` | **UNIQUE (company_id, site_name)** 추가 — UPSERT용 |
| `company_contacts.contact_name` | nullable → **NOT NULL DEFAULT '대표'** — UPSERT용 |
| `company_contacts` | **UNIQUE (company_id, contact_name)** 추가 — UPSERT용 |
| `company_material_capabilities` | **partial unique index 2개** 추가 — (company_id, material_id) WHERE specific_material, (company_id, material_category_code) WHERE material_category |
| `company_capability_summary` MV | WHERE 조건에 `AND onboarding_status = 'verified'` 추가 |

---

## 적용 전 체크리스트

`imma` 스키마 전환 전 아래 항목을 확인한다.

- 현재 작동하는 `main.py` 백업 완료
- 현재 GitHub repo 상태 백업 또는 백업 브랜치 생성 완료
- 기존 UI 폴더 zip 백업 완료
- Railway DB 접속 환경변수 확인
- `lookup_tables/schema.sql` 존재 확인
- `pipeline/setup_db.py` 존재 확인
- `setup_db.py` 실행 후 `imma` 스키마 생성 여부 확인
- Swagger `/docs` 접속 확인
- 신규 `/api/health` 정상 응답 확인
- `POST /signup` buyer/supplier 각각 테스트
- `GET /companies`, `/companies/buyers`, `/companies/suppliers` 테스트
- `POST /rfq`, `GET /rfqs` 테스트
- `GET /match/{rfq_id}` UUID 기반 테스트
- `POST /vlm-result`, `GET /vlm-results` 테스트
- 신규 선택지 API 6개 테스트
- **신규 온보딩 API 테스트**: `POST /api/equipment`, `POST /api/material-capability`, `POST /api/process-capability`
- **신규 프로필/가용상태 테스트**: `PUT /api/company/profile`, `PUT /api/company/availability`
- **신규 리뷰/견적 테스트**: `POST /api/reviews`, `POST /api/quote`
- **onboarding 자동 전환 확인**: 장비+재질+사업자등록번호+지역 입력 후 verified 전환 확인

---

## 전환 기준

기존 `public` 스키마는 백업/롤백용으로 보존한다.  
신규 개발 기준은 `imma` 스키마로 통일한다.

앞으로의 공식 기준은 아래와 같다.

| 영역 | 기준 테이블 |
|---|---|
| 발주자 | `imma.buyers` |
| 가공업체 | `imma.companies` |
| 업체 주소 | `imma.company_sites` |
| 업체 담당자 | `imma.company_contacts` |
| 장비 | `imma.equipment` |
| 공정 역량 | `imma.company_process_capabilities` |
| 재질 역량 | `imma.company_material_capabilities` |
| 도면 | `imma.drawings` |
| 견적 요청 | `imma.rfqs`, `imma.rfq_parts`, `imma.rfq_part_processes` |
| 매칭 요약 | `imma.company_capability_summary` |
| 리뷰 | `imma.reviews` |

---

## 결론

이번 개조는 기존 시연용 DB/API 구조를 `imma` 스키마 중심의 실제 서비스형 구조로 통합하는 작업이다.

핵심 원칙은 아래와 같다.

1. 기존 public 스키마는 삭제하지 않고 백업/롤백용으로 보존한다.
2. 신규 개발 기준은 `imma` 스키마로 통일한다.
3. API 주소와 응답 필드명은 최대한 유지한다.
4. 프론트엔드 수정은 UUID 처리, signup 입력값, RFQ buyer_id 처리 중심으로 최소화한다.
5. RAG/DB 매칭은 `company_capability_summary` MV와 정규화된 역량 테이블을 기준으로 수행한다.
