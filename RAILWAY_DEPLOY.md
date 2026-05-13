# Railway 배포 가이드

## 1. Railway 프로젝트 생성

1. [railway.app](https://railway.app) 가입 (GitHub 계정 연동 권장)
2. `New Project` → `Deploy from GitHub repo`
3. `Hexadark/IMMA_Personal_Server_Archive` 선택
4. 자동 빌드 시작 — `Procfile`의 `uvicorn main:app --host 0.0.0.0 --port $PORT` 인식

## 2. PostgreSQL 추가

1. 프로젝트 dashboard → `New` → `Database` → `Add PostgreSQL`
2. `DATABASE_URL` 환경변수가 *자동 주입*됨 (별도 입력 불필요)

## 3. 환경변수 입력

Railway 프로젝트 → `Variables` 탭 → 다음 값 입력:

| 변수 | 값 | 비고 |
|---|---|---|
| `DATABASE_URL` | (자동 주입) | PostgreSQL 추가 시 |
| `JWT_SECRET` | **32바이트 이상 안전 문자열** | `openssl rand -hex 32` 권장. 기본값 `imma-dev-secret` 사용 시 startup fail |
| `JWT_ALGORITHM` | `HS256` | 기본값 그대로 OK |
| `JWT_EXPIRE_HOURS` | `24` | 기본값 그대로 OK |
| `SCHEMA` | `imma` | DB 스키마명 |
| `ALLOWED_ORIGINS` | `https://<railway-도메인>` | 배포 후 도메인 확보 시 입력. 콤마 구분 |
| `REPLICATE_API_TOKEN` | `r8_...` | Replicate VLM 인증 |
| `REPLICATE_MODEL_VERSION` | `ff893abd14e076f5814b31ae12933c27b441a3f90111eaba27b535db3deaec05` | Qwen2-VL 모델 version |
| `GEMINI_API_KEY` | Gemini API 키 | GraphRAG 변환용 |
| `NEO4J_URI` | `bolt://<ngrok>:<port>` | 사용자 로컬 ngrok 터널 |
| `NEO4J_USER` | `neo4j` | |
| `NEO4J_PASSWORD` | (Neo4j 비밀번호) | |
| `ENV` | `production` | JWT_SECRET fail-fast 발동 트리거 |

## 4. DB 초기화 (첫 배포 후 1회)

배포 완료 후 Railway dashboard → `<service-name>` → `Settings` → `Service` → `Start Command` 일시 변경:

```
python pipeline/setup_db.py && uvicorn main:app --host 0.0.0.0 --port $PORT
```

또는 Railway CLI:

```bash
railway run python pipeline/setup_db.py
```

setup_db.py 한 번 실행 후 *원래 start command로 복원*. 단순 방식.

## 5. 시연 fixture INSERT (DB seed 후)

sample_00015 도면을 `drawings` 테이블에 사전 INSERT 필요. 다음 SQL을 Railway PostgreSQL에 실행:

```sql
-- buyer_id는 kim_cheolsu의 buyer_id (setup_db.py 실행 후 확인)
INSERT INTO imma.drawings (drawing_no, file_uri, file_sha256, original_filename, vlm_result_jsonb, buyer_id)
VALUES (
  'P104-0201-02',
  'fixture://sample_00015',
  'fixture_sha256_sample_00015',
  'sample_00015_image.jpg',
  '<v_b_export_samples/sample_00015/v_b_result.json 본문>'::jsonb,
  (SELECT buyer_id FROM imma.buyers WHERE login_id = 'kim_cheolsu')
);
```

또는 본인 로컬 DB에서 fixture row를 `pg_dump`로 추출 후 Railway PostgreSQL에 import.

## 6. CORS 도메인 정합

Railway가 부여한 도메인 (예: `https://imma-personal-server-archive-production.up.railway.app`) 확인 후 `ALLOWED_ORIGINS` 변경:

```
ALLOWED_ORIGINS=https://imma-personal-server-archive-production.up.railway.app
```

## 7. Neo4j 터널 확보

사용자 로컬 Neo4j → ngrok 터널 → Railway에서 `NEO4J_URI`로 접근.

```bash
# 로컬 Neo4j 가동 (예: 7687 포트)
ngrok tcp 7687
# ngrok이 부여한 주소 (tcp://X.tcp.ngrok.io:NNNN)를 NEO4J_URI에 입력
```

시연 도중 ngrok 터널이 *반드시 살아있어야* Railway에서 Neo4j 접근 가능.

## 8. 배포 후 헬스체크

```bash
curl https://<railway-도메인>/api/health
# {"status":"ok","db":"connected"} 예상
```

## 시연 직전 5개 회귀 테스트

`IMMA_UI_connection_plan_v3.md` §10 검증 시나리오 또는 `IMMA_phase1_v3_implementation_summary.md` 체크리스트 참조.

1. `kim_cheolsu/demo1234` 로그인 → `/api/me` `name`/`company_name` 응답 확인
2. `/quote-request` 도면 업로드 → `POST /vlm/analyze-upload` 호출 확인
3. VLM 504/timeout 시 "사전 분석 결과로 계속" 버튼 → fixture fallback → `/api/match-v2` → `/matching-ui`
4. supplier 매칭 수락 → 견적 제출 → buyer 발주 → supplier notifications `order_confirmed`
5. `admin/test1234` 로그인 → `/admin-control-center` pending verify/reject

## 알려진 제약

- **Replicate cold start 100~300초** — 첫 호출 후 1분 안 종료. always-on 원하면 Replicate Deployment 유료 옵션
- **Neo4j ngrok 종속** — 사용자 로컬 종료 시 매칭 GraphRAG 실패
- **JWT_SECRET 기본값 시 startup fail** — production 환경 필수 변경

## 문제 발생 시

Railway dashboard → `<service-name>` → `Deployments` → `View Logs` 확인. 주요 에러:

- `JWT_SECRET must be set in deployed/production environment` → `JWT_SECRET` 비기본값 설정
- `DATABASE_URL is not set` → PostgreSQL 플러그인 추가 확인
- `Replicate API 호출 실패` → `REPLICATE_API_TOKEN` 유효성 확인
- `Neo4j 연결 실패` → ngrok 터널 살아있는지 확인
