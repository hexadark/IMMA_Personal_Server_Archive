"""IMMA Neo4j 그래프 시드 스크립트.

PostgreSQL + lookup JSON + equipment_catalog.json에서 전체 제조 지식을
Neo4j 그래프에 시드한다.

실행:
    cd /home/tae-hun-kim/바탕화면/fas_analysis/pipeline
    python seed_neo4j.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from neo4j import GraphDatabase

import db
import lookup
from lookup import PROC_NORMALIZE, STAGE_TO_CODES, parse_it
from config import EQUIPMENT_CATALOG_PATH

# ─────────────────────────────────────────────
# 로깅
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("seed_neo4j")

# ─────────────────────────────────────────────
# Neo4j 연결 정보
# ─────────────────────────────────────────────
NEO4J_URI = os.environ.get("NEO4J_URI", "neo4j://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "imma_neo4j_2026!")



def _norm_proc(code: str) -> str:
    """공정 코드를 정규화한다."""
    return PROC_NORMALIZE.get(code, code)


# ═════════════════════════════════════════════
# 시드 함수들
# ═════════════════════════════════════════════

def _clear_graph(session) -> None:
    """기존 그래프 전체 삭제."""
    log.info("기존 그래프 삭제 (DETACH DELETE ALL)...")
    session.run("MATCH (n) DETACH DELETE n")
    log.info("  완료.")


# ─────────────────────────── 2. 공정 시드 ───
def _seed_processes(session) -> int:
    """process_catalog → :Process 노드 + :CHILD_OF 관계."""
    log.info("공정 시드 (process_catalog)...")
    rows = db.execute_query(
        "SELECT process_code, parent_process_code, process_name_ko, "
        "process_group FROM imma.process_catalog"
    )
    # 노드 생성
    for r in rows:
        session.run(
            "MERGE (p:Process {code: $code}) "
            "SET p.name_ko = $name_ko, p.group = $group",
            code=r["process_code"],
            name_ko=r["process_name_ko"],
            group=r["process_group"],
        )
    # 관계 생성
    for r in rows:
        if r["parent_process_code"]:
            session.run(
                "MATCH (child:Process {code: $child_code}) "
                "MATCH (parent:Process {code: $parent_code}) "
                "MERGE (child)-[:CHILD_OF]->(parent)",
                child_code=r["process_code"],
                parent_code=r["parent_process_code"],
            )
    log.info("  %d개 공정 시드 완료.", len(rows))
    return len(rows)


# ─────────────────────── 3. 재질 카테고리 ───
def _seed_material_categories(session) -> int:
    """material_category_catalog → :MaterialCategory 노드."""
    log.info("재질 카테고리 시드...")
    rows = db.execute_query(
        "SELECT category_code, category_name_ko "
        "FROM imma.material_category_catalog"
    )
    for r in rows:
        session.run(
            "MERGE (mc:MaterialCategory {code: $code}) "
            "SET mc.name_ko = $name_ko",
            code=r["category_code"],
            name_ko=r["category_name_ko"],
        )
    log.info("  %d개 재질 카테고리 시드 완료.", len(rows))
    return len(rows)


# ────────────────── 4. 재질 코드 + alias ────
def _seed_materials(session) -> int:
    """materials → :Material 노드, BELONGS_TO 관계, aliases."""
    log.info("재질 코드 시드 (materials + aliases)...")

    # 4a. Material 노드
    mat_rows = db.execute_query(
        "SELECT material_code, material_name_ko, category_code, "
        "jis_code, aisi_sae_code "
        "FROM imma.materials"
    )
    for r in mat_rows:
        session.run(
            "MERGE (m:Material {code: $code}) "
            "SET m.name_ko = $name_ko, m.jis_code = $jis, m.aisi_sae_code = $aisi",
            code=r["material_code"],
            name_ko=r["material_name_ko"],
            jis=r["jis_code"],
            aisi=r["aisi_sae_code"],
        )
        # BELONGS_TO 관계
        session.run(
            "MATCH (m:Material {code: $code}) "
            "MATCH (mc:MaterialCategory {code: $cat}) "
            "MERGE (m)-[:BELONGS_TO]->(mc)",
            code=r["material_code"],
            cat=r["category_code"],
        )
    log.info("  %d개 재질 노드 생성.", len(mat_rows))

    # 4b. MaterialAlias 노드 (material_aliases 테이블)
    alias_rows = db.execute_query(
        "SELECT ma.alias_text, m.material_code "
        "FROM imma.material_aliases ma "
        "JOIN imma.materials m ON ma.material_id = m.material_id"
    )
    for r in alias_rows:
        session.run(
            "MERGE (a:MaterialAlias {text: $text}) "
            "WITH a "
            "MATCH (m:Material {code: $code}) "
            "MERGE (a)-[:ALIAS_OF]->(m)",
            text=r["alias_text"],
            code=r["material_code"],
        )
    log.info("  %d개 alias (material_aliases 테이블) 등록.", len(alias_rows))

    # 4c. jis_code / aisi_sae_code 를 추가 alias 로 등록
    extra_alias_count = 0
    for r in mat_rows:
        for field in ("jis_code", "aisi_sae_code"):
            val = r.get(field)
            if val:
                session.run(
                    "MERGE (a:MaterialAlias {text: $text}) "
                    "WITH a "
                    "MATCH (m:Material {code: $code}) "
                    "MERGE (a)-[:ALIAS_OF]->(m)",
                    text=val,
                    code=r["material_code"],
                )
                extra_alias_count += 1
    log.info("  %d개 추가 alias (jis/aisi) 등록.", extra_alias_count)

    return len(mat_rows)


# ────────────── 5. 재질-공정 호환성 ──────────
def _seed_compatibility(session) -> int:
    """MATERIAL_PROCESS_COMPATIBILITY → :COMPATIBLE_WITH 관계."""
    log.info("재질-공정 호환성 시드...")
    rows = lookup.get_table("MATERIAL_PROCESS_COMPATIBILITY")

    count = 0
    for r in rows:
        proc_code = _norm_proc(r["process"])
        mat_group = r["material_group"]

        # :Process 노드가 없으면 MERGE 로 생성 (rough/finish 등)
        session.run(
            "MERGE (p:Process {code: $code})",
            code=proc_code,
        )

        session.run(
            "MATCH (mc:MaterialCategory {code: $mat_group}) "
            "MATCH (p:Process {code: $proc}) "
            "MERGE (mc)-[r:COMPATIBLE_WITH]->(p) "
            "SET r.compatibility = $compat, "
            "    r.machinability = $mach, "
            "    r.notes = $notes",
            mat_group=mat_group,
            proc=proc_code,
            compat=r.get("compatibility"),
            mach=r.get("machinability"),
            notes=r.get("notes"),
        )
        count += 1

    log.info("  %d개 호환성 관계 시드 완료.", count)
    return count


# ──────────── 6. 순서 규칙 시드 ─────────────
def _seed_sequence_constraints(session) -> int:
    """PROCESS_SEQUENCE_CONSTRAINTS → 관계 + 추상 단계 노드."""
    log.info("순서 규칙 시드...")
    rules = lookup.get_table("PROCESS_SEQUENCE_CONSTRAINTS")

    # 6a. 추상 단계 노드 + :INCLUDES 관계
    abstract_stages_created: set[str] = set()
    for stage_name, codes in STAGE_TO_CODES.items():
        session.run(
            "MERGE (p:Process {code: $code}) "
            "SET p.process_group = 'abstract_stage'",
            code=stage_name,
        )
        abstract_stages_created.add(stage_name)
        for concrete in codes:
            session.run(
                "MATCH (abstract:Process {code: $abstract}) "
                "MATCH (concrete:Process {code: $concrete}) "
                "MERGE (abstract)-[:INCLUDES]->(concrete)",
                abstract=stage_name,
                concrete=concrete,
            )
    log.info("  %d개 추상 단계 노드 + INCLUDES 관계 생성.", len(abstract_stages_created))

    # 6b. 규칙별 관계 생성
    rel_count = 0
    for rule in rules:
        rule_type = rule["rule_type"]
        rule_id = rule["rule_id"]
        rationale = rule.get("rationale", "")
        confidence = rule.get("confidence", "")

        if rule_type == "absolute_rule":
            pred = rule["predecessor_process"]
            succ = rule["successor_process"]
            # 추상 단계명도 :Process 노드로 MERGE (이미 위에서 했을 수 있음)
            session.run("MERGE (:Process {code: $c})", c=pred)
            session.run("MERGE (:Process {code: $c})", c=succ)
            session.run(
                "MATCH (a:Process {code: $pred}) "
                "MATCH (b:Process {code: $succ}) "
                "MERGE (a)-[r:MUST_PRECEDE]->(b) "
                "SET r.rule_id = $rid, r.rationale = $rat, r.confidence = $conf",
                pred=pred, succ=succ,
                rid=rule_id, rat=rationale, conf=confidence,
            )
            rel_count += 1

        elif rule_type == "recommended":
            # sequence 배열이 있는 경우
            seq = rule.get("sequence")
            if seq and len(seq) >= 2:
                for i in range(len(seq) - 1):
                    a, b = seq[i], seq[i + 1]
                    session.run("MERGE (:Process {code: $c})", c=a)
                    session.run("MERGE (:Process {code: $c})", c=b)
                    session.run(
                        "MATCH (a:Process {code: $a}) "
                        "MATCH (b:Process {code: $b}) "
                        "MERGE (a)-[r:RECOMMENDED_BEFORE]->(b) "
                        "SET r.rule_id = $rid, r.rationale = $rat",
                        a=a, b=b, rid=rule_id, rat=rationale,
                    )
                    rel_count += 1
            else:
                # predecessor/successor 쌍
                pred = rule.get("predecessor_process")
                succ = rule.get("successor_process")
                if pred and succ:
                    session.run("MERGE (:Process {code: $c})", c=pred)
                    session.run("MERGE (:Process {code: $c})", c=succ)
                    session.run(
                        "MATCH (a:Process {code: $a}) "
                        "MATCH (b:Process {code: $b}) "
                        "MERGE (a)-[r:RECOMMENDED_BEFORE]->(b) "
                        "SET r.rule_id = $rid, r.rationale = $rat",
                        a=pred, b=succ, rid=rule_id, rat=rationale,
                    )
                    rel_count += 1

        elif rule_type == "cannot_run_concurrently":
            pa = rule.get("process_a", "")
            pb = rule.get("process_b", "")
            session.run("MERGE (:Process {code: $c})", c=pa)
            session.run("MERGE (:Process {code: $c})", c=pb)
            session.run(
                "MATCH (a:Process {code: $a}) "
                "MATCH (b:Process {code: $b}) "
                "MERGE (a)-[r:CANNOT_RUN_CONCURRENTLY]->(b) "
                "SET r.rule_id = $rid, r.rationale = $rat, r.active = false",
                a=pa, b=pb, rid=rule_id, rat=rationale,
            )
            rel_count += 1

    log.info("  %d개 순서 관계 시드 완료.", rel_count)
    return rel_count


# ──────────── 7. 공차 달성 시드 ─────────────
def _seed_tolerances(session) -> int:
    """PROCESS_ACHIEVABLE_TOLERANCE → :Process 속성으로 저장."""
    log.info("공차 달성 데이터 시드...")
    rows = lookup.get_table("PROCESS_ACHIEVABLE_TOLERANCE")

    count = 0
    for r in rows:
        proc_code = _norm_proc(r["process"])
        ait = r.get("achievable_IT", {})
        ara = r.get("achievable_Ra_um", {})

        typical_it = ait.get("typical", {})
        precision_it = ait.get("precision", {})
        typical_ra = ara.get("typical", {})
        precision_ra = ara.get("precision", {})

        session.run(
            "MERGE (p:Process {code: $code}) "
            "SET p.typical_it_min  = $t_it_min, "
            "    p.typical_it_max  = $t_it_max, "
            "    p.precision_it_min = $p_it_min, "
            "    p.precision_it_max = $p_it_max, "
            "    p.typical_ra_min  = $t_ra_min, "
            "    p.typical_ra_max  = $t_ra_max, "
            "    p.precision_ra_min = $p_ra_min, "
            "    p.precision_ra_max = $p_ra_max",
            code=proc_code,
            t_it_min=parse_it(typical_it.get("min")),
            t_it_max=parse_it(typical_it.get("max")),
            p_it_min=parse_it(precision_it.get("min")),
            p_it_max=parse_it(precision_it.get("max")),
            t_ra_min=typical_ra.get("min"),
            t_ra_max=typical_ra.get("max"),
            p_ra_min=precision_ra.get("min"),
            p_ra_max=precision_ra.get("max"),
        )
        count += 1

    log.info("  %d개 공정 공차 속성 시드 완료.", count)
    return count


# ────────────── 8. 장비 모델 시드 ───────────
def _seed_equipment_models(session) -> int:
    """equipment_catalog.json → :EquipmentModel 노드 + :CAPABLE_OF 관계."""
    log.info("장비 모델 시드 (equipment_catalog.json)...")
    raw = json.loads(Path(EQUIPMENT_CATALOG_PATH).read_text(encoding="utf-8"))
    templates = raw["templates"]

    model_count = 0
    cap_count = 0
    for t in templates:
        common = t["common"]
        model_id = common["model_id"]["value"]
        manufacturer = common["manufacturer"]["value"]
        model_name = common["model_name"]["value"]
        cat_code = common["equipment_category_code"]["value"]

        session.run(
            "MERGE (em:EquipmentModel {model_id: $mid}) "
            "SET em.manufacturer = $mfr, "
            "    em.model_name = $mname, "
            "    em.category_code = $cat",
            mid=model_id, mfr=manufacturer, mname=model_name, cat=cat_code,
        )
        model_count += 1

        # process_capabilities
        for pc in t.get("process_capabilities", []):
            proc_code_val = pc.get("process_code", {}).get("value")
            if not proc_code_val:
                continue
            proc_code = _norm_proc(proc_code_val)
            typical_it = pc.get("typical_it_grade", {}).get("value")
            typical_ra = pc.get("typical_ra_um", {}).get("value")

            # :Process 노드 확보
            session.run("MERGE (:Process {code: $c})", c=proc_code)
            session.run(
                "MATCH (em:EquipmentModel {model_id: $mid}) "
                "MATCH (p:Process {code: $proc}) "
                "MERGE (em)-[r:CAPABLE_OF]->(p) "
                "SET r.typical_it = $it, r.typical_ra = $ra",
                mid=model_id, proc=proc_code,
                it=typical_it, ra=typical_ra,
            )
            cap_count += 1

    log.info("  %d개 장비 모델, %d개 CAPABLE_OF 관계 시드 완료.", model_count, cap_count)
    return model_count


# ──────── 9. 재질 물성 + ALTERNATIVE_TO 시드 ──
def _seed_material_properties(session) -> int:
    """MATERIAL_PROPERTIES → 기존 :Material 노드에 물성 속성 SET + :ALTERNATIVE_TO 관계."""
    log.info("재질 물성 시드 (MATERIAL_PROPERTIES)...")
    rows = lookup.get_table("MATERIAL_PROPERTIES")

    prop_count = 0
    alt_count = 0
    for r in rows:
        code = r["code"]
        ts = r.get("tensile_strength_mpa") or {}
        ys = r.get("yield_strength_mpa") or {}
        hr = r.get("hardness_range") or {}

        # yield_strength 의 min/max 가 null 일 수 있음 (회주철 등)
        ys_min = ys.get("min")
        ys_max = ys.get("max")

        session.run(
            "MATCH (m:Material {code: $code}) "
            "SET m.tensile_strength_min = $ts_min, "
            "    m.tensile_strength_max = $ts_max, "
            "    m.yield_strength_min   = $ys_min, "
            "    m.yield_strength_max   = $ys_max, "
            "    m.hardness_annealed    = $h_ann, "
            "    m.hardness_heat_treated = $h_ht, "
            "    m.corrosion_class      = $corr, "
            "    m.heat_resistance_max_c = $heat_c, "
            "    m.weldability          = $weld, "
            "    m.usage_tags           = $tags, "
            "    m.notes                = $notes",
            code=code,
            ts_min=ts.get("min"),
            ts_max=ts.get("max"),
            ys_min=ys_min,
            ys_max=ys_max,
            h_ann=hr.get("annealed_or_as_supplied"),
            h_ht=hr.get("heat_treated"),
            corr=r.get("corrosion_class"),
            heat_c=r.get("heat_resistance_max_c"),
            weld=r.get("weldability"),
            tags=r.get("usage_tags", []),
            notes=r.get("notes", ""),
        )
        prop_count += 1

        # ALTERNATIVE_TO 관계 (대상 Material 노드가 존재할 때만)
        for alt_code in r.get("alternatives", []):
            result = session.run(
                "MATCH (src:Material {code: $src}) "
                "MATCH (tgt:Material {code: $tgt}) "
                "MERGE (src)-[:ALTERNATIVE_TO]->(tgt) "
                "RETURN tgt.code AS matched",
                src=code,
                tgt=alt_code,
            )
            if result.single():
                alt_count += 1

    log.info("  %d개 재질 물성 속성 시드, %d개 ALTERNATIVE_TO 관계 시드 완료.", prop_count, alt_count)
    return prop_count


# ─────────────── 10. 결과 확인 ───────────────
def _print_summary(session) -> None:
    """노드/관계 수 카운트 출력."""
    node_result = session.run("MATCH (n) RETURN count(n) AS cnt").single()
    rel_result = session.run("MATCH ()-[r]->() RETURN count(r) AS cnt").single()
    node_count = node_result["cnt"] if node_result else 0
    rel_count = rel_result["cnt"] if rel_result else 0
    log.info("═══════════════════════════════════════")
    log.info("  전체 노드 수: %d", node_count)
    log.info("  전체 관계 수: %d", rel_count)
    log.info("═══════════════════════════════════════")

    # 라벨별 카운트
    label_result = session.run(
        "CALL db.labels() YIELD label "
        "CALL (label) { "
        "  MATCH (n) WHERE label IN labels(n) "
        "  RETURN count(n) AS cnt "
        "} RETURN label, cnt ORDER BY cnt DESC"
    )
    log.info("  [라벨별 노드 수]")
    for rec in label_result:
        log.info("    %-25s %d", rec["label"], rec["cnt"])

    # 관계 타입별 카운트
    rel_type_result = session.run(
        "CALL db.relationshipTypes() YIELD relationshipType AS rt "
        "CALL (rt) { "
        "  MATCH ()-[r]->() WHERE type(r) = rt "
        "  RETURN count(r) AS cnt "
        "} RETURN rt, cnt ORDER BY cnt DESC"
    )
    log.info("  [관계 타입별 수]")
    for rec in rel_type_result:
        log.info("    %-25s %d", rec["rt"], rec["cnt"])


# ═════════════════════════════════════════════
# 메인
# ═════════════════════════════════════════════

def main() -> None:
    log.info("Neo4j 연결: %s", NEO4J_URI)
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        driver.verify_connectivity()
        log.info("Neo4j 연결 성공.")
    except Exception as e:
        log.error("Neo4j 연결 실패: %s", e)
        sys.exit(1)

    with driver.session() as session:
        try:
            # 1. 기존 데이터 정리
            _clear_graph(session)

            # 2. 공정 시드
            _seed_processes(session)

            # 3. 재질 카테고리 시드
            _seed_material_categories(session)

            # 4. 재질 코드 + alias 시드
            _seed_materials(session)

            # 5. 재질 물성 시드
            _seed_material_properties(session)

            # 6. 재질-공정 호환성 시드
            _seed_compatibility(session)

            # 7. 순서 규칙 시드
            _seed_sequence_constraints(session)

            # 8. 공차 달성 시드
            _seed_tolerances(session)

            # 9. 장비 모델 시드
            _seed_equipment_models(session)

            # 10. 결과 확인
            _print_summary(session)

            log.info("시드 완료.")

        except Exception:
            log.exception("시드 중 오류 발생:")
            sys.exit(1)

    driver.close()
    log.info("Neo4j 드라이버 종료.")


if __name__ == "__main__":
    main()
