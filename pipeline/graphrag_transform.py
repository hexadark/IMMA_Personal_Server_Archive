"""IMMA GraphRAG 변환 레이어 — VLM raw JSON을 Neo4j 그래프 탐색 + LLM으로 우리 스키마로 변환."""

import json
import logging
import os
import re
import sys
import argparse

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 1. 환경 설정
# ─────────────────────────────────────────────

NEO4J_URI = os.environ.get("NEO4J_URI", "neo4j://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "imma_neo4j_2026!")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")

# ─────────────────────────────────────────────
# 2. Neo4j 연결
# ─────────────────────────────────────────────

_neo4j_driver = None


def _get_neo4j_driver():
    """Neo4j 드라이버를 lazy 초기화하여 반환한다. 연결 실패 시 None."""
    global _neo4j_driver
    if _neo4j_driver is not None:
        return _neo4j_driver
    try:
        from neo4j import GraphDatabase
        _neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        _neo4j_driver.verify_connectivity()
        logger.info("Neo4j 연결 성공: %s", NEO4J_URI)
        return _neo4j_driver
    except Exception as e:
        logger.warning("Neo4j 연결 실패 (%s) — 도구 없이 LLM만으로 변환 시도", e)
        return None


# ─────────────────────────────────────────────
# 3. 도구 정의 (LangGraph @tool)
# ─────────────────────────────────────────────

from langchain_core.tools import tool


@tool
def lookup_material(material_text: str) -> str:
    """재질명으로 Neo4j 그래프를 탐색하여 표준 코드와 카테고리를 반환한다.

    alias 매칭 → 직접 코드 매칭 → fuzzy 매칭 순서로 탐색한다.
    """
    driver = _get_neo4j_driver()
    if driver is None:
        return json.dumps({"error": "Neo4j 미연결", "input": material_text}, ensure_ascii=False)

    text = material_text.strip().upper()
    with driver.session() as session:
        # 1) alias 매칭 (MaterialAlias -[:ALIAS_OF]-> Material)
        result = session.run(
            """
            MATCH (a:MaterialAlias)-[:ALIAS_OF]->(m:Material)-[:BELONGS_TO]->(cat:MaterialCategory)
            WHERE toUpper(a.text) = $text
            RETURN m.code AS material_code, m.jis_code AS jis_code, m.name_ko AS name_ko,
                   cat.code AS category_code, cat.name_ko AS category_name_ko
            """,
            text=text,
        )
        records = [dict(r) for r in result]
        if records:
            return json.dumps(records, ensure_ascii=False)

        # 2) 직접 코드 매칭
        result = session.run(
            """
            MATCH (m:Material)-[:BELONGS_TO]->(cat:MaterialCategory)
            WHERE toUpper(m.code) = $code
            RETURN m.code AS material_code, m.jis_code AS jis_code, m.name_ko AS name_ko,
                   cat.code AS category_code, cat.name_ko AS category_name_ko
            """,
            code=text,
        )
        records = [dict(r) for r in result]
        if records:
            return json.dumps(records, ensure_ascii=False)

        # 3) fuzzy 매칭 (CONTAINS)
        result = session.run(
            """
            MATCH (m:Material)-[:BELONGS_TO]->(cat:MaterialCategory)
            WHERE toUpper(m.code) CONTAINS $text OR toUpper(m.jis_code) CONTAINS $text
            RETURN m.code AS material_code, m.jis_code AS jis_code, m.name_ko AS name_ko,
                   cat.code AS category_code, cat.name_ko AS category_name_ko
            LIMIT 5
            """,
            text=text,
        )
        records = [dict(r) for r in result]
        if records:
            return json.dumps(records, ensure_ascii=False)

    return json.dumps({"result": "not_found", "input": material_text}, ensure_ascii=False)


