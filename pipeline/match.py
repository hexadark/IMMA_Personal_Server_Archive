"""IMMA Phase 1 매칭 파이프라인 — [5] 하드필터 SQL 동적 생성/실행 + 장비 검증.

ResolvedPart 기준으로 company_capability_summary MV에 하드필터를 적용하고,
통과한 후보에 대해 equipment_process_capabilities 수준 검증을 수행한다.
공정별 달성 가능 공차/조도를 룩업 테이블에서 참조하여 교차 검증한다.
"""

import logging
import re

import db

logger = logging.getLogger(__name__)
from models import ResolvedPart, MatchCandidate
from lookup import get_table


# ── IT/Ra 검증이 불필요한 비가공 공정 그룹 ──
# process_catalog의 process_group 기준: post_process, joining, forming, sheet_metal
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
})
"""비가공 공정 코드 집합. 이 공정들은 IT/Ra 장비 검증을 스킵한다."""


# ── 정밀 공정 vs 중간 공정 분류 ──
# 정밀 공정: 최종 IT/Ra 달성을 담당하는 공정
PRECISION_PROCESSES: frozenset[str] = frozenset({
    "grinding", "cylindrical_grinding", "surface_grinding", "internal_grinding",
    "centerless_grinding",
    "honing", "lapping",
    "edm_sinker", "edm_wire",
    "boring",
})

# 중간 공정: 중간 가공 공정 (최종 정밀도 달성이 아닌 형상 가공)
INTERMEDIATE_PROCESSES: frozenset[str] = frozenset({
    "turning", "milling", "drilling", "threading",
    "keyway", "broaching", "hobbing",
})


# ── 룩업 JSON에서 PROCESS_ACHIEVABLE_TOLERANCE 로드 ──
# {process_family: {typical_it_min, typical_it_max, precision_it_min, precision_it_max,
#                   typical_ra_min, typical_ra_max, precision_ra_min, precision_ra_max}}
_PROCESS_TOLERANCE: dict[str, dict] = {}


def _parse_it(val: str | None) -> int | None:
    """'IT7' -> 7"""
    if val is None:
        return None
    m = re.search(r'IT(\d+)', str(val))
    return int(m.group(1)) if m else None


for _entry in get_table("PROCESS_ACHIEVABLE_TOLERANCE"):
    _family = _entry.get("process_family", "")
    _process = _entry.get("process", "")
    _ait = _entry.get("achievable_IT", {})
    _ara = _entry.get("achievable_Ra_um", {})

    _info = {
        "process": _process,
        "typical_it_min": _parse_it(_ait.get("typical", {}).get("min")),
        "typical_it_max": _parse_it(_ait.get("typical", {}).get("max")),
        "precision_it_min": _parse_it(_ait.get("precision", {}).get("min")),
        "precision_it_max": _parse_it(_ait.get("precision", {}).get("max")),
        "typical_ra_min": _ara.get("typical", {}).get("min"),
        "typical_ra_max": _ara.get("typical", {}).get("max"),
        "precision_ra_min": _ara.get("precision", {}).get("min"),
        "precision_ra_max": _ara.get("precision", {}).get("max"),
    }

    # process_family 수준으로 가장 넓은 범위(황삭~정삭 모두 포함)를 병합
    if _family not in _PROCESS_TOLERANCE:
        _PROCESS_TOLERANCE[_family] = _info.copy()
    else:
        existing = _PROCESS_TOLERANCE[_family]
        # precision 쪽(가장 좋은 IT/Ra)으로 확장
        if _info["precision_it_min"] is not None:
            if existing["precision_it_min"] is None or _info["precision_it_min"] < existing["precision_it_min"]:
                existing["precision_it_min"] = _info["precision_it_min"]
        # typical 쪽(가장 나쁜 IT)으로 확장
        if _info["typical_it_max"] is not None:
            if existing["typical_it_max"] is None or _info["typical_it_max"] > existing["typical_it_max"]:
                existing["typical_it_max"] = _info["typical_it_max"]
        # Ra도 동일 논리
        if _info["precision_ra_min"] is not None:
            if existing["precision_ra_min"] is None or _info["precision_ra_min"] < existing["precision_ra_min"]:
                existing["precision_ra_min"] = _info["precision_ra_min"]
        if _info["typical_ra_max"] is not None:
            if existing["typical_ra_max"] is None or _info["typical_ra_max"] > existing["typical_ra_max"]:
                existing["typical_ra_max"] = _info["typical_ra_max"]

    # 개별 process 이름으로도 저장 (정확 매칭용)
    _PROCESS_TOLERANCE[_process] = _info

