"""IMMA Phase 1 매칭 파이프라인 — 룩업 테이블 JSON 로더 + 정적 지식 단일 원천.

모듈 레벨에서 한 번만 JSON을 로드하고,
standard 이름으로 해당 테이블의 data를 반환하는 유틸리티를 제공한다.

또한 파이프라인 전역에서 공유되는 정적 지식 정의(딕셔너리, 집합, 유틸 함수)를
단일 원천으로 관리한다.
"""

import json
import re
from pathlib import Path
from functools import lru_cache

from config import LOOKUP_TABLE_PATH


# ══════════════════════════════════════════════════════════════════════════════
# 정적 지식 정의 (파이프라인 전역 단일 원천)
# ══════════════════════════════════════════════════════════════════════════════

# ── 추상 단계명 → 구체 공정 코드 매핑 (53항목) ──
STAGE_TO_CODES: dict[str, set[str]] = {
    "stock_preparation": set(),
    "rough_machining": {"turning", "milling"},
    "semi_finish_machining": {"turning", "milling", "boring"},
    "finish_machining": {"turning", "milling", "boring", "reaming", "threading", "keyway"},
    "finish_grinding_or_honing_or_lapping": {
        "grinding", "cylindrical_grinding", "surface_grinding",
        "internal_grinding", "centerless_grinding", "honing", "lapping",
    },
    "hardening_heat_treatment": {"heat_treatment"},
    "stress_relieving": {"heat_treatment"},
    "tempering": {"heat_treatment"},
    "drilling_or_boring_pre_hole": {"drilling", "boring"},
    "boring_or_grinding_or_reaming": {
        "boring", "reaming", "cylindrical_grinding", "internal_grinding",
    },
    "precision_machined_or_ground_surface": {
        "milling", "turning", "cylindrical_grinding", "surface_grinding",
    },
    "pre_hole": {"drilling"},
    "gear_blank_turning_and_datum_creation": {"turning"},
    "wire_start_hole_or_edge_entry": {"drilling"},
    "electrically_conductive_workpiece": set(),
    "hardening_or_quenching": {"heat_treatment"},
    "coating_or_plating": {"surface_treatment"},
    "material_removal_finish_machining": {
        "turning", "milling", "grinding", "cylindrical_grinding",
        "surface_grinding", "internal_grinding", "honing", "lapping",
    },
    "EDM_wire_internal_profile": {"edm_wire"},
    "EDM_sinker_or_EDM_wire": {"edm_sinker", "edm_wire"},
    "internal_broaching": {"broaching"},
    "hobbing": {"hobbing"},
    "carburizing": {"heat_treatment"},
    "quenching": {"heat_treatment"},
    "gear_grinding": {"gear_grinding", "cylindrical_grinding", "surface_grinding"},
    "welding": {"welding"},
    "cutting_or_edge_preparation": {
        "laser_cutting", "plasma_cutting", "waterjet_cutting",
    },
    "final_machining": {"turning", "milling", "boring", "reaming"},
    "hard_chrome_plating": {"surface_treatment"},
    "finish_grinding_or_lapping": {
        "cylindrical_grinding", "surface_grinding", "internal_grinding", "lapping",
    },
    "deburring": set(),
    "cleaning": set(),
    "final_inspection": set(),
    "assembly": set(),
    # ── 이하 PROCESS_SEQUENCE_CONSTRAINTS 전수 대조로 보완된 19개 ──
    "EDM_rough_cut": {"edm_sinker", "edm_wire"},
    "EDM_trim_cut_or_grinding": {
        "edm_wire", "cylindrical_grinding", "surface_grinding", "internal_grinding",
    },
    "all_part_machining_complete": set(),
    "deburring_or_shaving": set(),
    "finish_grinding": {
        "cylindrical_grinding", "surface_grinding", "internal_grinding",
    },
    "finish_turning_or_milling": {"turning", "milling"},
    "gear_blank_preparation": {"turning"},
    "gear_grinding_or_honing": {
        "cylindrical_grinding", "surface_grinding", "honing",
    },
    "grinding_if_IT6_or_better": {
        "cylindrical_grinding", "surface_grinding", "internal_grinding",
    },
    "heat_treatment": {"heat_treatment"},
    "hobbing_or_shaping": {"hobbing"},
    "honing": {"honing"},
    "honing_or_lapping_if_Ra_0_2um_or_better": {"honing", "lapping"},
    "lapping": {"lapping"},
    "polishing_or_lapping_if_required": {"lapping"},
    "reaming": {"reaming"},
    "rough_turning_or_milling": {"turning", "milling"},
    "stress_relief_or_heat_treatment_if_required": {"heat_treatment"},
    "turning_datum": {"turning"},
    "grinding_any": {
        "grinding", "cylindrical_grinding", "surface_grinding",
        "internal_grinding", "centerless_grinding",
    },
}


