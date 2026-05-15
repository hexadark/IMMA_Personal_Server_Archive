# JOURNEY — IMMA 의 의의, 여정, 학습

이 문서는 IMMA(Intelligent Manufacturing Matching Agent) 프로젝트가 거쳐 온 시도와 학습의 기록이다. 완성된 제품의 소개가 아니라, 문제를 인식하고 접근을 선택하고 실패에서 배우며 다시 선택한 과정 자체를 보존하기 위해 쓴다. 포트폴리오 평가자에게는 설계 판단의 근거를, 미래의 본인에게는 왜 그랬는지를 남긴다.

IMMA 는 제조업 발주자가 도면을 업로드하면 AI 가 도면을 분석하고, 가공 가능한 업체를 자동으로 매칭하는 플랫폼이다. 도면 분석(VLM), 제조 지식 그래프 변환(GraphRAG), 6 차원 하드필터 매칭, 견적-발주-생산-납품-리뷰의 전 과정을 하나의 백엔드에서 처리한다. FastAPI, PostgreSQL, Neo4j, LangGraph, Gemini, Qwen3-VL hybrid 로 구성되며, Railway 에 배포되어 운영된다.

---

## 1. 출발점 — 도면 한 장의 문제

제조업에서 발주자와 가공업체는 서로를 찾지 못한다.

발주자는 소량 발주를 기피당하고, 견적이 불투명하고, 납기와 품질이 불안하다. 가공업체는 반대로 장비가 놀고, 신규 도면의 가공 내용을 파악하는 부담이 크고, 대금 회수가 불안하여 신규 수주를 꺼린다.

양쪽 모두 상대를 신뢰할 정보가 부재하다는 것이 문제의 본질이다.

IMMA 의 핵심 인식은 단순하다. AI 가 도면을 읽고 업체를 자동으로 매칭하면 양측 모두에게 신뢰 가능한 정보가 형성된다.

도면 분석부터 매칭, 견적, 발주, 생산, 납품, 리뷰까지 한 플랫폼 위에 흐르면 발주자는 왜 이 업체인지를 알 수 있고, 가공업체는 왜 이 도면이 자기에게 적합한지를 알 수 있다.

카카오택시가 수요와 공급을 실시간으로 연결하듯, 제조업에도 같은 구조가 가능하다는 확신이 출발점이었다.

과기정통부 AI 경진대회 출품작이자 부트캠프 팀 프로젝트로 시작했다. 팀은 세 명이었고, 도면 인식 VLM 담당, 서버와 매칭 파이프라인 담당(본인), DB 초안과 프론트엔드 정적 디자인 담당으로 나뉘었다.

경쟁사 분석에서 CAPA(국내 제조 네트워크 기반 RFQ/입찰/파트너 탐색), Xometry(글로벌 제조 AI 마켓플레이스, 자동 견적), Geomiq(유럽 중심 디지털 제조, 신속 견적/온디맨드) 를 살펴보았다. 해외 플랫폼 다수가 3D CAD 중심인 반면, 국내 중소기업 제조 현장은 PDF/이미지/2D 도면 활용 비중이 높다. IMMA 는 국내 시장 환경에 최적화된 2D 도면 분석 및 발주 프로세스를 제공한다는 포지션을 잡았다. 단순 견적 요청 전달이 아닌 도면 구조화, 누락 정보 보완, 발주 조건 정리를 AI 가 수행하고, 공급자 친화형 모바일 UX 를 지향한다는 것이 차별점이다.

정부와 기업이 추진해 온 스마트제조혁신이 개별 공장의 내부 자동화에 집중되어 있었다면, IMMA 는 그 다음 단계인 수요와 공급의 실시간 연결을 구현하는 제조 AI 플랫폼이라는 비전을 품었다. 스마트공장이 더 많이 생산할 수 있게 만드는 기술을 넘어, 실제로 일감이 지속적으로 들어오게 만드는 "라스트 마일"의 구현이다.

초기 기술 스택으로 Qwen3 임베딩/리랭커, Qdrant, Neo4j, LlamaIndex, LangGraph, Gemini/Gemma LLM 을 확정하고, VDI 3682(공정-입출력-설비 관계), DIN 8580(공정 분류 체계), MaRCO(자원 매칭 골격), SAREF4INMA(재질/배치/측정/추적성) 4 개 외부 온톨로지의 조합을 설계했다. VLM JSON 스키마도 여러 버전을 거쳤다. 설계의 범위가 넓어질수록 "지금 당장 매칭이 돌아가는가"라는 질문이 더 날카롭게 다가왔다.

**학습**: 기술이 아니라 문제가 먼저다. 도면 인식 AI, 온톨로지, 벡터 검색 — 이 모든 기술은 "발주자와 가공업체를 연결한다"는 한 문장의 문제에 종속된다. 기술 스택을 먼저 선택하고 문제를 끼워 맞추는 순서가 아니라, 문제의 본질에서 기술을 골라야 했다. 매칭이 안 되면 나머지가 다 의미 없다 — 이 단순한 사실이 Phase 1 의 범위를 "SQL 하드필터로 매칭이 돌아가는 데모"로 좁혔다. 기획과 설계에 시간을 쏟는 것은 좋지만, 동작하는 코드가 없으면 기획서는 종이 위의 약속일 뿐이다.

---

## 2. 도면 인식의 도전 — 정확도 vs 분량 trade-off

거대한 2D 도면을 통째로 AI 에게 넘기면 작은 글씨와 기호가 누락된다. 해석 오류가 곧바로 매칭 오류로 이어지므로, 도면 인식의 정확도는 파이프라인 전체의 천장이 된다.

처음에는 3단계 집중 전략을 구상했다.

YOLO 가 도면 전체에서 치수선과 주석의 위치만 빠르게 탐색하고(Spotting), 찾은 패치만 선명하게 잘라내어(Zooming), VLM(Donut 계열) 이 잘린 이미지를 보고 기호의 의미를 정확히 데이터화하는(Reading) 구조였다. 복잡한 도면을 나눠서 정밀하게 읽어 정확도를 극대화한다는 것이 핵심 가치였다.

ROBOFLOW 소규모 데이터 테스트 후 ezdxf 기반 규칙 기반 증강 데이터를 생성하고, YOLO 와 DONUT 을 학습시키는 로드맵까지 수립했다. 오픈 데이터셋 1500 개를 확보하고, 실제 기계 제작 도면(저작권 보유) 100 개도 테스트 데이터로 준비했다.

그러나 실 운영을 시작하면서 VLM 의 zero-shot 능력이 예상을 크게 넘어섰다. Qwen3-VL 계열 모델이 별도 위치 검출기 없이도 도면 전체를 구조화된 JSON 으로 변환하는 성능을 보여주었다.

YOLO + DONUT + VLM 3 모델 조합의 복잡성 대비 VLM 단독의 단순성이 정확도에서도 밀리지 않는다는 사실을 확인한 뒤, VLM 단독 경로로 수렴했다. 도면에서 추출해야 하는 정형 데이터 — 기하학적 치수, GD&T, 재질 사양, 표면 거칠기 — 를 VLM 이 단독으로 구조화된 JSON 으로 변환할 수 있다면, 검출기 + 파서 + 해석기의 3 단 파이프라인은 복잡성만 추가할 뿐이다.

견적 통계 모델(XGBoost 기반 견적 범위 추정)과 대화형 에이전트(도면 피드백 + 공정 설계 + 유사 사례 검색 오케스트레이션) 또한 초기 로드맵에 있었으나, MVP 시점의 의식적 우선순위로 보류했다.

매칭이 돌아가지 않으면 나머지가 전부 의미 없다는 판단이었다. 도면 분석이 아무리 잘 되어도, 온톨로지가 아무리 정교해도, 결국 "이 도면에 맞는 업체를 찾아준다"가 플랫폼의 존재 이유이다. 서비스 흐름에서 매칭이 중간에 있고, 그 앞(도면 분석)과 뒤(견적, 발주, 생산)가 전부 매칭을 위해 존재하는 구조이기 때문이다.

Gemini/Gemma LLM 으로 매칭 근거를 자연어로 설명하는 기능("이 업체를 추천하는 이유")도 검토했다. 구현 자체는 API 호출 한 번으로 1-2 시간 안에 가능하지만, 후보가 3 개뿐인 현 데이터 규모에서 Reranker 순위 변동이 미미하고, 수주 이력이 없으면 벡터 검색에 넣을 것이 없다. LLM 설명 생성은 매칭 후보가 많아지고, 차별화가 필요해지는 시점에 도입하는 것이 적절하다.

