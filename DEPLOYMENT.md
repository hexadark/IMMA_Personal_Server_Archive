# DEPLOYMENT — 배포 및 셋업 가이드

IMMA 시스템은 세 개의 독립 서비스로 구성된다.

| 서비스 | 역할 | 인프라 |
|--------|------|--------|
| **IMMA Backend** | FastAPI + PostgreSQL. 인증, 매칭, RFQ/견적/발주, 정적 UI 서빙 | Railway (권장) 또는 로컬 |
| **Server_VB (GPU)** | Qwen2.5-VL 7B Student LoRA + Qwen3-VL-30B-A3B Teacher hybrid VLM | vast.ai / Colab / 로컬 GPU |
| **Neo4j (선택)** | GraphRAG 일부 흐름. 현재 시연에서는 미사용 가능 | 로컬 + ngrok TCP 터널 |

**의존성 매트릭스**

```
IMMA Backend ──→ PostgreSQL 15+       (필수, Railway 자동 주입)
             ──→ Server_VB            (필수, VLM_VAST_URL 환경변수)
             ──→ Gemini API           (필수, GraphRAG 변환)
             ──→ Neo4j                (선택, ngrok 터널)
Server_VB   ──→ GPU VRAM 24 GB+      (Qwen3-VL-30B-A3B FP8 기준)
             ──→ Cloudflare Tunnel    (public URL 노출)
```

---

## 1. 로컬 개발 환경

사전 요구사항: Python 3.10+, PostgreSQL 15+, pip.

```bash
# 1) repo clone
git clone https://github.com/Hexadark/IMMA_Personal_Server_Archive.git
cd IMMA_Personal_Server_Archive

# 2) 의존성 설치
pip install -r requirements.txt

# 3) .env 파일 생성 (repo root)
cat > .env << 'EOF'
DATABASE_URL=postgresql://user:password@localhost:5432/imma_db
JWT_SECRET=local-dev-only-secret-32bytes-min
VLM_VAST_URL=https://<server-vb-tunnel-url>
GEMINI_API_KEY=AIza...
EOF

# 4) DB 스키마 생성 + seed 데이터 로딩
python pipeline/setup_db.py

# 5) 서버 기동
uvicorn main:app --reload --port 8000
```

`setup_db.py` 의 실행 내용은 §6 DB 초기화를 참조한다.

기동 후 `http://localhost:8000` 에서 landing 페이지 접근 가능.

---

## 2. Railway 배포

Railway 의 최소 유료 플랜은 Hobby ($5/월) 이다. Trial (무료) 플랜은 2024-08 에 폐지되었으므로, 배포에는 Hobby 이상이 필요하다.