# ── 공정 계층: parent_process_code 매핑 (process_catalog seed 기반) ──
# 하위 공정 → 상위 공정. 도면이 하위 공정을 요구할 때, 업체에 상위 공정이 있으면
# 하드필터를 통과시킨다 (장비 검증에서 실제 능력 확인).
_PROCESS_PARENT: dict[str, str] = {}


def _load_process_parents() -> None:
    """process_catalog에서 parent_process_code 관계를 로드한다."""
    if _PROCESS_PARENT:
        return
    try:
        rows = db.execute_query(
            """SELECT process_code, parent_process_code
               FROM imma.process_catalog
               WHERE parent_process_code IS NOT NULL"""
        )
        for r in rows:
            _PROCESS_PARENT[r["process_code"]] = r["parent_process_code"]
    except Exception as e:
        logger.warning("process_catalog parent 로드 실패 — 공정 상하위 매핑 비활성화: %s", e)


def _expand_processes_with_parents(procs: list[str]) -> list[str]:
    """각 공정에 대해 parent가 있으면 parent도 대안으로 포함한 리스트를 반환한다.
    반환값은 중복 제거된 리스트. 원래 공정 + parent 공정 합집합."""
    _load_process_parents()
    expanded = set(procs)
    for p in procs:
        parent = _PROCESS_PARENT.get(p)
        if parent:
            expanded.add(parent)
    return list(expanded)


_SELECT_COLS = """
    company_id,
    company_name,
    material_codes,
    material_category_codes,
    process_codes,
    best_it_grade,
    best_ra_um,
    max_turning_diameter_mm,
    max_turning_length_mm,
    max_x_mm,
    max_y_mm,
    max_z_mm,
    next_available_date,
    overall_status,
    avg_rating_overall,
    review_count
"""

_ORDER_BY = """
ORDER BY
    CASE WHEN overall_status = 'available' THEN 0 ELSE 1 END,
    avg_rating_overall DESC NULLS LAST
"""


def build_hard_filter_sql(
    part: ResolvedPart, use_category: bool = False
) -> tuple[str, list]:
    """ResolvedPart의 속성으로 동적 WHERE절을 조립한다.

    use_category=False → Step 1 (material_codes 코드 매칭)
    use_category=True  → Step 2 (material_category_codes 카테고리 매칭)

    null인 조건은 WHERE절에서 생략한다.
    """
    conditions = []
    params: list = []

    # 재질 조건
    if not use_category and part.material_code:
        conditions.append("material_codes @> ARRAY[upper(%s)]::text[]")
        params.append(part.material_code)
    elif part.category_code:
        conditions.append("material_category_codes @> ARRAY[%s]::text[]")
        params.append(part.category_code)

    # 공정 조건: 각 필수 공정에 대해 해당 공정 또는 parent 공정이 있으면 통과
    if part.required_processes:
        _load_process_parents()
        proc_conditions = []
        for proc in part.required_processes:
            parent = _PROCESS_PARENT.get(proc)
            if parent:
                proc_conditions.append(
                    "(process_codes && ARRAY[%s, %s]::text[])"
                )
                params.append(proc)
                params.append(parent)
            else:
                proc_conditions.append(
                    "(process_codes @> ARRAY[%s]::text[])"
                )
                params.append(proc)
        if proc_conditions:
            conditions.append("(" + " AND ".join(proc_conditions) + ")")

    # 크기 조건 — 축물
    if part.shape_type == "turning":
        if part.envelope_diameter is not None:
            conditions.append("COALESCE(max_turning_diameter_mm, 0) >= %s")
            params.append(part.envelope_diameter)
        if part.envelope_length is not None:
            conditions.append("COALESCE(max_turning_length_mm, 0) >= %s")
            params.append(part.envelope_length)
    else:
        # 각형물
        if part.envelope_length is not None:
            conditions.append("COALESCE(max_x_mm, 0) >= %s")
            params.append(part.envelope_length)
        if part.envelope_width is not None:
            conditions.append("COALESCE(max_y_mm, 0) >= %s")
            params.append(part.envelope_width)
        if part.envelope_height is not None:
            conditions.append("COALESCE(max_z_mm, 0) >= %s")
            params.append(part.envelope_height)

    # IT 등급
    if part.tightest_it is not None:
        conditions.append("best_it_grade <= %s")
        params.append(part.tightest_it)

    # Ra 조도
    if part.finest_ra is not None:
        conditions.append("best_ra_um <= %s")
        params.append(part.finest_ra)

    # 가용 상태
    conditions.append(
        "COALESCE(overall_status, 'unknown') IN ('available', 'limited', 'unknown')"
    )

    where_clause = " AND ".join(conditions) if conditions else "TRUE"
    sql = f"SELECT {_SELECT_COLS} FROM imma.company_capability_summary WHERE {where_clause} {_ORDER_BY}"
    return (sql, params)