초기 5 단계 개발 로드맵(Phase 1: ROBOFLOW + ezdxf 증강 → Phase 2: YOLO + DONUT 학습 → Phase 3: 가공 장비 명세 + 공정 가이드 → Phase 4: 난이도 점수화 + 실시간 유휴률 매칭 → Phase 5: 견적 리포트 + 대화형 에이전트) 중 Phase 1-2 의 도면 인식 파트가 VLM 단독으로 수렴하면서, 로드맵 자체의 전제가 바뀌었다. 도면 인식의 복잡성이 줄어든 만큼, 그 자원을 매칭 파이프라인의 정합성에 집중할 수 있게 되었다.

**학습**: 학계 표준 stack(YOLO + DONUT + VLM 조합)이 현 VLM 의 zero-shot 능력에 압도되는 시점이 왔다. 복잡한 파이프라인이 항상 정답은 아니며, 단순화가 정답일 수 있다. 기술 선택의 기준은 "학계에서 표준인가"가 아니라 "지금 이 문제에 실질적으로 더 나은가"이다. 그리고 한 곳의 단순화가 다른 곳에 집중할 여유를 만든다.

---

## 3. VLM hybrid 의 설계 철학 — Student LoRA + Teacher

VLM 단독 경로로 수렴한 뒤에도 풀어야 할 문제가 남았다. 모든 도면을 30B 급 Teacher 모델에게 보내면 GPU 비용이 폭주하고, 모든 도면을 7B 급 Student 에게 보내면 복잡한 도면에서 정확도가 한계에 부딪힌다.

이 trade-off 를 Student LoRA + Teacher hybrid 구조로 흡수했다.

Student LoRA(Qwen2.5-VL 7B)가 자주 호출되는 일반 도면을 처리하고, Teacher(Qwen3-VL-30B-A3B-Instruct-FP8)가 어려운 케이스를 백업한다. 두 모델은 별도 GPU 서버(Server_VB)에서 FastAPI + `hybrid_router.py` 로 운영되며, Cloudflare Tunnel 을 통해 Railway 에 배포된 IMMA 백엔드(`VLM_VAST_URL` 환경변수)와 연결된다.

GPU 비용과 서비스 가용성을 분리 관리한다는 것이 핵심 결정이었다. 백엔드는 Railway 에서 상시 가동되고, GPU 서버는 필요할 때만 켜서 비용을 통제한다. Replicate API 를 fallback 으로 두어, 자체 GPU 서버가 내려가 있어도 도면 분석이 가능하게 했다. 다만 Replicate 의 cold start 가 100~300 초에 달하므로, 이를 사용자 경험 차원에서 흡수하는 별도 설계가 필요해졌다(9 절에서 서술).

VLM 팀원이 `server_demo_ver_E` 패키지에서 Qwen3-VL 기반 analyze_drawing_png 함수를 구현했고, lookup(사전 결과)/cloud(Replicate API)/live(stub) 3 모드를 지원한다. 운영 흐름에서는 `routers/vlm.py` 가 multipart 이미지를 받아 buyer 인증과 소유권을 확인한 뒤 Replicate API 를 호출하고, 결과를 `drawings.vlm_result_jsonb` 에 저장하여 `drawing_id` 를 반환한다. 후속 `/api/match-v2` 는 이 drawing_id 만 받으면 자동으로 GraphRAG 변환을 거쳐 매칭을 수행한다.

VLM 이 반환하는 JSON 에는 title_block(표제란), view(투상도), notes(주서) 등의 구조가 포함되며, 여기에서 부품명, 재질, 치수, 공차, 표면 거칠기, 후처리, 참조 표준 등을 추출한다. 이 JSON 은 도면 원문 보존 톤으로 작성되며, IMMA 내부 스키마와의 정합은 GraphRAG 변환 레이어(5 절)가 담당한다. VLM 과 GraphRAG 사이의 역할 분리가 파이프라인 전체의 유연성을 보장한다.

**학습**: 모델 크기와 호출 빈도의 trade-off 는 "어느 쪽이 옳은가"가 아니라 "어떻게 분리 운영할 것인가"의 문제다. 단일 모델이 아닌 hybrid 구조로 비용과 정확도를 동시에 관리할 수 있고, 인프라 계층(GPU 서버 / 앱 서버)을 분리하면 각각의 가용성을 독립적으로 통제할 수 있다.

---

## 4. 재질 정규화의 학습 — 단일 매핑의 한계

도면에서 추출된 재질 텍스트를 표준 코드로 변환하는 것은 보기보다 어려운 문제였다.

처음에는 단순한 접근을 시도했다. "스테인리스" 라는 텍스트가 들어오면 STS 로 매핑하면 될 것이라 생각했다.

그러나 STS304, STS316, STS430 은 가공성, 내식성, 가격이 모두 다르고, 가공업체가 다루는 재질 범위도 다르다. 단일 매핑으로는 어떤 스테인리스인지 식별할 수 없었다. 러시아 ГОСТ 표기(СЧ 18-36 ГОСТ 1412-85), 독일 DIN 표기(20MnCr), 일본 JIS 표기(SUS304) 등 국제적으로 다양한 표기가 도면에 등장하는 것도 문제를 복잡하게 했다.

이 한계를 인식한 뒤 4층 구조로 재설계했다. token(도면 원문 텍스트) → family(재질 계열, 예: STS) → candidate_grade_set(후보 등급 집합, 예: [STS304, STS316, STS430]) → confidence(특정 가능도, 예: 0.3). confidence 가 낮으면 발주자에게 확인 요청을 보내는 구조다. 불확실성을 시스템 안에서 명시적으로 관리한다는 것이 핵심이다. "정확한 답을 내놓는다"가 아니라 "얼마나 확실한지를 함께 제공한다".

KS 표준 조사 과정에서 더 근본적인 사실을 발견했다. KS↔JIS↔AISI 간에 공식 대응표가 존재하지 않는다. KS D 3698(STS), KS D 3503(SS), KS D 3752(SM) 등은 KS 고유 표준이고, JIS 나 AISI/SAE 와의 매핑은 비공식적이다. MISUMI, SteelNumber 같은 제3자 자료를 참조하되 confidence 를 별도로 관리하는 방식을 택했다. IDT(동일 채택) 표준은 ISO 원본 데이터를 그대로 사용할 수 있지만, MOD(수정 채택)인 KS B 0401(끼워맞춤)이나 KS B 0211/0214/0235(나사 공차)는 ISO 데이터로 단순 치환이 불가하여 KS variant 별도 처리가 필요했다. 폐지 표준의 이행 관계(KS D 4101/4301 → SPS 단체표준 등)도 추적하여, 구도면에 GC200, SC480 으로 찍혀 있는 legacy 코드를 해소할 수 있게 했다.

KS/ISO 룩업 테이블은 GPT-Pro 를 활용하여 일반공차, 주조/주강공차, 기어 정밀도, 표면조도, 나사 공차, 재질 대응표, 경도 환산표, Legacy 폐지코드, 공정별 달성 가능 공차/조도, 재질-공정 호환성, 공정 순서 제약 등 19 개 테이블로 구축했다. 이 룩업 데이터가 resolve.py 의 일반공차 fallback(KS B ISO 2768-m 등급 → IT 등급 추정)과 match.py 의 장비 검증(공정별 달성 가능 IT/Ra 교차 검증)의 기반이 된다.

실제 파이프라인에서 재질 해소는 7 단계로 구현되었다. 직접 코드 매칭 → alias 매칭 → 규격 접미사 제거 후 재시도(ГОСТ, KS, JIS, ASTM 등 규격 접두/접미사를 제거하여 핵심 코드만 추출) → 카테고리 이름 LIKE 매칭 → 패턴 기반 추정(정규식으로 12L14 → free_cutting_steel, СЧ 18-36 → gray_cast_iron, 20MnCr → alloy_steel 등 추정) → client_notes fallback(VLM 이 재질을 추출하지 못했을 때 발주자 입력을 사용) → 최종 실패 시 rejected. 쾌삭강(12L14 등)이 일반 탄소강 업체에서도 가공 가능하다는 사실을 반영하여 carbon_steel fallback 도 추가했다.

RDBMS 에는 68 개 재질과 219 개 별칭, Neo4j 에는 276 개 별칭이 등재되어 있다. 재질 카테고리 15 종, 호환성 매트릭스 224 행, 물성 데이터 68 건이 해소 과정의 기반이다.

