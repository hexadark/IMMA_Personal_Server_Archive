# IMMA — Intelligent Manufacturing Matching Agent

제조업 발주자 도면 → AI 분석 → 가공업체 자동 매칭 플랫폼.

## 핵심 가치

제조 발주자는 도면 한 장을 업로드한다. VLM이 재질·공정·치수·공차를 추출하고, GraphRAG가 제조 지식 그래프를 탐색하여 구조화하며, 매칭 파이프라인이 역량이 맞는 가공업체를 자동으로 찾아준다. "제조업 카카오택시" — 발주자·공급사·AI가 하나의 흐름에 묶여 견적에서 납품까지 전체 제조 발주 라이프사이클을 관리한다.

발주자 측의 소량 발주 기피·불투명 견적·납기 불안, 공급사 측의 장비 유휴·신규 도면 파악 부담을 AI가 도면 분석과 역량 매칭으로 동시에 해소한다.

## 팀 / 저장소

이스트캠프 AI 모델개발 15기 3차 프로젝트 (~5/18 종료).
과기정통부 주최 2026 전국민 AI 경진대회(AI CHAMPION) 출품 (~5/15 예선 종료).

| 담당 | 이름 | 저장소 |
|------|------|--------|
| 모델학습 + 도면인식 | 김지형 | [amadda0616-hash/IMMA](https://github.com/amadda0616-hash/IMMA) |
| 서버 + 프론트 | 권태은 | [rnjsxodms12-star/fas](https://github.com/rnjsxodms12-star/fas) |
| DB + RAG | 김태훈 | [hexadark/IMMA_Personal_Server_Archive](https://github.com/hexadark/IMMA_Personal_Server_Archive) |

## 시스템 구조

| 구성요소 | 기술 |
|----------|------|
| **백엔드** | FastAPI + PostgreSQL 15+ (`imma` 스키마) |
| **VLM 서버 (Server_VB)** | Qwen2.5-VL 7B Student LoRA + Qwen3-VL-30B-A3B Teacher hybrid — 별도 GPU + Cloudflare Tunnel |
| **GraphRAG** | Gemini 3 Flash Preview — VLM raw JSON → IMMA 구조화 JSON 변환 |
| **프론트엔드** | `machhub_ui/` 정적 HTML 21개 + 공용 JS 5종, FastAPI가 mount |
| **(선택) Neo4j** | 493노드 / 1015관계 제조 지식 그래프. GraphRAG 도구 탐색 대상 |

## 핵심 흐름

도면 업로드부터 납품까지 4 단계로 진행된다.

```
1. 도면 업로드
   buyer 도면 → POST /vlm/analyze-upload → Server_VB 호출
   → drawings.vlm_result_jsonb INSERT → drawing_id 반환
   → buyer 에게 AI 분석 결과 카드 제공 (재질·치수·후처리 인라인 수정 가능)

2. 매칭
   buyer 견적 요청 → POST /api/match-v2 { drawing_id, order_quantity }
   → GraphRAG 변환 (transform_vlm_raw) → 매칭 하드필터 → 후보 5명 반환
   → match_runs / match_candidates 저장 + supplier 알림 발송

3. 견적
   supplier 알림 수신 → 수락/거절 → 견적 제출 (POST /api/quote)
   → buyer 대시보드 견적 도착 알림 → 견적 비교

4. 발주
   buyer 견적 선택 + 발주 (POST /api/orders)
   → supplier 발주 확인 → 생산 진행 → 검수 → 납품 → 리뷰
```

## 핵심 파일 구조

```
fas_analysis/
├── main.py                       # FastAPI 진입점, 정적 UI mount, CORS
├── Procfile                      # Railway 시작 명령
├── requirements.txt
├── routers/
│   ├── vlm.py                    # POST /vlm/analyze-upload (Server_VB + Replicate 토글)
│   ├── matching.py               # POST /api/match-v2, GET /api/company/matches
│   ├── companies.py              # 장비/재질/공정/사업자정보 등록
│   ├── catalog.py                # GET /api/{equipment,material,...} 카탈로그
│   ├── signup.py / auth.py       # 가입 + 로그인 (JWT)
│   ├── rfqs/ quotes/ orders/ reviews/ notifications/ drawings.py
│   ├── admin.py                  # admin 진단 + MV refresh/inspect/repair
│   ├── config.py                 # 서버 설정 진단
│   └── deps.py                   # JWT + 온보딩 자동 검증 + MV refresh helper
├── pipeline/
│   ├── setup_db.py               # 스키마 + 시드 INSERT (--reset 옵션 DROP CASCADE)
│   ├── pipeline_runner.py        # match-v2 진입 (run_pipeline_from_dict)
│   ├── graphrag_transform.py     # Gemini 변환 (VLM raw → IMMA parts)
│   ├── resolve.py                # 재질/공정 normalize, IT/Ra 추출, alias 처리
│   ├── match.py                  # run_hard_filter — 매칭 핵심
│   ├── response.py               # MatchResponse 조립
│   ├── lookup.py                 # FAIL_OPEN_PROCESSES, SAFE_PARENT_FALLBACK 등
│   └── db.py / config.py / models.py / parse.py / seed_neo4j.py
├── lookup_tables/
│   ├── schema.sql                # DDL + company_capability_summary MV 정의
│   ├── lookup_data.json          # 재질 68 강종 + 14 카테고리 + 공정 카탈로그
│   └── equipment_catalog.json    # 59 장비 모델 (제조사 spec sheet 기반)
└── machhub_ui/                   # 정적 frontend
    ├── landing.html / client-register.html / supplier-register.html
    ├── supplier-settings.html    # 4 카드 온보딩 (장비/재질/공정/사업자정보)
    ├── client-dashboard.html / supplier-dashboard.html
    ├── quote-request.html / matching.html
    ├── order-management.html / supplier-workbench.html / supplier-rfq-detail.html
    ├── admin-dashboard.html / admin-operations.html / admin-control-center.html
    ├── imma-phase1-pages.js      # 페이지별 init 함수 (SPA-like 라우터)
    └── imma-common.css / role-workflows.css / imma-api.js / imma-ui-utils.js / auth.js / admin-menu.js
```

## 매칭 핵심 메커니즘

- **hard filter** — `pipeline/match.py`의 `run_hard_filter`가 MV(`company_capability_summary`)를 조회하여 재질 + 공정 + envelope + IT + Ra + availability 6 조건으로 필터링한다.
- **MV 자동 매핑** — `lookup_tables/schema.sql`의 MV 정의가 supplier의 specific 강종 등록 시 부모 카테고리를 CTE에서 자동 union하여 `material_codes`와 `material_category_codes` 양면을 채운다.
- **장비 → 공정/envelope/IT/Ra 자동** — `routers/companies.py`의 `POST /api/equipment`가 `equipment_model_catalog`의 `process_capabilities + category_specs`를 자동 INSERT한다.
- **재질 → supplier 카테고리 등록** — 장비 카탈로그는 공정·envelope·IT·Ra를 커버하고, 재질은 supplier가 카테고리를 선택하면 자식 specific 전체를 자동 INSERT한다.

## 시연 영상 / 시연 계정

> 시연 영상: *[placeholder — 영상 업로드 후 link 교체]*

| 역할 | login_id | password |
|------|----------|----------|
| buyer | `kim_cheolsu` | `demo1234` |
| buyer | `dohyun_buyer` | `demo1234` |
| admin | `admin` | `test1234` |
| supplier (mock 19개) | `c_tech` / `i_aero` / `j_composite` / `l_general` / `b_industry` 등 | `test1234` |

## AI handoff 가이드

본 repo를 받은 AI가 코드 진입 시 권장하는 점검 순서:

1. **endpoint 전체 파악** — `main.py` + `routers/*.py`. FastAPI 66 API + 21 UI 라우트.
2. **매칭 핵심** — `pipeline/match.py`의 `run_hard_filter` + `lookup_tables/schema.sql`의 `company_capability_summary` MV 정의.
3. **frontend 진입** — `machhub_ui/imma-phase1-pages.js`의 페이지별 `init*` 함수가 기존 디자인 DOM을 직접 조회하여 실 API 결과를 hydrate한다.
4. **DB 초기화** — `python pipeline/setup_db.py --reset`으로 스키마 DROP CASCADE + 재생성 + 19 mock supplier 시드 INSERT.

## 설계 결정 요약

- **VLM 출력 방어** — Server_VB의 `_parse_error`(schema echo / word repetition 감지)가 비정상 응답을 걸러내고, GraphRAG가 정합된 입력만 수신하도록 한다.
- **카테고리 확장 정밀도 우선** — `pipeline/match.py`에서 `code_candidates`가 존재하면 정확 코드 매칭을 우선하고, 비어 있을 때만 카테고리 확장으로 넘어간다.
- **재질 역량은 supplier 직접 등록** — 장비 카탈로그는 공정·envelope·IT·Ra를 자동 파생하고, 재질은 supplier가 카테고리를 선택하면 자식 specific 전체를 자동 INSERT한다.

## 상세 문서

| 문서 | 내용 |
|------|------|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | 매칭 파이프라인 모듈별 역할, 온톨로지 검증 3종, DB 스키마 39 테이블, 설계 결정 매트릭스, 프론트엔드 구조 |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | Railway 배포 상세 단계, Server_VB GPU 셋업, Neo4j 터널, 환경변수 전체 명세 |
| [`JOURNEY.md`](JOURNEY.md) | 프로젝트의 의도와 여정 — 도면 인식 접근 변천, 매칭 로직 수렴 과정, 학습한 것 |

## 환경변수

### 필수

| 변수 | 용도 | 예시 |
|------|------|------|
| `DATABASE_URL` | PostgreSQL 연결 | `postgresql://user:pw@host:port/db` |
| `JWT_SECRET` | JWT 서명 (production 시 32+ byte 권장) | `<random>` |
| `VLM_VAST_URL` | Server_VB public endpoint | `https://xxx.trycloudflare.com` |
| `GEMINI_API_KEY` | GraphRAG Gemini 키 | `AIza...` |

### 선택

| 변수 | 용도 |
|------|------|
| `ALLOWED_ORIGINS` | CORS (콤마 구분) |
| `PORT` | 서버 port (Railway 자동 주입) |
| `REPLICATE_API_TOKEN` | 백업 VLM (`routers/vlm.py` 토글) |
| `REPLICATE_MODEL_VERSION` | Replicate 모델 hash |
| `VLM_VAST_TIMEOUT_SEC` | Server_VB timeout 초 (기본 180) |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | Neo4j 사용 시 |

## 구동

### 로컬

```bash
# 1. PostgreSQL 가동 + DATABASE_URL 설정
# 2. .env 에 필수 환경변수 입력
pip install -r requirements.txt
python pipeline/setup_db.py          # 스키마 + 시드 (19 mock supplier + admin + buyer)
uvicorn main:app --reload --port 8000
```

### Railway (권장)

```bash
# GitHub repo connect → auto-deploy
# PostgreSQL add-on → DATABASE_URL 자동 주입
# Variables: JWT_SECRET, VLM_VAST_URL, GEMINI_API_KEY 추가
# Procfile: uvicorn main:app --host 0.0.0.0 --port $PORT
# 시연 전 초기화: python pipeline/setup_db.py --reset && uvicorn main:app ...
```

상세 단계는 [`DEPLOYMENT.md`](DEPLOYMENT.md) 참조.

### Server_VB (GPU 별도)

```bash
# 코드 = 본 repo 외부 (별도 디렉토리). FastAPI + Qwen2.5-VL Student LoRA + Qwen3-VL Teacher
python hybrid_router.py                              # port 8000
cloudflared tunnel --url http://localhost:8000        # public 노출
# 발급된 URL → IMMA backend 의 VLM_VAST_URL 환경변수 입력
```

상세 구성은 [`DEPLOYMENT.md`](DEPLOYMENT.md) 참조.
