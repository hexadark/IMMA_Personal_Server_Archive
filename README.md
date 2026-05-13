# IMMA — Intelligent Manufacturing Matching Agent

제조업 발주자가 도면을 업로드하면 AI(VLM + GraphRAG)가 분석하고 가공 가능한 업체를 자동 매칭하는 플랫폼.

## 구성

- **백엔드**: FastAPI (60 API + 22 UI 라우트)
- **DB**: PostgreSQL 15+ (imma 스키마)
- **그래프 DB**: Neo4j 5.x (493노드 / 1015관계)
- **VLM**: Replicate API (Qwen2-VL 7B 또는 Qwen3-VL)
- **GraphRAG**: LangGraph ReAct + Gemini 3 Flash Preview
- **프론트엔드**: `machhub_ui` (정적 HTML/CSS/JS, FastAPI가 서빙)

## 핵심 흐름 (5단계 매칭)

1. **도면 업로드** (`POST /vlm/analyze-upload`) — 발주자가 도면 이미지 업로드
2. **VLM 분석** — Replicate API가 도면을 V.B raw JSON으로 추출
3. **자동 GraphRAG 변환** — Gemini가 V.B raw를 구조화 JSON으로 변환 (`POST /api/match-v2`)
4. **매칭** — SQL 하드필터 + 장비 검증 + 가용성 점수 산출
5. **견적·발주** — 가공업체가 응답·견적 제출 → 발주자가 비교·발주

## 빠른 시작 (로컬)

```bash
# 1. 환경변수 설정 (.env 파일)
DATABASE_URL=postgresql:///imma
JWT_SECRET=<32바이트 이상 안전 문자열>
REPLICATE_API_TOKEN=<Replicate 토큰>
REPLICATE_MODEL_VERSION=<Qwen2-VL 모델 version hash>
GEMINI_API_KEY=<Gemini API 키>
NEO4J_URI=<bolt://...>
NEO4J_USER=neo4j
NEO4J_PASSWORD=<...>
ALLOWED_ORIGINS=http://localhost:8000

# 2. 의존성 설치
pip install -r requirements.txt

# 3. DB 초기화 (mock 데이터 + 시드)
python pipeline/setup_db.py

# 4. 서버 실행
uvicorn main:app --reload --port 8000
```

## Railway 배포

`RAILWAY_DEPLOY.md` 참조.

## 시연 계정

| 역할 | login_id | password |
|---|---|---|
| buyer | `kim_cheolsu` | `demo1234` |
| admin | `admin` | `test1234` |
| supplier (mock) | `b_industry` 등 19개 | `test1234` |

## 문서

- `IMMA_UI_connection_plan_v3.md`: Phase 1 UI ↔ 백엔드 연결 계획서 (v3 — 최신)
- `IMMA_구현_현황서.md`: 백엔드 기술 스펙 현황