일반공차 fallback 도 resolve.py 의 중요한 결정이다. 끼워맞춤이나 개별 공차가 없는 도면에서 tightest_it_grade 가 null 일 때, 참조 표준에서 일반공차 등급(KS B ISO 2768-m 등)을 파싱하고, 룩업 JSON 의 IT 폭 테이블(ISO 286 기준 50 mm 대표 구간)과 비교하여 가장 가까운 IT 등급을 추정한다. 추정 실패 시에는 등급별 대표 IT(f ≈ IT12, m ≈ IT14, c ≈ IT16, v ≈ IT17)로 fallback 한다. nullable 추출값 정책에서 재질, 공정, 수량 부재는 rejected, envelope/IT/Ra 부재는 해당 필터 생략 + 경고 표시로 분기한다.

**학습**: 정규화는 "매핑 테이블 하나 만들면 끝"이 아니다. 도메인 데이터의 실제 구조(공식 대응표의 부재, 업계 관행의 비공식성)를 직면해야 정직한 설계가 가능하다. confidence 를 명시적으로 관리하는 것이 불확실성을 숨기는 것보다 낫다.

---

## 5. GraphRAG 의 본 정의 — 검증이 아닌 변환

Neo4j 에 493 노드와 1015 관계로 구축한 제조 지식 그래프의 역할을 정의하는 과정에서 중요한 전환이 있었다.

초기 Neo4j 설계는 5 층 구조였다. 1 층(원본 보존 — OWL/RDF 를 n10s 로 namespace 포함 import), 2 층(IMMA 정규화 — LPG 친화 노드로 flatten), 3 층(규칙 실체화 — OWL restriction 대신 명시적 관계), 4 층(ABox — 업체별 실제 설비/공차/조도 인스턴스), 5 층(RAG 제어 — feasibility edge + Cypher 질의). VDI 3682, DIN 8580, MaRCO, SAREF4INMA 외부 온톨로지를 import 하여 공정-입출력-설비 관계, 공정 분류 체계, 자원 매칭 골격, 재질/배치/측정/추적성을 통합하는 야심 찬 설계였다.

처음에는 Neo4j 를 검증 도구로 사용하려 했다. Cypher 쿼리를 수동으로 작성하여, 매칭 결과가 온톨로지 상의 재질-공정 호환성이나 공정 순서 제약에 부합하는지 사후 검증하는 구조였다. 재질-공정 호환성(224 행), 공정 순서 제약(절대 9 + 권장 11 + 동시 불가 2 = 22 규칙), 도면 피드백(공차 누락, 정밀 공정 부재, GDT vs IT/Ra 누락 등 7 규칙) 세 종류의 온톨로지 검증이 여기에서 나왔다. 이 검증들은 정보성 경고로 매칭 응답에 첨부되며, hard filter 와는 독립적으로 작동한다.

그러나 VLM 의 raw 출력은 도면 원문 보존 톤이라 IMMA 의 내부 스키마와 직접 대응하지 않는다.

raw JSON 을 IMMA 스키마로 변환하는 과정 자체가 온톨로지의 도움을 필요로 한다는 인식이 전환점이었다. "재질 텍스트를 표준 코드로 변환한다", "이 재질에 이 공정이 호환되는지 확인한다", "이 공정 다음에 어떤 공정이 와야 하는지 확인한다", "이 공정으로 달성 가능한 IT/Ra 를 확인한다" — 이 모든 것이 온톨로지에 물어봐야 하는 질문이었다.

Cypher 를 수동으로 작성하는 것이 아니라, AI 가 그래프 도구를 자율적으로 호출하여 VLM raw 를 IMMA 스키마로 변환하는 구조로 재설계했다.

LangGraph ReAct 에이전트에 Gemini 3 Flash Preview(thinking_level=low)를 결합하고, lookup_material(재질 → 카테고리), lookup_compatibility(재질 × 공정 호환성), lookup_sequence(공정 순서), lookup_tolerance(달성 가능 IT/Ra) 4 개 도구를 부여했다. AI 가 도구를 자율적으로 조합하여 변환을 수행하고, 평균 10 초대에 결과를 반환한다. Neo4j 연결이 실패하면 도구 없이 LLM 만으로 변환을 시도하는 graceful degradation 도 구현했다. 발주자가 직접 parts 를 전달하면 GraphRAG 단계 자체를 생략하여 CLI/테스트 흐름을 보존한다.

SYSTEM_PROMPT 에는 part_name 추출 우선순위(title_block 최우선, view.name 채택 금지)와 원문 보존 정책(part_name, material.raw_text, referenced_standards 는 도면 원문 유지, 자의적 한국어 번역 금지)을 명시했다. 이 제약들은 VLM 출력이 GraphRAG 를 거치면서 왜곡되는 것을 방지하기 위한 것이다.

초기 5 층 Neo4j 설계의 야심에 비하면, 현재 GraphRAG 는 훨씬 실용적인 형태로 수렴했다. 5 층 전부를 구현하지는 않았지만, Material, MaterialCategory, MaterialAlias, Process, ProcessFamily, Equipment, CompatibilityRule, SequenceRule 등의 노드와 그 관계들이 4 개 도구의 Cypher 쿼리를 통해 AI 에게 제공된다. 남은 층(ABox 업체별 인스턴스, RAG 제어 feasibility edge)은 운영 단계에서 점진적으로 채워 나갈 수 있다.

**학습**: 온톨로지는 검색 도구가 아닌 변환 매개체로 사용할 수 있다. 수동 Cypher 작성보다 AI 자율 도구 호출이 유연하고, SYSTEM_PROMPT 의 명시적 제약이 변환 품질을 통제하는 핵심 수단이 된다. 그리고 야심 찬 설계의 전부를 한 번에 구현하지 않아도, 핵심 가치가 동작하는 부분부터 구현하면 된다.

---

## 6. 매칭 hard filter 의 6 조건 + parent fallback 설계 결정

매칭의 핵심은 "이 도면에 맞는 업체를 찾는다"이다. 매칭이 돌아가지 않으면 플랫폼의 존재 이유 자체가 없다. 매칭 파이프라인의 설계에서 가장 많은 시간과 판단이 소요되었다.

파이프라인은 parse(VLM JSON → VlmPart) → resolve(재질 7 단계 해소 + 일반공차 fallback + 축물/각형물 분기) → match(하드필터 SQL + 장비 검증) → response(응답 조립, 사내/외주 공정 분리) 4 단계로 구성된다. pipeline_runner 가 진입점이며, 각 단계의 출력이 다음 단계의 입력이 된다.

hard filter 는 재질, 공정, envelope(가공 크기), IT(공차 등급), Ra(표면 거칠기), availability(가용 상태) 6 차원으로 구성된다. PostgreSQL 의 Materialized View(`company_capability_summary`)가 업체 역량을 사전 집계하고, 매칭 시점에 동적 SQL 로 필터링한다. MV 는 `companies.status = 'active' AND onboarding_status = 'verified' AND accepting_orders = true` 인 업체만 포함하며, CTE 단일 지점에서 자식 공정 보유 업체의 부모 공정을 자동으로 확장한다. 예를 들어 cylindrical_grinding 을 보유한 업체의 MV process_codes 에 grinding 이 자동으로 포함된다. 이 parent-child 확장을 MV CTE 한 곳에서 처리한다는 것이 중요하다. SQL 하드필터, 장비 검증, seed 데이터가 각각 독립적으로 parent-child 를 다루면 비대칭이 발생할 수 있으므로, MV CTE 단일 지점에서 확장을 일원화하여 모든 하위 소비자가 동일한 process_codes 를 참조하도록 설계했다.

매칭 응답 후보 객체에는 match_run_id, rank_no, rfq_part_id, 기술/가용성/품질/종합 4 종 점수, availability_info, equipment_summary 가 포함된다. `_save_match_history()` 실패 시에는 sigh-and-continue 가 아닌 500 fail-fast 로 응답한다. 매칭 이력과 supplier 알림이 buyer 응답과 결합되어 있어, 부분 실패 시 비대칭이 발생하기 때문이다.

parent fallback 설계에서는 화이트리스트 방식을 선택했다. `SAFE_PARENT_FALLBACK = {turning_rough, turning_finish, milling_rough, milling_finish}` 4 개만 부모로의 fallback 을 허용한다. 동일 장비가 황삭과 정삭을 모두 수행할 수 있다는 물리적 사실에 근거한 결정이다. gear_grinding, honing, lapping 같은 전용 장비 공정은 일반 grinder 로 대체할 수 없으므로 fallback 에서 제외한다.