def _rows_to_candidates(rows: list[dict], match_type: str) -> list[MatchCandidate]:
    """DB 결과 행을 MatchCandidate 리스트로 변환한다."""
    candidates = []
    for r in rows:
        candidates.append(MatchCandidate(
            company_id=str(r["company_id"]),
            company_name=r["company_name"],
            material_codes=r.get("material_codes") or [],
            material_category_codes=r.get("material_category_codes") or [],
            process_codes=r.get("process_codes") or [],
            best_it_grade=int(r["best_it_grade"]) if r.get("best_it_grade") is not None else None,
            best_ra_um=float(r["best_ra_um"]) if r.get("best_ra_um") is not None else None,
            max_turning_diameter_mm=float(r["max_turning_diameter_mm"]) if r.get("max_turning_diameter_mm") is not None else None,
            max_turning_length_mm=float(r["max_turning_length_mm"]) if r.get("max_turning_length_mm") is not None else None,
            max_x_mm=float(r["max_x_mm"]) if r.get("max_x_mm") is not None else None,
            max_y_mm=float(r["max_y_mm"]) if r.get("max_y_mm") is not None else None,
            max_z_mm=float(r["max_z_mm"]) if r.get("max_z_mm") is not None else None,
            overall_status=r.get("overall_status"),
            avg_rating_overall=float(r["avg_rating_overall"]) if r.get("avg_rating_overall") is not None else None,
            review_count=int(r.get("review_count", 0)),
            next_available_date=str(r["next_available_date"]) if r.get("next_available_date") else None,
            material_match_type=match_type,
        ))
    return candidates


def run_hard_filter(part: ResolvedPart) -> list[MatchCandidate]:
    """Step 1(코드 매칭) 실행 → 0건이면 Step 2(카테고리 확장) 실행."""

    # Step 1: 코드 매칭
    if part.material_code:
        sql, params = build_hard_filter_sql(part, use_category=False)
        rows = db.execute_query(sql, tuple(params))
        if rows:
            return _rows_to_candidates(rows, "code")

    # Step 2: 카테고리 매칭
    if part.category_code:
        sql, params = build_hard_filter_sql(part, use_category=True)
        rows = db.execute_query(sql, tuple(params))
        return _rows_to_candidates(rows, "category")

    return []


def _check_process_achievability(
    proc: str, claimed_it: int | None, claimed_ra: float | None
) -> list[str]:
    """업체 장비가 주장하는 IT/Ra가 해당 공정에서 이론적으로 달성 가능한 범위인지 교차 검증.

    룩업 테이블의 PROCESS_ACHIEVABLE_TOLERANCE 데이터를 참조한다.
    Returns: 의심 사유 문자열 리스트 (비어 있으면 정상)
    """
    warnings: list[str] = []

    # process_family로 먼저 탐색, 없으면 개별 process로
    ref = _PROCESS_TOLERANCE.get(proc)
    if ref is None:
        return warnings

    # IT 교차 검증: 업체 주장 IT가 precision 최솟값보다 좋으면 의심
    if claimed_it is not None and ref.get("precision_it_min") is not None:
        if claimed_it < ref["precision_it_min"]:
            warnings.append(
                f"[공정 달성범위 의심] {proc}: 업체 주장 IT{claimed_it} < "
                f"이론적 precision 하한 IT{ref['precision_it_min']}"
            )

    # Ra 교차 검증: 업체 주장 Ra가 precision 최솟값보다 좋으면 의심
    if claimed_ra is not None and ref.get("precision_ra_min") is not None:
        if claimed_ra < ref["precision_ra_min"]:
            warnings.append(
                f"[공정 달성범위 의심] {proc}: 업체 주장 Ra {claimed_ra}µm < "
                f"이론적 precision 하한 {ref['precision_ra_min']}µm"
            )

    return warnings