@tool
def lookup_compatibility(category_code: str) -> str:
    """재질 카테고리 코드로 호환 가능한 공정 목록을 조회한다."""
    driver = _get_neo4j_driver()
    if driver is None:
        return json.dumps({"error": "Neo4j 미연결", "input": category_code}, ensure_ascii=False)

    with driver.session() as session:
        result = session.run(
            """
            MATCH (cat:MaterialCategory {code: $code})-[r:COMPATIBLE_WITH]->(p:Process)
            RETURN p.code AS process, r.compatibility AS compatibility,
                   r.machinability AS machinability, r.notes AS notes
            ORDER BY r.compatibility, p.code
            """,
            code=category_code.strip().lower(),
        )
        records = [dict(r) for r in result]

    if records:
        return json.dumps(records, ensure_ascii=False)
    return json.dumps({"result": "no_compatibility_data", "input": category_code}, ensure_ascii=False)


@tool
def lookup_sequence() -> str:
    """모든 공정 순서 규칙(MUST_PRECEDE, RECOMMENDED_BEFORE)을 조회한다."""
    driver = _get_neo4j_driver()
    if driver is None:
        return json.dumps({"error": "Neo4j 미연결"}, ensure_ascii=False)

    rules = []
    with driver.session() as session:
        result = session.run(
            """
            MATCH (a)-[r:MUST_PRECEDE]->(b)
            RETURN a.code AS predecessor, b.code AS successor,
                   'absolute' AS rule_type, r.rationale AS rationale
            """
        )
        rules.extend([dict(r) for r in result])

        result = session.run(
            """
            MATCH (a)-[r:RECOMMENDED_BEFORE]->(b)
            RETURN a.code AS predecessor, b.code AS successor,
                   'recommended' AS rule_type, r.rationale AS rationale
            """
        )
        rules.extend([dict(r) for r in result])

    if rules:
        return json.dumps(rules, ensure_ascii=False)
    return json.dumps({"result": "no_sequence_rules"}, ensure_ascii=False)


@tool
def lookup_tolerance(process_code: str) -> str:
    """공정 코드로 달성 가능한 IT/Ra 범위를 조회한다."""
    driver = _get_neo4j_driver()
    if driver is None:
        return json.dumps({"error": "Neo4j 미연결", "input": process_code}, ensure_ascii=False)

    with driver.session() as session:
        result = session.run(
            """
            MATCH (p:Process {code: $code})
            RETURN p.code AS process_code, p.name_ko AS name_ko,
                   p.typical_it_min AS typical_it_min, p.typical_it_max AS typical_it_max,
                   p.precision_it_min AS precision_it_min, p.precision_it_max AS precision_it_max,
                   p.typical_ra_min AS typical_ra_min, p.typical_ra_max AS typical_ra_max,
                   p.precision_ra_min AS precision_ra_min, p.precision_ra_max AS precision_ra_max
            """,
            code=process_code.strip().lower(),
        )
        records = [dict(r) for r in result]

    if records:
        return json.dumps(records, ensure_ascii=False)
    return json.dumps({"result": "not_found", "input": process_code}, ensure_ascii=False)


# ─────────────────────────────────────────────
# 4. LangGraph 에이전트 생성
# ─────────────────────────────────────────────

TOOLS = [lookup_material, lookup_compatibility, lookup_sequence, lookup_tolerance]

OUTPUT_SCHEMA = """{
  "drawing_no": "도면번호 또는 null",
  "referenced_standards": ["KS B ISO 2768-m", "KS D 3752", ...],
  "parts": [{
    "part_name": "부품명",
    "material": {"raw_text": "도면 원문 그대로", "category": "카테고리 한글명(아래 목록 중 하나)"},
    "quantity": 1,
    "required_processes": ["공정코드(아래 목록 중 선택)"],
    "max_envelope_mm": {"length": null, "width": null, "height": null},
    "dimensions": [{"feature": "설명", "value": 숫자, "unit": "mm", "type": "outer_diameter|hole_diameter|length|width|height"}],
    "tolerances": [{"feature": "대상", "text": "도면 원문 그대로(예: Ø65js5, 80h7, 120±0.05)", "type": "끼워맞춤|치수공차|일반공차"}],
    "gdt": [{"type": "타입", "symbol": "기호", "value": 숫자, "unit": "mm", "datum": "기준면", "feature": "적용부위"}],
    "surface_roughness": [{"feature": "대상", "Ra": 숫자, "unit": "μm"}],
    "post_treatment": "한글 자유 텍스트 또는 null",
    "unsupported": false,
    "unsupported_reason": null
  }]
}"""