재질 매칭에서는 2 단계 전략을 쓴다. 먼저 정확한 material_code 로 매칭을 시도하고, 결과가 0 건이면 material_category_code 로 확장한다. 카테고리 확장 시 false positive 위험이 있지만, 후보를 0 건으로 돌려보내는 것보다 넓은 후보에서 장비 검증 단계로 좁히는 것이 낫다는 판단이다.

장비 검증 단계에서는 공정을 정밀(grinding 류, EDM, honing, lapping, boring — IT/Ra 직접 비교), 중간(turning, milling, drilling, threading 등 — typical 범위 내 확인), 비가공(heat_treatment, welding, surface_treatment 등 — IT/Ra 개념 자체가 없으므로 스킵) 3 분류로 나누어 처리한다. 플라스틱이나 복합재 같은 비금속 재질에 금속 기준 IT/Ra 를 적용하면 달성 불가 판정이 발생하므로, `MATERIAL_PROCESS_CAPABILITY_OVERRIDES` 18 행으로 별도 기준을 적용한다.

가용성 점수도 단순 평균이 아니라, 사내 공정 가능 장비의 `equipment_daily_schedule.available_hours` 합산으로 계산한다. 시드 한계로 인해 납기 데이터가 부재한 경우 0.7 fallback, 전 공정 외주 RFQ 는 0.9, 장비 0 대 신고 업체는 0.3 으로 분기한다.

GDT(기하공차)는 의식적으로 hard filter 에서 제외했다. 업체 CMM/검사 데이터 없이 GDT 를 매핑하면 적합한 업체가 false negative 로 탈락하는 위험이 크다. 향후 리스크 태깅과 가중치 형태로 소프트 시그널로 도입하는 방향을 열어 두었다.

매칭 결과는 "추천"이 아니라 "후보"라는 것이 중요한 서술 원칙이다. 정확한 가공 가능 여부는 업체 확인이 필요하다. `match_reasons` 는 SQL 조건 통과 사실만 기술하며, "납기 충족" 같은 검증되지 않은 표현은 사용하지 않는다. `material_match_type` (code/alias/category) 을 응답에 포함하여 발주자가 매칭 근거의 정확도를 판단할 수 있게 한다.

rejected 부품(재질, 공정, 수량 중 필수 정보 누락)은 별도 구조(status: "rejected", missing_fields, message)로 반환하여 발주자에게 무엇을 보완해야 하는지 안내한다. envelope 이 없으면 크기 필터를 생략하되 "크기 미검증" 경고를, tightest_it_grade 가 없으면 공차 필터를 생략하되 "공차 미검증" 경고를 표시한다.

**학습**: 카테고리 fallback 은 물리적 장비 호환성의 진실에 종속되어야 한다. 소프트웨어 로직이 아무리 깔끔해도, 전용 장비를 일반 장비로 대체할 수 없다는 제조 현장의 사실을 무시하면 매칭 품질이 무너진다. 그리고 false negative(적합 업체 탈락)가 false positive(부적합 업체 포함)보다 사용자 경험에서 더 치명적이라는 것이 반복적으로 확인되었다.

---

## 7. 외주 공정의 모델링 — SQL 하드필터에서 제외

열처리, 표면처리, 주조, 용접 등 8 개 공정은 `FAIL_OPEN_PROCESSES` 로 분류하여 SQL AND 조건에서 제외했다. 이 공정들은 대부분의 가공업체가 직접 수행하지 않고 외주망을 통해 처리한다.

이 결정의 배경에는 IMMA 의 본질적 모델 선택이 있다. IMMA 는 발주자와 단일 업체의 직접 매칭이지, 발주자와 컨소시엄의 매칭이 아니다.

단일 업체 매칭 모델에서 외주 처리 능력까지 hard filter 에 포함하면, 열처리 설비를 직접 보유하지 않는 대다수 정밀 가공업체가 탈락한다. 실제로 대부분의 중소 가공업체는 열처리, 표면처리, 도금 같은 공정을 협력업체에 외주한다. 이를 SQL AND 조건에 포함하면 적합한 업체의 대부분이 탈락하는, false negative 의 전형이 된다.

대신 외주 공정은 SQL 단계에서 빠지고, 장비 검증 단계에서 fail-open 처리된 뒤, 업체의 외주망 확인과 견적 단계에서 실질적으로 검증된다. 응답에서 사내 공정과 외주 공정을 분리하여 발주자에게 명시적으로 알린다. 이 분리는 response.py 가 매칭 응답을 조립할 때 `inhouse_process_codes` 와 `outsourced_process_codes` 를 구분하여 표시하는 것으로 구현된다.

향후 외주 capability 를 시그널로 활용하는 방안(예: "외주망 확인 권장" 정보성 경고, 사내 보유 업체 우대 스코어링)도 열어 두었으나, 현 시점에서는 견적 단계 위임이 가장 정직한 처리다.

이 결정은 mock 업체 데이터로도 확인되었다. 19 개 mock 업체(A 정밀부터 O 워터젯까지) 중 열처리 설비를 직접 보유한 업체는 소수이며, 대부분 외주로 처리한다. FAIL_OPEN_PROCESSES 에 포함된 8 개 공정 — heat_treatment, surface_treatment, casting, welding 등 — 을 SQL 에서 제외하니 적합 후보가 크게 증가했다.

**학습**: 매칭 모델이 "발주자 ↔ 단일 업체"인지 "발주자 ↔ 컨소시엄"인지에 따라 hard filter 의 범위가 완전히 달라진다. 이 결정은 기술적 결정이 아니라 사업 모델의 결정이며, 파이프라인 코드에 직접적으로 반영된다.

---

## 8. PostgreSQL + Neo4j 분담 — 인메모리 룩업의 발견

초기 아키텍처에서는 Qdrant(벡터 DB) + Qwen3-Embedding-0.6B + Qwen3-Reranker-0.6B/4B + LlamaIndex 를 결합한 풀 RAG stack 을 설계했다. 벡터 검색이 업체 프로필, 과거 수주 이력, 공정 가이드의 시맨틱 유사도를 담당하고, Neo4j 온톨로지가 공정 실현가능성 추론을 담당하며, SQL 이 하드 제약 필터를 담당하는 구조였다. semantic score 와 graph-consistency score 를 분리한 뒤 late fusion 으로 결합하고, Complexity Router 가 질의 난이도에 따라 경로를 분기하는 설계까지 했다.

그러나 실제 운영에서 결정적인 사실을 발견했다.

IMMA 의 매칭 기준 — 재질, 공정, 크기, 공차 등급, 표면 거칠기, 가용 상태 — 은 전부 구조화된 데이터다. 시맨틱 유사도가 필요한 순간이 없다.

PostgreSQL 카탈로그의 인메모리 룩업이 0 ms 에 응답하는 반면, 벡터 검색은 0.2 초의 latency 와 임베딩 비용과 인덱싱 부담을 추가한다. 수치(공차, 치수, 조도)는 임베딩에 맡기지 않고 SQL 필터로 처리해야 한다는 초기 설계 원칙이 여기서 현실로 확인되었다.

벡터 검색이 필요해지는 시점은 "이 도면과 비슷한 작업을 한 업체"를 찾는 유사 수주 이력 검색, 또는 "정밀 의료기기 부품 경험이 많다"는 업체 자유 텍스트 프로필 검색이다. 그런데 수주 이력 데이터 자체가 아직 없고, 업체 프로필은 구조화 필드로 충분히 커버된다. 데이터가 쌓이고 비구조화 검색이 실제로 필요해질 때 벡터를 붙이면 된다. 이것은 "벡터 검색을 포기했다"가 아니라 "벡터 검색이 가치를 추가하는 시점을 정확히 알고 있다"이다. 선제적 도입의 유혹을 참는 것도 설계 판단이다.

이 발견은 초기 설계 전체를 재평가하게 했다.

초기에 구상했던 Hybrid Retrieval 구조를 다시 살펴보면 — SQL 하드필터로 후보를 좁히고, Qdrant 벡터 검색으로 유사도를 추가하고, Neo4j 그래프 경로로 온톨로지 정합성을 검증하고, Reranker 로 최종 순위를 매기는, 4 단계 파이프라인이었다. 여기에 Complexity Router 를 붙여 질의 난이도에 따라 경로를 분기하는 설계까지 했다. 단순 질의(재질, 치수 조회)는 SQL 직접 응답, 중간 질의(업체 검색, 유사 이력)는 벡터 + reranker, 복합 질의(공정 설계, 실현 가능성)는 KG path + 벡터 + reranker + 에이전트.