**Step 1** — [railway.app](https://railway.app) 가입 (GitHub 연동) → `New Project` → `Deploy from GitHub repo` → `Hexadark/IMMA_Personal_Server_Archive` 선택. Procfile 자동 인식.

**Step 2** — 프로젝트 dashboard → `New` → `Database` → `Add PostgreSQL`. `DATABASE_URL` 자동 주입.

**Step 3** — `Variables` 탭에서 `JWT_SECRET`, `VLM_VAST_URL`, `GEMINI_API_KEY` 를 수동 입력한다 (§3 환경변수 매트릭스 참조).

**Step 4** — DB 초기화 (첫 배포 후 1 회). Start Command를 일시 변경한다:

```
python pipeline/setup_db.py && uvicorn main:app --host 0.0.0.0 --port $PORT
```

또는 Railway CLI:

```bash
railway run python pipeline/setup_db.py
```

`setup_db.py` 1 회 실행 후 Start Command를 Procfile 기본값 (`web: uvicorn main:app --host 0.0.0.0 --port $PORT`) 으로 복원한다.

**Step 5** — CORS. Railway 부여 도메인 확인 후 `ALLOWED_ORIGINS` 설정:

```
ALLOWED_ORIGINS=https://imma-production.up.railway.app
```

복수 도메인은 콤마로 구분. 미설정 시 `localhost:8000` + `127.0.0.1:8000` 만 허용.

**Step 6** — 헬스체크:

```bash
curl https://<railway-domain>/api/health
# 예상 응답: {"status":"ok","db":"connected"}
```

---

## 3. 환경변수 매트릭스

### 필수 (4 개)

| 변수 | 용도 | 생성 방법 | 비고 |
|------|------|-----------|------|
| `DATABASE_URL` | PostgreSQL 연결 문자열 | Railway: 자동 주입. 로컬: 직접 설정 | `postgresql://user:pw@host:port/db` |
| `JWT_SECRET` | JWT 서명 키 | `openssl rand -hex 32` | 32 바이트 이상. 기본값 `imma-dev-secret`은 production 에서 fail-fast |
| `VLM_VAST_URL` | Server_VB public endpoint | Cloudflare Tunnel 발급 URL | `https://xxx.trycloudflare.com` |
| `GEMINI_API_KEY` | GraphRAG 변환 (Gemini 3 Flash Preview) | Google AI Studio 발급 | `AIza...` |

### 선택 (7 개)

| 변수 | 용도 | 기본값 |
|------|------|--------|
| `ALLOWED_ORIGINS` | CORS 허용 도메인 (콤마 구분) | `http://localhost:8000,http://127.0.0.1:8000` |
| `PORT` | 서버 포트 | Railway 자동 주입 |
| `ENV` | JWT_SECRET fail-fast 트리거 (`production` / `prod`) | (없음) |
| `REPLICATE_API_TOKEN` | 백업 VLM (Replicate 경유, `routers/vlm.py` 토글) | (없음) |
| `REPLICATE_MODEL_VERSION` | Replicate 모델 hash | (없음) |
| `VLM_VAST_TIMEOUT_SEC` | Server_VB 타임아웃 (초) | `180` |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | Neo4j 연결 (선택) | (없음) |

**JWT_SECRET fail-fast 조건**: `JWT_SECRET == "imma-dev-secret"` 이고 다음 중 하나라도 참이면 서버 기동 시 즉시 `RuntimeError`:
- `ENV` 또는 `RAILWAY_ENVIRONMENT` 가 `production` / `prod`
- `RAILWAY_ENVIRONMENT` 또는 `RAILWAY_PROJECT_ID` 환경변수가 존재 (Railway 배포 자동 감지)

해당 로직은 `routers/deps.py` 모듈 레벨에서 import 시 실행된다.

---

## 4. Server_VB (GPU) 별도 배포

Server_VB는 본 repo 외부의 독립 서비스이다. Qwen2.5-VL 7B Student LoRA 와 Qwen3-VL-30B-A3B-Instruct-FP8 Teacher 를 hybrid 라우팅하는 FastAPI 서버이다.

요구 GPU VRAM: 16 GB (Student 단독) / 24 GB+ (Teacher hybrid). 인프라: Colab T4/A100, vast.ai RTX 4090 / A100.

```bash
# Server_VB 디렉토리에서 기동
python hybrid_router.py    # → FastAPI port 8000
```

### Public URL 노출 (Cloudflare Tunnel)

```bash
# cloudflared 설치 후
cloudflared tunnel --url http://localhost:8000
```

출력에서 `https://xxx.trycloudflare.com` URL 을 확인한다. 이 URL을 IMMA Backend 의 `VLM_VAST_URL` 환경변수에 입력한다.

```bash
# Railway Variables 또는 .env
VLM_VAST_URL=https://xxx.trycloudflare.com
```

Cloudflare Tunnel은 프로세스 재시작 시 URL 이 변경된다. 재발급 후 `VLM_VAST_URL` 갱신이 필요하다.

연동 확인: `curl https://xxx.trycloudflare.com/health` 또는 buyer 로그인 후 도면 업로드로 end-to-end 확인.

---

## 5. Neo4j (선택)

GraphRAG 일부 흐름에서 사용된다. 현재 시연에서는 Neo4j 미연결 상태에서도 핵심 매칭이 정상 동작한다.

Neo4j 5.x Community Edition 을 로컬에 설치하고 기동한다 (기본 Bolt 포트 `7687`). Railway 에서 로컬 Neo4j 에 접근하려면 TCP 터널이 필요하다.

### 5.1 ngrok TCP 터널

```bash
ngrok tcp 7687
# 출력 예: Forwarding  tcp://0.tcp.jp.ngrok.io:23176 -> localhost:7687
```

ngrok 이 부여한 주소를 Railway 환경변수에 입력한다:

```
NEO4J_URI=bolt://0.tcp.jp.ngrok.io:23176
NEO4J_USER=neo4j
NEO4J_PASSWORD=<neo4j-password>
```

ngrok 무료 티어는 세션 만료 시 URL 이 변경된다. 시연 도중 ngrok 프로세스가 살아 있어야 한다.

### 5.2 Cloudflare Tunnel 대안

```bash
cloudflared tunnel --url tcp://localhost:7687
```

발급 URL 을 `NEO4J_URI` 에 `bolt://` 프로토콜로 입력한다.

---

## 6. DB 초기화

### 6.1 기본 실행

```bash
python pipeline/setup_db.py
```

실행 내용:
1. `imma` 스키마 DDL (테이블 + MV + seed catalog INSERT)
2. 재질 마스터 데이터 (68 강종 + alias)
3. 장비 카탈로그 (59 모델)
4. mock 업체 19 개 (장비 + 재질 + 공정 + 리뷰 + 90 일 스케줄)
5. admin 계정 (`admin` / `test1234`)
6. demo buyer 6 명 (`kim_cheolsu` / `demo1234` 등)
7. `company_capability_summary` MV refresh

### 6.2 완전 초기화 (`--reset`)

```bash
python pipeline/setup_db.py --reset
```

`--reset` 플래그는 `imma` 스키마를 `DROP CASCADE` 후 재생성한다. 기존 데이터가 모두 삭제된다. 시연 직전 깨끗한 상태가 필요할 때 사용한다.

### 6.3 주의사항

- `ON CONFLICT DO NOTHING` 패턴이므로 중복 실행해도 기존 데이터를 덮어쓰지 않는다.
- mock 업체는 `company_name` 기준 중복 검사. 이미 존재하면 skip.

### 6.4 시연 계정 참조

| 역할 | login_id | password | 비고 |
|------|----------|----------|------|
| buyer | `kim_cheolsu` | `demo1234` | 김철수 / 세진테크 반도체장비사업부 |
| buyer | `dohyun_buyer` | `demo1234` | 이도현 / 도현로보틱스 |
| admin | `admin` | `test1234` | superadmin |
| supplier (19) | `c_tech`, `i_aero`, `j_composite`, `l_general`, `b_industry` 등 | `test1234` | mock 가공업체 |

---

## 7. 트러블슈팅

### 7.1 JWT_SECRET fail-fast

**증상**: 서버 기동 즉시 종료. 로그에 `RuntimeError: JWT_SECRET must be set in deployed/production environment`.

**원인**: Railway 배포 환경에서 `JWT_SECRET` 이 기본값 `imma-dev-secret` 인 상태.

**해결**:

```bash
# 안전한 시크릿 생성
openssl rand -hex 32
# 출력값을 Railway Variables 의 JWT_SECRET 에 입력
```

### 7.2 VLM 타임아웃 (504)

**증상**: `POST /vlm/analyze-upload` 가 504 반환.

**원인**: Server_VB 미기동, Cloudflare Tunnel URL 만료, 또는 GPU cold start (첫 호출 시 100~300 초).

**해결**:
1. `VLM_VAST_URL` 이 현재 유효한 Cloudflare Tunnel URL 인지 확인
2. `VLM_VAST_TIMEOUT_SEC` 를 `300` 이상으로 상향 (기본값 180)
3. UI 에서 "사전 분석 결과로 계속" 버튼으로 fixture fallback 진행 가능

### 7.3 Cloudflare Tunnel URL 재발급

**증상**: `VLM_VAST_URL` 로 요청 시 `502 Bad Gateway` 또는 연결 거부.

**해결**: Server_VB 에서 `cloudflared tunnel --url http://localhost:8000` 재실행 후 새 URL 을 `VLM_VAST_URL` 에 갱신한다.

### 7.4 DATABASE_URL 미설정

**증상**: 로그에 `DATABASE_URL is not set` 또는 DB 관련 `NoneType` 에러.

**해결**: Railway 의 경우 PostgreSQL 플러그인 추가 확인. 로컬의 경우 `.env` 에 `DATABASE_URL` 설정 확인.

### 7.5 Neo4j 연결 실패

**증상**: 매칭 시 GraphRAG Neo4j 관련 에러 로그.

**해결**:
1. ngrok 터널 프로세스가 살아 있는지 확인
2. `NEO4J_URI` 가 현재 ngrok 주소와 일치하는지 확인
3. Neo4j 가 선택 구성요소임을 확인 — 미연결 시에도 핵심 매칭은 정상 동작

### 7.6 CORS 에러

**증상**: 브라우저 콘솔에 `Access-Control-Allow-Origin` 관련 에러.

**해결**: `ALLOWED_ORIGINS` 환경변수에 접속 도메인을 추가한다. 프로토콜 (`https://`) 포함, 끝에 슬래시 미포함.

```
ALLOWED_ORIGINS=https://imma-production.up.railway.app,http://localhost:8000
```

### 7.7 Replicate 호출 실패

**증상**: VLM 호출 시 Replicate API 에러 (Server_VB 가 아닌 Replicate 경유 사용 시).

**해결**: `REPLICATE_API_TOKEN` 유효성 확인. Replicate cold start 시 100~300 초 소요 — 첫 호출 후 워밍업이 필요하다.

### 7.8 Railway 로그 확인

Railway dashboard → `<service-name>` → `Deployments` → `View Logs`, 또는 CLI:

```bash
railway logs
```