SYSTEM_PROMPT = """너는 제조업 도면 분석 전문가다.
VLM이 도면에서 추출한 raw JSON을 분석하고, 제공된 도구로 Neo4j 제조 지식 그래프를 탐색하여 구조화된 JSON을 생성해라.

[출력 스키마]
{output_schema}

[허용 공정 코드 — required_processes에는 반드시 아래 값만 사용]
bending, boring, broaching, casting, centerless_grinding, cylindrical_grinding,
drilling, edm_sinker, edm_wire, gear_grinding, grinding, heat_treatment, hobbing,
honing, internal_grinding, keyway, lapping, laser_cutting, milling, milling_finish,
milling_rough, plasma_cutting, polishing, press_forming, reaming, sheet_metal,
surface_grinding, surface_treatment, threading, turning, turning_finish,
turning_rough, waterjet_cutting, welding

[허용 재질 카테고리 — material.category에는 반드시 아래 한글명만 사용]
탄소강, 합금강, 스테인리스강, 회주철, 구상흑연주철, 주강, 스테인리스 주강, 알루미늄 합금, 구리합금, 판금용 강판, 공구강, 엔지니어링 플라스틱, 쾌삭강, 복합재/절연재

[규칙]
1. 재질이 있으면 lookup_material로 조회하여 카테고리를 확인한다.
2. 카테고리가 확인되면 lookup_compatibility로 호환 공정 후보를 확인한다. 단, required_processes에는 도면의 형상·치수·공차·조도·후처리에서 실제로 필요한 최소 필수 공정만 넣는다. 호환 가능한 전체 공정을 넣지 않는다.
3. 공정이 결정되면 lookup_sequence로 순서 규칙을 확인하여 올바른 순서로 배열한다.
4. 필요시 lookup_tolerance로 공정별 달성 가능 IT/Ra를 확인한다.
5. 수치는 VLM raw에서 추출된 것만 사용한다. 숫자를 생성하지 않는다.
6. dimensions 배열: 외경 또는 최대 소재 직경은 type "outer_diameter"로 넣는다. 이 값이 선삭 envelope 매칭에 사용된다. 구멍, 내경, 나사, PCD, 카운터보어 지름은 type "hole_diameter"로 넣는다.
7. max_envelope_mm: 부품의 최대 외접 직육면체 치수(length×width×height). 지름은 여기 넣지 않는다.
8. tolerances[].text: 도면 원문을 그대로 유지한다(예: "Ø65js5", "80h7", "120±0.05", "80+0.03/-0.01"). 반드시 nominal 치수를 포함해야 한다. IT등급과 ±공차 모두 여기서 자동 추출된다.
9. post_treatment: 한글로 작성한다(예: "열처리 HRC 50±2", "크롬도금", "아노다이징", "질화처리"). 영문 금지.
9-1. 후처리가 있으면 post_treatment에 한글 원문을 적고, 동시에 required_processes에도 대응 공정 코드를 반드시 포함한다:
  - 열처리/HRC/경도/QT/침탄/담금질/뜨임/소입/소둔/질화 → heat_treatment
  - 도금/아노다이징/크롬/산화피막/흑착색/코팅/도장 → surface_treatment
  - 용접 → welding
10. referenced_standards: Notes에서 참조 표준을 추출한다(예: "ISO 2768-mK", "KS D 3752").
10-1. surface_roughness의 Ra 값: ▽ 기호는 다음과 같이 Ra 수치로 변환한다:
  - ▽ = Ra 25, ▽▽ = Ra 6.3, ▽▽▽ = Ra 1.6, ▽▽▽▽ = Ra 0.4 (단위 μm)
  도면에 ▽ 기호만 있고 Ra 수치가 없으면 위 변환값을 사용한다.
11. 공정 순서는 반드시 그래프의 MUST_PRECEDE 규칙을 따른다.
12. JSON만 출력한다. 설명/마크다운 없이.
13. GDT(기하공차)는 gdt 배열에 보존하되, required_processes에 직접 추가하지 않는다. GDT는 업체 탈락 조건이 아니라 정밀도 리스크/검사 요구 신호로만 사용된다.
14. 엔지니어링 플라스틱과 복합재/절연재에는 edm_sinker, edm_wire를 required_processes에 넣지 않는다. 비전도성 소재는 EDM 가공이 불가하다.
15. 플라스틱의 응력제거/어닐링은 금속 heat_treatment로 매핑하지 않는다. post_treatment에 한글로 기재만 한다.
16. 쾌삭강(SUM24L, SUM22 등)은 "쾌삭강"으로 분류한다. "탄소강"으로 분류하지 않는다.

[재질 분류 힌트]
- POM, MC Nylon, PA6, PA66, PTFE, PEEK, PC, PMMA, ABS → 엔지니어링 플라스틱
- SUM24L, SUM22, SUM23, SUM43, 12L14 → 쾌삭강
- FCD, GCD, ductile iron, nodular cast iron → 구상흑연주철
- FR-4, FR4, G10, G11, 베크라이트, CFRP, GFRP → 복합재/절연재
- SCS13, SCS14, SCS16, SSC13, SSC14, SSC16, CF8, CF8M, CF3M → 스테인리스 주강

[특수 케이스 — unsupported 처리]
재질이나 공정이 위 허용 목록에 해당하지 않아 매칭이 불가능한 부품은 unsupported를 true로 설정하고 이유를 명시한다.
나머지 필드는 추출 가능한 만큼 채우되, required_processes는 빈 배열로 둔다.
이 판정은 반드시 lookup_material 또는 lookup_compatibility 조회 결과를 근거로 해야 하며, 도구 조회 없이 자의적으로 판정하지 않는다.

예시:
{{
  "part_name": "하우징",
  "material": {{"raw_text": "Ti-6Al-4V", "category": null}},
  "quantity": 1,
  "required_processes": [],
  "unsupported": true,
  "unsupported_reason": "티타늄 합금(Ti-6Al-4V)은 현재 지원 재질 카테고리에 해당하지 않음"
}}"""