이 설계의 복잡성에 비해 실제 데이터의 성격은 단순했다. 제조 매칭의 기준이 전부 구조화된 데이터인 이상, 벡터 검색과 reranker 는 실제로 추가하는 가치가 없었다. semantic score 와 graph-consistency score 를 late fusion 으로 결합하는 것은 이론적으로는 아름답지만, 실 데이터에서 의미를 갖지 못했다.

Neo4j Cypher 로 resolve/match 를 전환하는 것도 검토했으나, 인메모리 룩업(0 ms)이 Cypher 쿼리(0.2 초)보다 빠르고, 원천 단일화 외에 실질적 이익이 없었다. 재질 alias 해소, 공정 parent-child 매핑, IT/Ra 룩업 모두 JSON 파일에서 메모리로 로드하여 dict 탐색으로 처리하는 것이 DB 왕복보다 빠르다. Neo4j 는 GraphRAG 변환 레이어(5 절)에서 AI 가 자율적으로 도구를 호출하는 매개체로 사용하고, hard filter 자체는 PostgreSQL MV 에서 처리하는 분담이 확정되었다. 데이터베이스 스키마는 39 개 테이블, mock 업체 19 개, 장비 모델 59 개, 장비 일일 스케줄 6120 행(90 일)으로 MVP 시연을 지탱한다.

최종적으로 IMMA 의 기술 분담은 다음과 같이 정리된다. PostgreSQL 이 카탈로그, 트랜잭션, MV 의 정합성을 담당하고 1 차원 hard filter 의 가장 빠른 길이다. Neo4j 는 재질-공정-장비-호환성-순서의 그래프 관계 자체가 필요한 곳, 즉 GraphRAG 변환 매개체로 사용한다. FastAPI 는 Python 단일 언어 stack + async + 자동 OpenAPI 의 이점을 살린다. JWT(HS256) + 3-tier 게이팅은 B2B 마켓플레이스 표준이다. 인메모리 룩업은 도메인 지식이 유한한 카탈로그이므로 인덱싱 부담이 없다.

**학습**: AI + RAG = 만능이 아니다. 문제의 본질이 hard filter + 그래프 추론이라면 벡터 검색은 noise 다. "AI 시대니까 벡터 검색을 써야 한다"는 통념에 대한 정직한 반례를 직접 경험했다. 기술 선택은 문제의 데이터 구조에 종속되어야 한다.

---

## 9. VLM 통합의 안정성 — 3 층 방어

VLM 은 외부 의존이다. Replicate API 의 cold start 가 100~300 초, 자체 GPU 서버(Server_VB)는 상시 가동이 아니다. 시연 현장에서 VLM 응답이 지연되면 발표 흐름이 끊긴다.

이 문제를 3 층 방어로 흡수했다.

첫째, UI 에 0/30/90/180/240/300 초 6 단계 진행도 메시지를 표시하여 사용자가 대기 상태를 인지할 수 있게 했다. 둘째, VLM 응답이 504 또는 timeout 으로 떨어지면 fixture fallback UI 가 노출되어, "사전 분석 결과로 계속"과 "다시 시도" 선택지를 제공한다. sample_00015 도면(S45C 펌프)이 drawings 테이블에 사전 INSERT 되어 있어 즉시 매칭을 진행할 수 있다. fallback 사용 사실은 `general_notes.vlm_fallback_used=true` 로 영구 기록된다. 셋째, Server_VB hybrid 구조 자체가 Student/Teacher 양면 가동으로 한쪽이 내려가도 다른 쪽이 처리할 수 있다.

VLM 응답 수신 후에는 AI 분석 결과 카드(`#ai-result-card`)가 부품명, 재질, 치수, 후처리, 도면번호 5 항목을 추출하여 사용자 확인 + 인라인 수정 카드로 표시한다. 사용자가 수정한 내용은 `client_notes.ai_user_edits` 에 보존되며, material 이나 post_treatment 수정은 파이프라인에서 우선 적용된다. AI 가 추출한 결과를 그대로 쓰는 것이 아니라, 사용자가 검증하고 정정할 수 있는 구조다.

502 응답은 toast 후 사용자 선택을 제공하고, 504/timeout 은 fixture fallback UI 를 즉시 노출한다. 응답 schema 는 `vlm_output` 키 단일 source 이며, quote-request 화면은 `data.vlm_output || data.vlm_result_jsonb` 양면 fallback 으로 `hydrateAiResultCard` 를 호출한다. fallback 경로(sample_00015 직접 호출)에서는 VLM 응답이 부재하므로 AI 결과 카드가 노출되지 않고 정적 fallback 흐름으로 진행된다.

VLM 통합의 또 다른 측면은 도면 업로드와 매칭 호출의 분리이다. `POST /vlm/analyze-upload` 로 도면을 업로드하고, `POST /api/match-v2` 로 매칭을 실행한다. 두 단계가 분리되어 있으므로, VLM 이 실패해도 기존 drawing_id 로 매칭을 재시도할 수 있고, VLM 없이 직접 parts 를 전달하는 CLI/테스트 경로도 보존된다. 이 분리는 외부 의존 차단의 핵심 설계이다.

**학습**: 외부 의존(GPU, 외부 API)이 있을 때 사용자가 인지 가능한 fallback 은 필수다. 기술적 완벽성보다 사용자 경험의 연속성이 시연에서 더 중요하며, fallback 사실을 숨기지 않고 명시적으로 기록하는 것이 정직한 설계다.

---

## 10. frontend 의 두 길 — 패널 prepend vs 디자인 DOM 직접 hook

팀원이 작성한 정적 HTML/CSS 디자인 위에 실 API 결과를 연결하는 작업에서, 두 가지 접근을 시도했다.

처음에는 별도 패널을 기존 디자인 위에 prepend 하는 방식을 시도했다. `setPanelContent()` 함수가 `imma-phase1-panel` 이라는 별도 패널을 생성하여 API 결과를 표시하는 구조였다. 그러나 기존 디자인 위에 회색 패널이 덧씌워지는 부작용이 발생했다. 디자인 보존과 API hydrate 가 충돌하는 상황이었다.

이를 인식한 뒤, route 별 init 함수가 기존 디자인의 form/button/table/stat DOM 을 직접 hook 하여 실 API 결과를 hydrate 하는 방식으로 수렴했다. `imma-phase1-pages.js` 가 21 페이지 각각의 path 를 인식하고, 해당 페이지의 기존 DOM 요소를 직접 조회하여 API 응답 데이터를 채운다. 별도 패널 생성 로직은 제거되었다. 로그인 상태에서는 `renderSessionHeader` 가 기존 `.btn-login`/`.btn-signup` CTA 에 `display:none` 을 부여하여 디자인의 마케팅 요소와 실 세션 상태의 충돌을 방지한다.

`site-actions.js` 는 원래 단순한 toast 유틸이 아니라 실 페이지를 시뮬레이션으로 바꾸는 본체였다. `applyScenarioDemo()` 가 대부분의 페이지에 scenario 텍스트 치환과 header rewrite 를 적용하고, 전역 submit handler 가 모든 폼 submit 을 `preventDefault()` 하여 demo route 로 넘기는 구조였다. 이 코드가 남아 있으면 실 API 연결을 아무리 해도 demo 레이어가 가로채는 상황이 발생한다. 분리가 아니라 제거만이 답이었다.

동시에, 비활성 데모 JS(client.js, supplier.js, app.js, app_unified.js, shared-state.js, site-actions.js, site-actions-demo.js)를 디렉토리에서 물리적으로 제거했다. 비활성 데모 코드의 전역 submit/click intercept 가 실 API 흐름을 가로채는 위험을 코드 잔존만으로도 발생시키기 때문이다. 활성/비활성 자원의 물리적 분리로 컨텍스트 노이즈와 회귀 위험을 동시에 해소했다.