# ── 정밀 공정 집합 (10개 통일) ──
PRECISION_PROCESSES: frozenset[str] = frozenset({
    "grinding", "cylindrical_grinding", "surface_grinding", "internal_grinding",
    "centerless_grinding", "gear_grinding",
    "honing", "lapping",
    "edm_sinker", "edm_wire",
    "boring",
})


# ── 외주/후처리 공정 집합 (SQL 하드필터 제외, 장비 검증 fail-open) ──
FAIL_OPEN_PROCESSES: frozenset[str] = frozenset({
    "heat_treatment", "surface_treatment", "polishing", "deburring",
    "cleaning", "final_inspection", "casting", "welding",
})


# ── parent fallback 화이트리스트 (자식→부모 EPC fallback 허용 자식 공정) ──
# 동일 장비로 양쪽을 모두 수행하는 inclusion 관계가 도메인적으로 성립하는 영역에만 한정.
# grinding 가족(gear_grinding, honing, lapping, cylindrical/surface/internal/centerless)은
# 전용 장비가 분리되어 부모 fallback이 false positive를 일으키므로 제외.
SAFE_PARENT_FALLBACK: frozenset[str] = frozenset({
    "turning_rough", "turning_finish",
    "milling_rough", "milling_finish",
})


# ── 비가공 공정 집합 ──
NON_MACHINING_PROCESSES: frozenset[str] = frozenset({
    "heat_treatment",
    "welding",
    "surface_treatment",
    "casting",
    "sheet_metal",
    "laser_cutting",
    "bending",
    "plasma_cutting",
    "waterjet_cutting",
    "press_forming",
    "polishing",
})


# ── 중간 공정 집합 ──
INTERMEDIATE_PROCESSES: frozenset[str] = frozenset({
    "turning", "turning_rough", "turning_finish",
    "milling", "milling_rough", "milling_finish",
    "drilling", "threading", "reaming",
    "keyway", "broaching", "hobbing",
})


# ── 공정 코드 정규화 맵 ──
PROC_NORMALIZE: dict[str, str] = {
    "grinding_cylindrical": "cylindrical_grinding",
    "grinding_surface": "surface_grinding",
    "grinding_internal": "internal_grinding",
    "EDM_sinker": "edm_sinker",
    "EDM_wire": "edm_wire",
}


# ── 파이프라인 process_code → 룩업 process 매핑 (rough/finish 분리) ──
PROCESS_TO_LOOKUP_PROCESSES: dict[str, list[str]] = {
    "turning": ["turning_rough", "turning_finish"],
    "milling": ["milling_rough", "milling_finish"],
}