def _create_agent():
    """LangGraph ReAct 에이전트를 생성하여 반환한다."""
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langgraph.prebuilt import create_react_agent

    llm = ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        google_api_key=GEMINI_API_KEY,
        temperature=1.0,
        thinking_level="low",
        timeout=120,
        max_retries=2,
    )

    system_message = SYSTEM_PROMPT.format(output_schema=OUTPUT_SCHEMA)
    agent = create_react_agent(llm, TOOLS, prompt=system_message)
    return agent


# ─────────────────────────────────────────────
# 5. 메인 변환 함수
# ─────────────────────────────────────────────

def _extract_json_from_text(text: str) -> dict:
    """LLM 응답 텍스트에서 JSON을 추출한다."""
    # ```json ... ``` 블록 추출 시도
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    # 전체 텍스트가 JSON인 경우
    text_stripped = text.strip()
    if text_stripped.startswith("{"):
        return json.loads(text_stripped)
    # [ 로 시작하는 경우
    if text_stripped.startswith("["):
        return {"parts": json.loads(text_stripped)}
    raise ValueError(f"LLM 응답에서 JSON을 추출할 수 없음: {text[:200]}...")


def transform_vlm_raw(vlm_raw: dict, retry: bool = True) -> dict:
    """VLM raw JSON을 받아서 우리 스키마 JSON을 반환한다.

    Args:
        vlm_raw: VLM이 추출한 raw JSON
        retry: JSON 파싱 실패 시 1회 재시도 여부

    Returns:
        우리 스키마에 맞는 dict

    Raises:
        ValueError: 변환 결과가 유효하지 않은 경우
    """
    agent = _create_agent()

    user_message = f"""아래 VLM raw JSON을 분석하고, 도구를 사용하여 구조화된 JSON으로 변환해라.

[VLM Raw JSON]
{json.dumps(vlm_raw, ensure_ascii=False, indent=2)[:50000]}"""

    try:
        result = agent.invoke({"messages": [{"role": "user", "content": user_message}]})
    except Exception as e:
        logger.error("에이전트 실행 실패: %s", e)
        raise

    # 마지막 AI 메시지에서 JSON 추출
    messages = result.get("messages", [])
    if not messages:
        raise ValueError("에이전트 응답이 비어 있음")
    last_message = messages[-1]
    content = last_message.content if hasattr(last_message, "content") else str(last_message)
    if isinstance(content, list):
        response_text = "\n".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    else:
        response_text = str(content)

    try:
        schema_json = _extract_json_from_text(response_text)
    except (json.JSONDecodeError, ValueError) as e:
        if retry:
            logger.warning("JSON 파싱 실패, 1회 재시도: %s", e)
            return transform_vlm_raw(vlm_raw, retry=False)
        raise ValueError(f"LLM 응답이 유효한 JSON이 아님: {e}")

    # 검증: "parts" 키 존재 필수
    if "parts" not in schema_json:
        raise ValueError(f"변환 결과에 'parts' 키가 없음: {list(schema_json.keys())}")

    return schema_json