공용 JS 5 종(auth.js, imma-api.js, imma-ui-utils.js, imma-phase1-pages.js, admin-menu.js)이 전체 frontend 의 실 API 연결 본체가 되었다. auth.js 는 JWT decode, 세션 관리, role 기반 redirect 를, imma-api.js 는 Authorization 헤더 자동 주입 fetch wrapper 와 401 single-flight logout 을, imma-ui-utils.js 는 toast, KST 변환, 통화 포맷 등 공용 유틸을, imma-phase1-pages.js 는 21 페이지 route 별 init 함수(매칭 결과 hydrate, 견적 카드 hydrate, 폴링, 온보딩 카드 등)를, admin-menu.js 는 admin 3 페이지 사이드바와 Demo UI 배지를 각각 담당한다.

매칭 화면에서는 5 개 supplier 카드, 3 후보 비교 사이드바, AI 분석 요약, RFQ 요약, 점수 분해 tooltip, supplier 상세 modal 을 모두 실 API hydrate 로 전환했다. `classifyReason`/`cleanReason`/`renderReason` 헬퍼가 매칭 신호 토큰([INFO_CATEGORY_FALLBACK], [WARN_EQUIPMENT_CAPABILITY_MISSING] 등)을 info/warning/danger chip 으로 시각화한다.

21 개 HTML 의 공통 head 에는 `window.__imma_realmode__ = true;` 가 선언되어 있고, `imma-ui-utils.js` → `auth.js` → `imma-api.js` 순서로 공용 JS 가 로드된다. 보호 페이지 진입 시 `auth.js` 의 `verifySession()` 이 서버 검증을 수행하고, localStorage user 는 화면 깜빡임 방지용 1 차 캐시일 뿐이다. 이 로딩 순서와 검증 흐름이 21 개 페이지 전체의 일관성을 보장한다.

**학습**: 디자인 보존과 실 API hydrate 는 별도 신설이 아닌 기존 직접 주입으로 양립한다. 비활성 코드는 "안 쓰이니까 괜찮다"가 아니라, 잔존 자체가 위험이다. 그리고 frontend 작업의 본질은 "예쁘게 만드는 것"이 아니라 "데이터 흐름이 끊기지 않게 하는 것"이다.

---

## 11. 인증 + 보안의 3-tier 게이팅

B2B 마켓플레이스에서 동일한 endpoint 가 호출자의 정체에 따라 다른 응답을 내는 것은 자연스럽다.

`GET /api/company/{id}` 를 예로 들면, admin 은 모든 정보를 볼 수 있고, 본인 supplier 는 자기 회사 정보를 볼 수 있고, 관계가 형성된 buyer(매칭 accepted 또는 orders 존재)만 BRN, 연락처, 대표자명 같은 민감 정보에 접근할 수 있다. 익명에게는 공개 정보만, 인증 사용자에게는 기본 정보만 노출된다.

JWT(HS256) 기반 인증에서 production 환경의 기본값 fail-fast 를 도입했다. `JWT_SECRET=imma-dev-secret` 상태로 배포되는 것을 startup 단계에서 차단한다. 가동률/스케줄 조회도 본인 supplier 로 한정하여 경쟁사 정찰을 차단했고, `/companies/buyers` 는 admin-only 로 두어 buyer 실명/지역 평문 노출을 막았다.

세션 관리에서는 localStorage 의 user 를 1 차 캐시(화면 깜빡임 방지)로만 두고, `verifySession()` 이 `/api/me` 를 single-flight 로 호출하여 서버 검증을 최종 진실로 삼는다. 토큰 위변조, 만료, 서버 측 revoke 를 클라이언트 1 차 검사만으로 신뢰하지 않는 구조다. 가입 시 cross-table login_id 검증(buyers + companies UNION ALL)으로 한 사람이 buyer 와 supplier 를 동시 등록하는 것을 방지하고, `/api/check-login-id` endpoint 로 가입 전 중복 확인도 별도 제공한다.

`imma-api.js` 의 fetch wrapper 는 JWT 존재 시 Authorization 헤더를 자동 주입하고, 401 응답을 single-flight `imma.logout('unauthorized')` 로 처리하여 race condition 을 방지한다. 매 페이지가 개별적으로 fetch 에 헤더를 수동 부착하던 패턴을 한 곳으로 모은 결정이다.

CORS 는 화이트리스트 + `credentials=False` 로 설정했다. JWT 는 Authorization 헤더로 전달하므로 credentials 가 불필요하고, Railway same-origin 이라 외부 호출이 없다. `/api/config/health` 는 admin-only + boolean 만 반환하여, 민감 정보 값은 노출하지 않으면서 환경 설정 누락을 admin 이 확인할 수 있게 했다.

로그인 흐름에서 buyer 와 supplier 를 순차 조회하되, body 에 `expected_role` 을 동봉하여 cross-table 오분기를 차단한다. signup 에서 supplier 는 담당자명(name)과 회사명(company_name)을 분리 소비하며, 같은 트랜잭션 안에서 `companies` INSERT + `company_contacts` primary contact 추가 + `company_availability_snapshot` 초기화를 수행한다. 가입 응답에는 토큰이 없고, UI 는 즉시 `/api/login` 을 다시 호출한다. 가입과 인증을 분리하여, 가입 실패 시 반쯤 인증된 상태가 남지 않게 한다.

localStorage 키는 전역 인증 키 2 개(`imma_access_token`, `imma_user`)와 업무 상태 키(`imma:{user_id}:...` prefix)로 분리한다. logout 시 `clearUserScopedState(user.id)` 가 해당 prefix 키를 일괄 삭제하여, 동일 브라우저에서 사용자 전환 시 이전 사용자의 RFQ/order id 가 남지 않는다.

**학습**: 동일 endpoint 가 호출자 정체에 따라 다른 응답을 내는 것은 B2B 마켓플레이스의 자연스러운 패턴이다. 보안은 별도 모듈이 아니라, 매 endpoint 의 접근 판단에 녹아 있어야 한다. 그리고 보안의 대부분은 "뭘 추가하느냐"가 아니라 "뭘 노출하지 않느냐"의 문제다.

---

## 12. supplier 온보딩의 자동화

supplier 가입 후 실제 매칭에 노출되기까지의 온보딩 흐름에서, admin 수동 verify 단계를 제거하는 결정을 내렸다.

supplier 는 가입 시 `onboarding_status='draft'` 로 시작한다.

supplier-settings 화면에서 4 개 카드를 순차 충족한다.

첫째, 보유 장비 등록. CNC 선반, 머시닝센터, 연삭기 등을 장비 카테고리와 모델을 선택하여 등록한다. 둘째, 처리 가능 재질. 장비 등록 시 자동 추정된 재질이 선체크되어 표시되고, 사용자가 toggle 로 보강한다. 셋째, 추가 공정. 장비 자동 매핑 공정 외에 외주나 수작업 공정을 추가 등록할 수 있다. 넷째, 사업자 정보. BRN, region(17 종 시/도 select), city, address 등을 입력한다.

4 조건(장비 1 대 이상, 재질 1 종 이상, BRN, region)이 충족되면 `_check_onboarding` 이 자동으로 `verified` 로 전환하고, MV 를 즉시 refresh 한다. 4 조건 일부만 충족하면 `draft → submitted` 까지만 자동 전환되어 admin pending 목록에 노출된다.

장비 등록 시 `company_process_capabilities` 가 백엔드에서 자동으로 매핑된다. UI 는 `/api/equipment` 만 호출하며, 매핑된 공정은 lock 상태로 표시된다. 재질 칩에서는 장비 카테고리별 `EQUIPMENT_TO_MATERIAL_HINT` dict 기반 추정 재질이 자동 체크되어 노출되고, 사용자가 toggle 로 보강한다.

supplier-register 화면은 1 카드(회사명, 담당자명, 아이디, 비밀번호, 이메일, 전화)로 단순화했다. 기존에 가입 단계에서 받으려 했던 업체 정보(BRN, 주소, 회사 규모, 재질, 공정, 장비)는 모두 supplier-settings 의 4 카드로 이관하여, 가입의 진입 장벽을 낮추고 온보딩의 완결성을 높였다.

admin 수동 verify 를 없앤 이유는 단순하다. 검수의 본질은 입력 데이터의 정합성이다. 4 조건이 충족되었다는 것 자체가 시스템적 검증이고, admin 의 수동 클릭이 추가 가치를 주지 않는다면 자동화가 맞다. admin pending 목록에는 `submitted` 단계(일부 충족)만 노출되고, `verified` 업체는 이미 완료이므로 목록에서 제외된다.

온보딩 화면의 UX 세부 사항에서도 학습이 있었다. 재질 칩에서 `pendingMaterialCodes` Set 을 source of truth 로 유지하여, API refresh 도중에도 사용자 선택이 누락되지 않게 한다. 공정 카드에서 `service_mode` 드롭다운(in_house/outsourced/both)은 제거하고 backend 기본 `in_house` 로 처리했다. supplier 에게 불필요한 선택지를 줄이는 것이 온보딩 완료율을 높인다는 판단이다.