# ── VLM category 텍스트 → DB category_code 매핑 ──
CATEGORY_TEXT_TO_CODE: dict[str, str] = {
    # DB category_name_ko 기준
    "탄소강": "carbon_steel",
    "합금강": "alloy_steel",
    "스테인리스강": "stainless_steel",
    "회주철": "gray_cast_iron",
    "주강": "cast_steel",
    "알루미늄 합금": "aluminum_alloy",
    "구리합금": "copper_alloy",
    "판금용 강판": "sheet_steel",
    "공구강": "tool_steel",
    # VLM이 출력할 수 있는 변형
    "크롬몰리브덴강": "alloy_steel",
    "크롬강": "alloy_steel",
    "니켈크롬몰리브덴강": "alloy_steel",
    "스테인리스": "stainless_steel",
    "스텐": "stainless_steel",
    "스뎅": "stainless_steel",
    "주철": "gray_cast_iron",
    "알루미늄": "aluminum_alloy",
    "구리": "copper_alloy",
    "황동": "copper_alloy",
    "베릴륨동": "copper_alloy",
    "인청동": "copper_alloy",
    "쾌삭강": "free_cutting_steel",
    "기계구조용 탄소강": "carbon_steel",
    "구조용 강재": "carbon_steel",
    "스프링강": "alloy_steel",
    "탄소주강": "cast_steel",
    # 엔지니어링 플라스틱
    "엔지니어링 플라스틱": "engineering_plastic",
    "플라스틱": "engineering_plastic",
    "수지": "engineering_plastic",
    "POM": "engineering_plastic",
    "MC나일론": "engineering_plastic",
    "나일론": "engineering_plastic",
    "PTFE": "engineering_plastic",
    "PEEK": "engineering_plastic",
    "폴리카보네이트": "engineering_plastic",
    "아크릴": "engineering_plastic",
    "ABS": "engineering_plastic",
    # 구상흑연주철
    "구상흑연주철": "ductile_cast_iron",
    "FCD": "ductile_cast_iron",
    "GCD": "ductile_cast_iron",
    "덕타일주철": "ductile_cast_iron",
    # 복합재/절연재
    "복합재": "composite_insulation",
    "절연재": "composite_insulation",
    "베크라이트": "composite_insulation",
    "에폭시": "composite_insulation",
    # 스테인리스 주강
    "스테인리스 주강": "stainless_cast_steel",
    "스테인리스주강": "stainless_cast_steel",
    "스텐주강": "stainless_cast_steel",
    "SCS13": "stainless_cast_steel",
    "SCS14": "stainless_cast_steel",
    "SCS16": "stainless_cast_steel",
}


# ══════════════════════════════════════════════════════════════════════════════
# 유틸리티 함수
# ══════════════════════════════════════════════════════════════════════════════

def parse_it(val: str | None) -> int | None:
    """'IT7' -> 7. None이면 None."""
    if val is None:
        return None
    m = re.search(r'IT(\d+)', str(val))
    return int(m.group(1)) if m else None


# ══════════════════════════════════════════════════════════════════════════════
# 룩업 테이블 JSON 로더
# ══════════════════════════════════════════════════════════════════════════════

@lru_cache(maxsize=1)
def _load_all() -> dict[str, list[dict]]:
    """룩업 JSON 전체를 로드하여 {standard: [object, ...]} 딕셔너리로 캐싱한다.

    동일 standard 이름의 object가 여러 개일 수 있으므로(e.g. KS_B_ISO_2768_1의
    선형/각도/모따기 세 테이블) 리스트로 저장한다.
    """
    raw = json.loads(Path(LOOKUP_TABLE_PATH).read_text(encoding="utf-8"))
    result: dict[str, list[dict]] = {}
    for obj in raw["objects"]:
        result.setdefault(obj["standard"], []).append(obj)
    return result


def get_table(standard: str, index: int = 0) -> list[dict]:
    """지정한 standard의 data 배열을 반환한다.

    Args:
        standard: 테이블 식별자
        index: 동일 standard가 여러 개일 때 인덱스 (기본 0 = 첫 번째)

    Returns:
        data 배열. 없으면 빈 리스트.
    """
    objs = _load_all().get(standard, [])
    if index < len(objs):
        return objs[index].get("data", [])
    return []


def get_all_tables(standard: str) -> list[list[dict]]:
    """동일 standard의 모든 data 배열을 리스트로 반환한다."""
    objs = _load_all().get(standard, [])
    return [obj.get("data", []) for obj in objs]