def _is_within_process_typical_range(
    proc: str, eq_it: int | None, eq_ra: float | None
) -> tuple[bool, str]:
    """중간 공정의 장비 IT/Ra가 해당 공정의 typical 범위 안인지 확인.

    Returns: (범위내 여부, 설명 문자열)
    """
    ref = _PROCESS_TOLERANCE.get(proc)
    if ref is None:
        return True, f"{proc} (룩업 데이터 없음, 통과)"

    # IT 검증: typical 범위 내이면 OK (typical_it_min ~ typical_it_max)
    if eq_it is not None and ref.get("typical_it_min") is not None and ref.get("typical_it_max") is not None:
        if eq_it > ref["typical_it_max"]:
            return False, f"{proc} 장비 IT{eq_it} > typical 상한 IT{ref['typical_it_max']}"

    # Ra 검증: typical 범위 내이면 OK
    if eq_ra is not None and ref.get("typical_ra_max") is not None:
        if eq_ra > ref["typical_ra_max"]:
            return False, f"{proc} 장비 Ra {eq_ra}µm > typical 상한 {ref['typical_ra_max']}µm"

    it_str = f"IT{eq_it}" if eq_it is not None else "IT?"
    return True, f"{proc} {it_str} (중간 공정 범위 내)"


def run_equipment_verification(
    candidates: list[MatchCandidate], part: ResolvedPart
) -> list[MatchCandidate]:
    """후보 업체별로 equipment_process_capabilities에서 해당 공정의 실제 IT/Ra 확인.
    미달이면 equipment_verified=False 플래그.

    공정 역할별 검증 로직:
    - 정밀 공정(grinding, honing 등): tightest_it/finest_ra 달성 여부를 직접 확인
    - 중간 공정(turning, milling 등): 해당 공정의 typical 범위 내이면 OK
    - 정밀 공정이 없으면: 중간 공정 중 가장 정밀한 것이 tightest_it를 만족하는지 확인

    추가로 룩업 테이블의 PROCESS_ACHIEVABLE_TOLERANCE와 교차 검증하여
    이론적 달성 범위를 벗어나는 주장에 대해 warnings를 남긴다.
    """
    if not part.required_processes:
        return candidates

    # 필수 공정을 역할별로 분류
    precision_procs = [p for p in part.required_processes
                       if p in PRECISION_PROCESSES and p not in NON_MACHINING_PROCESSES]
    intermediate_procs = [p for p in part.required_processes
                          if p in INTERMEDIATE_PROCESSES and p not in NON_MACHINING_PROCESSES]
    has_precision = len(precision_procs) > 0

    for cand in candidates:
        failed = False

        # ── 정밀 공정 검증: tightest_it/finest_ra 달성 여부 ──
        for proc in precision_procs:
            rows = db.execute_query(
                """SELECT epc.best_achievable_it_grade, epc.best_ra_um
                   FROM imma.equipment_process_capabilities epc
                   JOIN imma.equipment e ON e.equipment_id = epc.equipment_id
                   WHERE e.company_id = %s::uuid
                     AND (epc.process_code = %s
                          OR epc.process_code IN (
                              SELECT process_code FROM imma.process_catalog
                              WHERE parent_process_code = %s))
                     AND e.status IN ('running', 'idle')""",
                (cand.company_id, proc, proc),
            )
            if not rows:
                continue

            best_eq_it = None
            best_eq_ra = None
            for r in rows:
                it = r.get("best_achievable_it_grade")
                ra = r.get("best_ra_um")
                if it is not None:
                    best_eq_it = min(best_eq_it, it) if best_eq_it is not None else it
                if ra is not None:
                    best_eq_ra = min(best_eq_ra, float(ra)) if best_eq_ra is not None else float(ra)

            # IT 미달 검증
            if part.tightest_it is not None and best_eq_it is not None:
                if best_eq_it > part.tightest_it:
                    cand.equipment_verified = False
                    cand.match_reasons.append(
                        f"{proc} 장비 IT{best_eq_it} > 도면 요구 IT{part.tightest_it} (정밀 공정 미달)"
                    )
                    failed = True
                    break
                else:
                    cand.match_reasons.append(
                        f"{proc} IT{best_eq_it} (정밀 공정, 도면 IT{part.tightest_it} 충족)"
                    )

            # Ra 미달 검증
            if not failed and part.finest_ra is not None and best_eq_ra is not None:
                if best_eq_ra > part.finest_ra:
                    cand.equipment_verified = False
                    cand.match_reasons.append(
                        f"{proc} 장비 Ra {best_eq_ra}µm > 도면 요구 Ra {part.finest_ra}µm (정밀 공정 미달)"
                    )
                    failed = True
                    break

            # 공정별 달성 가능 범위 교차 검증
            achievability_warnings = _check_process_achievability(proc, best_eq_it, best_eq_ra)
            if achievability_warnings:
                cand.match_reasons.extend(achievability_warnings)

        if failed:
            continue

        # ── 중간 공정 검증 ──
        best_intermediate_it = None
        best_intermediate_ra = None

        for proc in intermediate_procs:
            rows = db.execute_query(
                """SELECT epc.best_achievable_it_grade, epc.best_ra_um
                   FROM imma.equipment_process_capabilities epc
                   JOIN imma.equipment e ON e.equipment_id = epc.equipment_id
                   WHERE e.company_id = %s::uuid
                     AND (epc.process_code = %s
                          OR epc.process_code IN (
                              SELECT process_code FROM imma.process_catalog
                              WHERE parent_process_code = %s))
                     AND e.status IN ('running', 'idle')""",
                (cand.company_id, proc, proc),
            )
            if not rows:
                continue

            best_eq_it = None
            best_eq_ra = None
            for r in rows:
                it = r.get("best_achievable_it_grade")
                ra = r.get("best_ra_um")
                if it is not None:
                    best_eq_it = min(best_eq_it, it) if best_eq_it is not None else it
                if ra is not None:
                    best_eq_ra = min(best_eq_ra, float(ra)) if best_eq_ra is not None else float(ra)

            if has_precision:
                # 정밀 공정이 있으면 중간 공정은 typical 범위 내이면 OK
                in_range, reason = _is_within_process_typical_range(proc, best_eq_it, best_eq_ra)
                if not in_range:
                    cand.equipment_verified = False
                    cand.match_reasons.append(f"{reason} (중간 공정 범위 초과)")
                    failed = True
                    break
                else:
                    cand.match_reasons.append(reason)
            else:
                # 정밀 공정이 없으면 중간 공정의 best IT/Ra를 추적
                in_range, reason = _is_within_process_typical_range(proc, best_eq_it, best_eq_ra)
                cand.match_reasons.append(reason)
                if best_eq_it is not None:
                    best_intermediate_it = min(best_intermediate_it, best_eq_it) if best_intermediate_it is not None else best_eq_it
                if best_eq_ra is not None:
                    best_intermediate_ra = min(best_intermediate_ra, best_eq_ra) if best_intermediate_ra is not None else best_eq_ra

            # 공정별 달성 가능 범위 교차 검증
            achievability_warnings = _check_process_achievability(proc, best_eq_it, best_eq_ra)
            if achievability_warnings:
                cand.match_reasons.extend(achievability_warnings)

        if failed:
            continue

        # ── 정밀 공정이 없는 경우: 중간 공정 중 가장 정밀한 것으로 최종 판정 ──
        if not has_precision:
            if part.tightest_it is not None and best_intermediate_it is not None:
                if best_intermediate_it > part.tightest_it:
                    cand.equipment_verified = False
                    cand.match_reasons.append(
                        f"중간 공정 최고 IT{best_intermediate_it} > 도면 요구 IT{part.tightest_it} (정밀 공정 없음)"
                    )
            if part.finest_ra is not None and best_intermediate_ra is not None:
                if best_intermediate_ra > part.finest_ra:
                    cand.equipment_verified = False
                    cand.match_reasons.append(
                        f"중간 공정 최고 Ra {best_intermediate_ra}µm > 도면 요구 Ra {part.finest_ra}µm (정밀 공정 없음)"
                    )

    return candidates