**학습**: 자동화의 판단 기준은 "자동화할 수 있는가"가 아니라 "수동 단계가 추가 가치를 주는가"이다. 데이터의 정합성을 시스템이 검증할 수 있다면, 사람의 클릭은 지연일 뿐이다. 그리고 온보딩의 핵심은 "정보를 많이 받는 것"이 아니라 "supplier 가 완료하는 것"이다.

---

## 13. 시연 안정성 — fixture + warm-up + dry-run + 격리 창

5/18 시연은 buyer, supplier, admin 3 개 역할이 같은 백엔드를 바라보는 것을 보여주는 자리였다. 기술 데모가 아니라, 발표자가 자연스러운 흐름을 끊지 않는 무대 운영이 핵심이다.

시연 흐름은 18 단계로 구성된다.

공급사 가입(1 카드 단순화) → 온보딩 4 카드(장비 등록 → 자동 공정 매핑 lock → 재질 chip 자동 추정 → 추가 공정 → 사업자 정보 → 자동 verified) → 발주자 가입 → 대시보드 진입 → 도면 업로드 + VLM 분석(6 단계 진행도) + AI 결과 카드(5 항목 + 인라인 수정) → match-v2 자동 실행 → 매칭 결과(5 후보 + 3 비교 + supplier 상세 modal) → 발주자 로그아웃 → 공급사 로그인 + 매칭 수신 → 공급사 수락 → 5 항목 회신(납기/금액/인증/후처리/메모) → 공급사 로그아웃 → 발주자 재로그인 → 견적 카드 확인 + 발주 확정 → 결제(데모) → 발주자 로그아웃 → 공급사 재로그인 → 발주 확인 수락 → 생산 진행 6 단계 → 납품 → 발주자 리뷰 → 공급사 받은 리뷰 확인.

buyer 와 supplier 2 화면 동시 진행으로 약 15 분, admin 은 종료 후 별도 1-2 분이다. admin 시연에서는 자동 verified 흐름으로 인해 시연 중 가입한 supplier 가 pending 에 남지 않으므로, 별도 dry-run supplier 를 사전 INSERT 하여 verify 시연을 진행한다.

시연 직전 준비로 DB `--reset`(DROP CASCADE 후 스키마/시드/목업/스케줄 재구성), VLM warm-up, ngrok 점검, 신규 회원 dry-run + 삭제를 수행했다. `setup_db.py --reset` 단일 명령으로 깔끔한 초기 상태를 보장한다. 발표 직전 5 가지 회귀 테스트(로그인 → 도면 업로드 → VLM fallback → 매칭 → supplier 수락+견적+발주 → admin verify)를 통과하는 것을 필수 조건으로 두었다.

시연 중에는 2 개 브라우저 창(시크릿 + 일반)으로 buyer 와 supplier 의 localStorage 를 격리했다. `imma:{user_id}:...` prefix 의 user-scoped key 로 동일 브라우저에서 사용자 전환 시 RFQ/order id 누수를 방지한다.

buyer 와 supplier 양쪽 모두 5 초 간격 폴링으로 상대방의 행동을 자동 감지한다. buyer 의 order-management 는 `/api/rfq/{rfq_id}/quotes` 와 `/api/notifications` 를 병렬 조회하여, `supplier_accepted` 알림 도착 시 녹색 수락 배지(`#imma-accept-badge`)를 표시하고 견적 도착 시 견적 카드(`.imma-quote-card-real`)를 hydrate 한다. 견적 카드는 납기, 금액, 품질 인증, 후처리/조립, 작업 메모 5 행 grid 로 표시된다. supplier 의 workbench 는 `order_confirmed` 알림 감지 시 발주 카드를 hydrate 하고, 체크박스 선택 시 `PUT status(ordered)` + `POST /api/jobs` 양면 호출이 자동으로 일어난다. 견적 1 건 이상 도착 시 폴링은 `clearInterval` 로 자동 중단되어 불필요한 서버 부하를 차단한다.

client-dashboard 에서는 재로그인 시에도 견적 대기/견적 도착 RFQ 가 진입 가능하도록 `#recent-orders` status 필터에 `open` 과 `quoted` 를 추가했다. payment-success 페이지는 `?order_id=` query 진입 시 `GET /api/orders/{id}` 로 PO 번호, 총 금액, 가공업체명을 실 데이터로 hydrate 한다.

수량 100 EA 정적 더미, UTC 시각 표시, quote_line_items.process_code FK 위반 등 시연 안정성을 해치는 잔여 이슈들을 일괄 정비했다. 수량은 실 API hydrate 또는 `'-'` fallback 으로, 시각은 KST 변환 helper 일괄 적용으로, process_code 는 frontend 첫 코드 추출 + backend 방어층(CSV/array 재정규화 + process_catalog 존재 검증)으로 각각 해소했다. supplier 견적 회신도 납기, 금액, 인증, 후처리, 메모 5 항목 구조화 회신으로 확장하여, buyer 가 견적 카드에서 의미 있는 비교를 할 수 있게 했다. `assumptions` JSON 구조화로 단일 text 컬럼에 3 항목(certification, post_treatment, memo)을 안전하게 보존하고, buyer 쪽 `parseQuoteAssumptions` 가 safe parse 로 추출한다.

vlm.py 의 `analyze_upload` 함수는 `def`(sync)로 정의했다. Replicate API 의 sync requests 호출과 time.sleep 폴링이 async event loop 를 freeze 시키는 위험을 회피하기 위해서다. FastAPI thread pool 에서 실행되어 다른 요청을 차단하지 않는다. 이처럼 "async 가 항상 옳다"는 전제를 의심하고, 외부 의존의 실제 호출 패턴에 맞춰 sync/async 를 선택하는 것도 학습이었다.

demo 페이지가 남아 있는 화면(결제, 메시징, supplier 검색, admin KPI)에서는 `.admin-demo-notice` 배지("Demo UI - 일부 데이터는 시연용 샘플입니다")를 명시하여 시연 흐름이 끊기지 않으면서도 실 API 와 데모의 경계를 사용자에게 알린다. 정적 demo 시각을 일괄 숨기는 방식은 시연 단절 부작용 우려로 폐기하고, demo 카드를 그대로 노출하되 배지로 명시하는 방식을 택했다.

matching 화면의 CTA 텍스트도 "발주 진행"에서 "이 후보로 견적 받기"로 변경했다. 시연 흐름에서 발주 자체는 견적 도착 후에 일어나므로, "발주 진행"이라는 표현은 견적 단계를 건너뛴 인상을 줄 수 있기 때문이다. 이처럼 UI 텍스트 하나까지도 시연 흐름의 의미 정합성을 고려했다.

**학습**: 시연은 기술 데모가 아니다. 발표자가 자연스러운 흐름을 끊지 않는 무대 운영이며, 그 안정성은 fixture, warm-up, dry-run, 격리 같은 준비의 합이다. "잘 돌아가겠지"가 아니라 "잘 안 돌아갈 때 어떻게 되는가"를 먼저 설계해야 한다.

---

## 14. 한계와 향후 방향성

IMMA 는 MVP 이다. 의식적으로 보류한 것들이 있다.

다부품 RFQ 에서 rfq_part_id 별 분리 매칭은 현재 단부품에서만 완전히 정합한다. 다부품에서는 cartesian 중복 가능성이 있고, supplier 응답 API 경로에 rfq_part_id 를 추가해야 한다.

company_material_process_capabilities(CMPC) 결합 테이블의 seed 350 행은 보유하고 있으나, hard filter SELECT 단계 결합은 MV 와 SQL 전면 재설계가 필요하여 향후로 두었다. 현재 MV 는 재질과 공정을 독립적으로 집계하므로, "이 업체가 이 재질에 대해 이 공정을 수행할 수 있는가"라는 결합 질의는 별도 JOIN 이 필요하다.

GDT 는 hard filter 미반영 상태이며, 리스크 태깅과 RFQ 질문 생성, 업체 랭킹 가중치 형태의 소프트 시그널 도입을 염두에 두고 있다. 실시간 알림은 현재 DB 5 초 폴링이며, WebSocket 도입은 운영 부하 단계의 과제다. supplier 입장 주문 목록 endpoint 도 현재는 `/api/notifications` 기반 발견으로 처리하고 있으며, 별도 endpoint 신설은 다음 단계다.