# ─────────────────────────────────────────────
# 6. 파이프라인 연결 함수
# ─────────────────────────────────────────────

def run_graphrag_pipeline(vlm_raw: dict) -> dict:
    """GraphRAG 변환 후 기존 파이프라인(parse→resolve→match→response) 실행까지 체이닝한다.

    Args:
        vlm_raw: VLM이 추출한 raw JSON

    Returns:
        최종 매칭 결과 dict
    """
    # 1. GraphRAG 변환
    logger.info("GraphRAG 변환 시작")
    schema_json = transform_vlm_raw(vlm_raw)
    logger.info("GraphRAG 변환 완료: parts=%d", len(schema_json.get("parts", [])))

    # 2. 기존 파이프라인 실행
    from pipeline_runner import run_pipeline_from_dict
    return run_pipeline_from_dict(schema_json)


# ─────────────────────────────────────────────
# 7. CLI
# ─────────────────────────────────────────────

def main():
    """CLI 진입점. VLM raw JSON 파일 경로를 받아서 실행한다."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="IMMA GraphRAG 변환 레이어 — VLM raw JSON → 스키마 JSON 변환"
    )
    parser.add_argument("vlm_raw_path", help="VLM raw JSON 파일 경로")
    parser.add_argument(
        "--transform-only", "-t",
        action="store_true",
        help="변환만 수행 (기존 파이프라인 실행 없이 스키마 JSON만 출력)",
    )
    args = parser.parse_args()

    from pathlib import Path
    raw_path = Path(args.vlm_raw_path)
    if not raw_path.exists():
        logger.error("파일이 존재하지 않음: %s", args.vlm_raw_path)
        sys.exit(1)

    vlm_raw = json.loads(raw_path.read_text(encoding="utf-8"))

    if args.transform_only:
        result = transform_vlm_raw(vlm_raw)
    else:
        result = run_graphrag_pipeline(vlm_raw)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