결제, 메시징, supplier 정밀 검색은 시연에서 데모 페이지로 유지했고, PG 연동과 WebSocket 메시징은 운영 단계에서 구현한다. admin 관제 KPI 실연결 또한 일부 실 API(pending verify/reject)와 일부 데모 카드의 혼합 상태이며, read-only 실 API 연결과 KPI 산출은 다음 단계다. admin 실연결 범위를 한정한 이유는 단순하다. 범위가 넓어지면 시연 리스크가 커지고, 나머지는 발표 슬라이드에서 데모로 명시하는 것이 더 안전하다.

매칭 신호의 DB 영구 기록(`match_candidates.explanation_jsonb` 에 [INFO_*]/[WARN_*] 신호 미저장), snapshot 과 스케줄 자동 동기화, 사용자 장비 등록 시 non_machining(열처리/용접/주조) capability 자동 생성 등도 인식하고 있는 과제다.

Server_VB Teacher 모델(30B FP8)의 첫 호출 cold start 도 운영 과제다. always-on 은 GPU 상시 점유 비용의 trade-off 이며, Student LoRA 가 대부분의 호출을 처리하는 hybrid 구조가 이 비용을 통제하는 현재의 답이다.

이 한계들은 프로젝트 완성도의 결함이 아니라, MVP 시점의 의식적 우선순위 결정의 결과다. 시연 가능한 핵심 흐름을 우선하고, 운영 단계 확장을 차후로 분리했다.

점수 체계도 의식적으로 3 단계(1.0/0.7/0.3)로 유지했다. 4 단계(0.05 차이)는 순위 민감도를 증가시키고, 미세한 점수 차이가 실질적 의미를 갖지 않는 현 데이터 규모에서 노이즈만 추가한다. max_seed/rfq 외부 일괄 추출도 매칭 1 회당 100 ms 미만 추가로, 현 부하에서 의미가 없어 보류했다.

자유 텍스트 특이사항(예: "급한 납기", "특수 후처리 요청")은 매칭 hard filter 에 반영하지 않고, `general_notes_jsonb` 에 저장하여 업체 견적 단계에 위임한다. 자유 텍스트를 SQL 조건으로 변환하는 것은 NLU 정확도 문제를 수반하며, 견적 단계에서 업체가 직접 판단하는 것이 더 정확하다.

**학습**: 한계를 숨기는 것보다 명시하는 것이 더 강하다. "안 한 것"에도 이유가 있고, 그 이유가 설계 판단의 일부라는 것을 인식하는 것이 중요하다. MVP 의 의미는 "미완성"이 아니라 "핵심 가치가 동작하는 최소 형태"이다.

---

## 15. 마무리 — 시도와 학습

---

IMMA 는 완성된 무엇이 아니다. 시도와 학습의 결과이다. 그리고 그 시도는 끝나지 않았다.

학계 표준 stack(YOLO + DONUT + VLM 조합) 대신 문제의 본질에 맞춘 단순화(VLM 단독 hybrid)를 선택했다.

벡터 검색 대신 인메모리 룩업을 선택했다.

컨소시엄 매칭 대신 단일 업체 직접 매칭을 선택했다.

온톨로지를 검증 도구가 아닌 변환 매개체로 재정의했다.

재질 정규화에서 불확실성을 숨기지 않고 confidence 로 명시 관리하기로 결정했다.

외주 공정을 hard filter 에서 제외하여 false negative 를 방지하기로 결정했다.

admin 수동 verify 를 데이터 정합성 자동 검증으로 대체하기로 결정했다.

각 선택에는 "왜"가 있었고, 그 "왜"가 이 문서의 본질이다.

AI 시대의 제조 매칭 자동화라는 비전의 첫 시도에서, 발주자와 공급사와 관리자 3 역할이 같은 백엔드를 바라보며 도면 업로드부터 발주, 생산, 납품, 리뷰까지를 시연할 수 있었다는 사실이 프로젝트의 의의다. 62 개 API endpoint, 21 개 HTML 페이지, 39 개 DB 테이블, 493 노드의 지식 그래프, 19 개 KS/ISO 룩업 테이블이 하나의 흐름으로 연결된다.

프로젝트를 진행하면서 가장 많이 한 일은 코드를 작성하는 것이 아니라 결정을 내리는 것이었다.

어떤 기술을 쓸 것인가, 어떤 기술을 내려놓을 것인가, 어디까지가 MVP 이고 어디부터가 다음 단계인가. 이 질문들이 반복되었고, 매번 문제의 본질로 돌아가서 답을 찾았다.

GPT-Pro(OpenAI GPT-5.5 Pro)의 독립 검증, 서브 에이전트의 분업 검증, 본인의 결정과 검수 — 이 3 층 구조로 판단의 품질을 관리했다. 혼자서 모든 것을 판단하지 않고, 검증을 분산시키되 최종 결정의 책임은 본인에게 두는 구조다. 에이전트에게 코드 작성을 위탁하되, 결정과 검수는 본인이 수행하여 컨텍스트를 보존했다.

되돌아보면, 다회 독립 검증을 통해 보강된 것들이 있다. 프롬프트/스키마의 enum 누락, 업체-재질-장비 구조의 공백, 인증과 소유권 검증의 약점, 비활성 데모 JS 의 전역 intercept 위험, async event loop freeze 위험, supplier 온보딩 흐름의 불일치 등. 이 발견들은 어느 하나도 "한 번에 잡히는" 종류가 아니었다. 반복 검증이 품질을 만든다는 것을 몸으로 체감했다.

프로젝트 전체를 관통하는 설계 원리가 있다면, 3 층 분담이다.

1 층 — VLM hybrid 가 도면을 읽어 구조화된 JSON 을 생성한다. AI 가 자율적으로 추론하는 층이다.

2 층 — GraphRAG 가 VLM raw JSON 을 Neo4j 지식 그래프를 매개로 IMMA 스키마로 변환한다. AI 가 도구를 자율적으로 조합하는 층이다.

3 층 — 매칭 hard filter 가 IMMA 스키마를 SQL 로 처리하여 후보 업체를 찾는다. 결정 가능한 것은 규칙으로 닫는 층이다.

AI 만능도 규칙 만능도 아닌, 각 층의 본질에 맞춘 분담. 이 원리는 처음부터 의식한 것이 아니라, 시도와 실패를 거치며 사후적으로 발견한 것이다.

이 프로젝트에서 가장 가치 있었던 학습은, 기술 자체가 아니라 기술 선택의 판단 과정이다.

문제를 정의하고, 접근을 시도하고, 실패에서 배우고, 다시 선택하는 순환. 그 순환의 매 단계에서 "왜"를 묻고 답하는 것이 엔지니어링의 본질이라는 것을 체감했다.

도면 인식에서 VLM 단독 수렴, 재질 정규화에서 4 층 confidence 관리, 온톨로지에서 검증 도구 → 변환 매개체 재정의, 벡터 검색 폐기와 인메모리 룩업 채택, 외주 공정의 fail-open 정책, parent fallback 의 화이트리스트 한정, supplier 온보딩의 자동 verified 전환, 비활성 데모 코드의 물리적 제거, async/sync 의 실제 호출 패턴 기반 선택 — 이 모든 결정에 각각의 "왜"가 있었다.

기술은 도구이고, 도구는 문제에 종속된다. 문제는 제조업에서 발주자와 가공업체가 서로를 찾지 못한다는 것이었다.

그 문제 앞에서 어떤 기술을 선택하고, 어떤 기술을 내려놓고, 왜 그렇게 했는지를 기억하기 위해 이 문서를 남긴다.

이 여정은 계속된다.

---

스마트제조혁신의 라스트 마일을 구현한다는 비전은 여전히 유효하다. 중국 저가 공세에 대응 가능한 고품질/다품종 소량 생산 시장, 하드웨어 스타트업과 개인 발명가의 시제품 조달 문제, 10 인 미만 소규모 가공업체의 디지털 전환 — 이 시장의 문제는 풀리지 않았고, IMMA 의 다음 단계가 그 답의 일부가 될 수 있다.

영세 소공인과 고령 운영자가 수천만 원 규모 시스템 도입 없이 가입만으로 AI 기능을 활용할 수 있는 세계. 도면 한 장에서 시작한 문제가 거기까지 갈 수 있는지, 이 문서를 다시 읽게 될 미래의 본인이 판단할 것이다.
