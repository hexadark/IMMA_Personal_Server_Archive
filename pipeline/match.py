"""IMMA Phase 1 매칭 파이프라인 — [5] 하드필터 SQL 동적 생성/실행 + 장비 검증.

ResolvedPart 기준으로 company_capability_summary MV에 하드필터를 적용하고,
통과한 후보에 대해 equipment_process_capabilities 수준 검증을 수행한다.
공정별 달성 가능 공차/조도를 룩업 테이블에서 참조하여 교차 검증한다.
"""

import logging

import db

logger = logging.getLogger(__name__)
from models import ResolvedPart, MatchCandidate
from lookup import (
    get_table, parse_it,
    NON_MACHINING_PROCESSES, PRECISION_PROCESSES, INTERMEDIATE_PROCESSES,
    STAGE_TO_CODES, PROC_NORMALIZE, FAIL_OPEN_PROCESSES, SAFE_PARENT_FALLBACK,
)
# 핵심 공정: 장비 row가 없으면 경고 + equipment_verified_warning 플래그
_STRICT_EQUIPMENT_PROCESSES: frozenset[str] = frozenset({
    "turning", "turning_rough", "turning_finish",
    "milling", "milling_rough", "milling_finish",
    "drilling", "boring", "threading", "reaming", "keyway", "broaching",
    "grinding", "cylindrical_grinding", "surface_grinding",
    "internal_grinding", "centerless_grinding",
    "honing", "lapping",
    "edm_sinker", "edm_wire",
    "hobbing", "gear_grinding",
    "laser_cutting", "plasma_cutting", "waterjet_cutting",
    "bending", "press_forming",
})


# ── 룩업 JSON에서 PROCESS_ACHIEVABLE_TOLERANCE 로드 ──
# {process_family: {typical_it_min, typical_it_max, precision_it_min, precision_it_max,
#                   typical_ra_min, typical_ra_max, precision_ra_min, precision_ra_max}}
_PROCESS_TOLERANCE: dict[str, dict] = {}

# ── 룩업 JSON에서 MATERIAL_PROCESS_CAPABILITY_OVERRIDES 로드 ──
# 비금속 재질(엔지니어링 플라스틱, 복합재 등)의 공정별 IT/Ra 기준을 금속 기준 대신 사용.
# 키: (material_code, process_code) 또는 (category_code, process_code)
# material_code 매칭이 category_code보다 우선.
_MATERIAL_PROCESS_OVERRIDES: dict[tuple[str, str, str], dict] = {}

for _ovr in get_table("MATERIAL_PROCESS_CAPABILITY_OVERRIDES"):
    _cat = _ovr.get("material_category_code", "")
    _mat = _ovr.get("material_code")  # None이면 카테고리 수준
    _proc = _ovr.get("process_code", "")
    _it = _ovr.get("typical_it_grade", {})
    _ra = _ovr.get("typical_ra_um", {})
    _key = (_cat, _mat or "", _proc)
    _MATERIAL_PROCESS_OVERRIDES[_key] = {
        "typical_it_min": _it.get("min"),
        "typical_it_max": _it.get("max"),
        "typical_ra_min": _ra.get("min"),
        "typical_ra_max": _ra.get("max"),
        "special_requirements": _ovr.get("special_requirements", []),
    }


_OVERRIDE_PROC_NORMALIZE: dict[str, str] = {
    "turning_rough": "turning", "turning_finish": "turning",
    "milling_rough": "milling", "milling_finish": "milling",
}


def _get_override_tolerance(
    category_code: str | None, material_code: str | None, process_code: str
) -> dict | None:
    """해당 재질+공정 조합에 override가 있으면 반환. material_code 우선, 없으면 category_code.
    rough/finish 하위 공정은 부모 공정으로 정규화하여 조회한다."""
    process_code = _OVERRIDE_PROC_NORMALIZE.get(process_code, process_code)
    if material_code:
        key = (category_code or "", material_code, process_code)
        if key in _MATERIAL_PROCESS_OVERRIDES:
            return _MATERIAL_PROCESS_OVERRIDES[key]
        # material_code만으로 category를 모를 때: 모든 카테고리 순회
        for k, v in _MATERIAL_PROCESS_OVERRIDES.items():
            if k[1] == material_code and k[2] == process_code:
                return v
    if category_code:
        # 카테고리 수준 override (material_code == "")
        key = (category_code, "", process_code)
        if key in _MATERIAL_PROCESS_OVERRIDES:
            return _MATERIAL_PROCESS_OVERRIDES[key]
        # category-only fallback: 해당 카테고리의 아무 override라도 있으면
        # 가장 보수적인(IT 숫자 큰, Ra 큰) 값을 대표로 사용
        cat_overrides = [
            v for k, v in _MATERIAL_PROCESS_OVERRIDES.items()
            if k[0] == category_code and k[2] == process_code
        ]
        if cat_overrides:
            conservative = {
                "typical_it_min": None,
                "typical_it_max": None,
                "typical_ra_min": None,
                "typical_ra_max": None,
                "special_requirements": [],
            }
            for ov in cat_overrides:
                it_max = ov.get("typical_it_max")
                if it_max is not None:
                    if conservative["typical_it_max"] is None or it_max > conservative["typical_it_max"]:
                        conservative["typical_it_max"] = it_max
                it_min = ov.get("typical_it_min")
                if it_min is not None:
                    if conservative["typical_it_min"] is None or it_min < conservative["typical_it_min"]:
                        conservative["typical_it_min"] = it_min
                ra_max = ov.get("typical_ra_max")
                if ra_max is not None:
                    if conservative["typical_ra_max"] is None or ra_max > conservative["typical_ra_max"]:
                        conservative["typical_ra_max"] = ra_max
                ra_min = ov.get("typical_ra_min")
                if ra_min is not None:
                    if conservative["typical_ra_min"] is None or ra_min < conservative["typical_ra_min"]:
                        conservative["typical_ra_min"] = ra_min
            return conservative
    return None


for _entry in get_table("PROCESS_ACHIEVABLE_TOLERANCE"):
    _family = _entry.get("process_family", "")
    _process_raw = _entry.get("process", "")
    _process = PROC_NORMALIZE.get(_process_raw, _process_raw)
    _ait = _entry.get("achievable_IT", {})
    _ara = _entry.get("achievable_Ra_um", {})

    _info = {
        "process": _process,
        "typical_it_min": parse_it(_ait.get("typical", {}).get("min")),
        "typical_it_max": parse_it(_ait.get("typical", {}).get("max")),
        "precision_it_min": parse_it(_ait.get("precision", {}).get("min")),
        "precision_it_max": parse_it(_ait.get("precision", {}).get("max")),
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


# 카테고리 확장 매핑 (하위 카테고리 → 상위 카테고리 fallback)
# free_cutting_steel → carbon_steel: 명확 inclusion (피삭성 더 좋음, 능력 inclusion 성립)
# cast_steel → carbon_steel: 부분 inclusion (절삭/연마는 inclusion, 주물 결함 대응 노하우는 별개)
# stainless_cast_steel → stainless_steel: 부분 inclusion (절삭/연마는 inclusion, 주물 결함 대응 노하우는 별개)
_CATEGORY_EXPANSION: dict[str, list[str]] = {
    "free_cutting_steel": ["free_cutting_steel", "carbon_steel"],
    "cast_steel": ["cast_steel", "carbon_steel"],
    "stainless_cast_steel": ["stainless_cast_steel", "stainless_steel"],
}

# 카테고리 fallback 발동 시 reasons에 부착할 신호 메시지
# 부분 inclusion fallback에 한해 부수 노하우 요구 사실을 발주자에 전달
_CATEGORY_FALLBACK_SIGNAL: dict[str, str] = {
    "cast_steel": (
        "[INFO_CATEGORY_FALLBACK] cast_steel → carbon_steel: "
        "주물 결함(기공/표피/개재물) 대응 노하우 별도 확인 필요"
    ),
    "stainless_cast_steel": (
        "[INFO_CATEGORY_FALLBACK] stainless_cast_steel → stainless_steel: "
        "주물 결함(기공/표피/개재물) 대응 노하우 별도 확인 필요"
    ),
}


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
        expanded = _CATEGORY_EXPANSION.get(part.category_code, [part.category_code])
        if len(expanded) == 1:
            conditions.append("material_category_codes @> ARRAY[%s]::text[]")
            params.append(expanded[0])
        else:
            conditions.append(
                "material_category_codes && ARRAY["
                + ",".join(["%s"] * len(expanded))
                + "]::text[]"
            )
            params.extend(expanded)

    # 공정 조건: 각 필수 공정에 대해 해당 공정 또는 parent 공정이 있으면 통과
    # parent fallback은 안전한 공정(turning_rough→turning 등)에만 허용
    if part.required_processes:
        _load_process_parents()
        proc_conditions = []
        for proc in part.required_processes:
            # 외주/후처리 공정은 업체 외주망으로 처리되므로 SQL 1차 필터에서 제외.
            # 정보는 part.required_processes로 보존되어 응답·견적 단계로 전달됨.
            if proc in FAIL_OPEN_PROCESSES:
                continue
            parent = _PROCESS_PARENT.get(proc)
            if parent and proc in SAFE_PARENT_FALLBACK:
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


def _populate_equipment_summary(candidates: list[MatchCandidate]) -> None:
    """후보별 카테고리별 보유 장비 수 + 대표 모델명을 채운다.

    공개 자연 — 비밀 영역 부재 (사용자 결재 완료). 단부품/다부품 무관 동일 적용.
    """
    if not candidates:
        return
    company_ids = [c.company_id for c in candidates]
    rows = db.execute_query(
        """
        SELECT
            e.company_id::text AS company_id,
            e.equipment_category_code,
            ecc.category_name_ko,
            COUNT(*) AS cnt,
            (ARRAY_AGG(
                COALESCE(emc.model_name, e.model_name, e.display_name)
                ORDER BY COALESCE(e.year_made, 0) DESC NULLS LAST
            ))[1] AS representative_model
        FROM imma.equipment e
        JOIN imma.equipment_category_catalog ecc
            ON ecc.equipment_category_code = e.equipment_category_code
        LEFT JOIN imma.equipment_model_catalog emc
            ON emc.model_id = e.model_id
        WHERE e.company_id = ANY(%s::uuid[])
          AND e.status IN ('running', 'idle')
        GROUP BY e.company_id, e.equipment_category_code, ecc.category_name_ko
        ORDER BY e.company_id, cnt DESC
        """,
        (company_ids,),
    )
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        cid = r["company_id"]
        grouped.setdefault(cid, []).append({
            "category_code": r["equipment_category_code"],
            "category_name_ko": r["category_name_ko"],
            "count": int(r["cnt"]),
            "representative_model": r["representative_model"],
        })
    for cand in candidates:
        cand.equipment_summary = grouped.get(cand.company_id, [])


def run_hard_filter(part: ResolvedPart) -> list[MatchCandidate]:
    """Step 1(코드 매칭) 실행 → 0건이면 Step 2(카테고리 확장) 실행.

    카테고리 확장이 정의된 재질(예: free_cutting_steel → carbon_steel 업체 포함)은
    Step 1 결과가 있어도 Step 2를 추가 실행하여 recall을 보존한다.

    단일 출구 정책: 모든 분기에서 result 리스트를 구성한 뒤 마지막에
    _populate_equipment_summary 를 한 차례 호출하고 반환한다.
    """

    # Step 1: 코드 매칭
    code_candidates: list[MatchCandidate] = []
    if part.material_code:
        sql, params = build_hard_filter_sql(part, use_category=False)
        rows = db.execute_query(sql, tuple(params))
        if rows:
            code_candidates = _rows_to_candidates(rows, "code")

    # 카테고리 확장이 필요한 재질이면 Step 1 결과가 있어도 Step 2 merge
    needs_category_expansion = part.category_code in _CATEGORY_EXPANSION

    result: list[MatchCandidate]

    if code_candidates and not needs_category_expansion:
        result = code_candidates
    elif part.category_code:
        # Step 2: 카테고리 매칭
        sql, params = build_hard_filter_sql(part, use_category=True)
        rows = db.execute_query(sql, tuple(params))
        cat_candidates = _rows_to_candidates(rows, "category")

        # 부분 inclusion fallback(예: stainless_cast_steel → stainless_steel)의 경우,
        # 원래 카테고리를 보유하지 않은 후보(즉 상위 카테고리로만 매칭된 후보)에 신호 부착
        fallback_signal = _CATEGORY_FALLBACK_SIGNAL.get(part.category_code)
        if fallback_signal:
            for cand in cat_candidates:
                if part.category_code not in (cand.material_category_codes or []):
                    cand.match_reasons.append(fallback_signal)

        if code_candidates:
            # merge: code_candidates 우선, 중복 제거
            seen_ids = {c.company_id for c in code_candidates}
            for cc in cat_candidates:
                if cc.company_id not in seen_ids:
                    code_candidates.append(cc)
                    seen_ids.add(cc.company_id)
            result = code_candidates
        else:
            result = cat_candidates
    else:
        result = code_candidates

    _populate_equipment_summary(result)
    return result


def _check_process_achievability(
    proc: str, claimed_it: int | None, claimed_ra: float | None,
    override: dict | None = None,
) -> list[str]:
    """업체 장비가 주장하는 IT/Ra가 해당 공정에서 이론적으로 달성 가능한 범위인지 교차 검증.

    룩업 테이블의 PROCESS_ACHIEVABLE_TOLERANCE 데이터를 참조한다.
    override가 있으면(비금속 재질 등) 해당 값을 기준으로 사용한다.
    Returns: 의심 사유 문자열 리스트 (비어 있으면 정상)
    """
    warnings: list[str] = []

    if override:
        # override 기준: typical 범위의 min을 precision 하한 대용으로 사용
        if claimed_it is not None and override.get("typical_it_min") is not None:
            if claimed_it < override["typical_it_min"]:
                warnings.append(
                    f"[공정 달성범위 의심·재질override] {proc}: 업체 주장 IT{claimed_it} < "
                    f"재질 override 하한 IT{override['typical_it_min']}"
                )
        if claimed_ra is not None and override.get("typical_ra_min") is not None:
            if claimed_ra < override["typical_ra_min"]:
                warnings.append(
                    f"[공정 달성범위 의심·재질override] {proc}: 업체 주장 Ra {claimed_ra}µm < "
                    f"재질 override 하한 {override['typical_ra_min']}µm"
                )
        return warnings

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
    proc: str, eq_it: int | None, eq_ra: float | None,
    override: dict | None = None,
) -> tuple[bool, str]:
    """중간 공정의 장비 IT/Ra가 해당 공정의 typical 범위 안인지 확인.

    override가 있으면(비금속 재질 등) override의 typical 범위를 기준으로 사용한다.
    Returns: (범위내 여부, 설명 문자열)
    """
    if override:
        # override 기준으로 검증
        if eq_it is not None and override.get("typical_it_max") is not None:
            if eq_it > override["typical_it_max"]:
                return False, f"{proc} 장비 IT{eq_it} > 재질override typical 상한 IT{override['typical_it_max']}"
        if eq_ra is not None and override.get("typical_ra_max") is not None:
            if eq_ra > override["typical_ra_max"]:
                return False, f"{proc} 장비 Ra {eq_ra}µm > 재질override typical 상한 {override['typical_ra_max']}µm"
        it_str = f"IT{eq_it}" if eq_it is not None else "IT?"
        return True, f"{proc} {it_str} (재질override 중간 공정 범위 내)"

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
            # 재질별 override 조회 (비금속 재질의 완화된 IT/Ra 기준)
            proc_override = _get_override_tolerance(
                part.category_code, part.material_code, proc
            )

            # 부모 fallback은 SAFE_PARENT_FALLBACK 화이트리스트(turning_rough/finish, milling_rough/finish)에
            # 한해서만 허용. gear_grinding·honing·lapping 등 grinding 가족 자식은 부모(general grinder)와
            # 장비 자체가 다르므로 부모 fallback이 false positive를 일으킨다.
            allow_parent_fallback = proc in SAFE_PARENT_FALLBACK
            parent_clause = (
                " OR epc.process_code = ("
                "SELECT parent_process_code FROM imma.process_catalog "
                "WHERE process_code = %s)"
                if allow_parent_fallback else ""
            )
            sql = f"""SELECT epc.best_achievable_it_grade, epc.best_ra_um,
                              (epc.process_code = %s) AS is_self_match,
                              (epc.process_code IN (
                                  SELECT process_code FROM imma.process_catalog
                                  WHERE parent_process_code = %s)) AS is_child_match
                       FROM imma.equipment_process_capabilities epc
                       JOIN imma.equipment e ON e.equipment_id = epc.equipment_id
                       WHERE e.company_id = %s::uuid
                         AND (epc.process_code = %s
                              OR epc.process_code IN (
                                  SELECT process_code FROM imma.process_catalog
                                  WHERE parent_process_code = %s){parent_clause})
                         AND e.status IN ('running', 'idle')"""
            params = [proc, proc, cand.company_id, proc, proc]
            if allow_parent_fallback:
                params.append(proc)
            rows = db.execute_query(sql, tuple(params))
            if not rows:
                if proc in FAIL_OPEN_PROCESSES:
                    continue
                cand.match_reasons.append(
                    f"[WARN_EQUIPMENT_CAPABILITY_MISSING] {proc}: "
                    f"업체 {cand.company_name}가 {proc} 공정 역량으로 1차 통과했지만 "
                    f"활성 장비의 equipment_process_capabilities row가 없습니다. "
                    f"장비 모델/외주 여부/공정별 IT·Ra 검증 필요"
                )
                cand.equipment_verified_warning = True
                continue

            # 부모 EPC fallback 신호 — 자기/자식 직접 매칭 모두 부재하고 부모로만 매칭된 경우
            if allow_parent_fallback and not any(
                r.get("is_self_match") or r.get("is_child_match") for r in rows
            ):
                parent_proc = _PROCESS_PARENT.get(proc)
                cand.match_reasons.append(
                    f"[INFO_PARENT_FALLBACK] {proc}: 업체가 부모 공정 "
                    f"{parent_proc or '?'} EPC로 등록됨 (직접 자식 EPC 부재, 동일 장비로 수행 가능)"
                )

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

            # 공정별 달성 가능 범위 교차 검증 (override 전달)
            achievability_warnings = _check_process_achievability(
                proc, best_eq_it, best_eq_ra, override=proc_override
            )
            if achievability_warnings:
                cand.match_reasons.extend(achievability_warnings)

        if failed:
            continue

        # ── 중간 공정 검증 ──
        best_intermediate_it = None
        best_intermediate_ra = None

        for proc in intermediate_procs:
            # 재질별 override 조회 (비금속 재질의 완화된 IT/Ra 기준)
            proc_override = _get_override_tolerance(
                part.category_code, part.material_code, proc
            )

            # 부모 fallback 화이트리스트 적용 (precision 루프와 동일 정책)
            allow_parent_fallback = proc in SAFE_PARENT_FALLBACK
            parent_clause = (
                " OR epc.process_code = ("
                "SELECT parent_process_code FROM imma.process_catalog "
                "WHERE process_code = %s)"
                if allow_parent_fallback else ""
            )
            sql = f"""SELECT epc.best_achievable_it_grade, epc.best_ra_um,
                              (epc.process_code = %s) AS is_self_match,
                              (epc.process_code IN (
                                  SELECT process_code FROM imma.process_catalog
                                  WHERE parent_process_code = %s)) AS is_child_match
                       FROM imma.equipment_process_capabilities epc
                       JOIN imma.equipment e ON e.equipment_id = epc.equipment_id
                       WHERE e.company_id = %s::uuid
                         AND (epc.process_code = %s
                              OR epc.process_code IN (
                                  SELECT process_code FROM imma.process_catalog
                                  WHERE parent_process_code = %s){parent_clause})
                         AND e.status IN ('running', 'idle')"""
            params = [proc, proc, cand.company_id, proc, proc]
            if allow_parent_fallback:
                params.append(proc)
            rows = db.execute_query(sql, tuple(params))
            if not rows:
                if proc in FAIL_OPEN_PROCESSES:
                    continue
                cand.match_reasons.append(
                    f"[WARN_EQUIPMENT_CAPABILITY_MISSING] {proc}: "
                    f"업체 {cand.company_name}가 {proc} 공정 역량으로 1차 통과했지만 "
                    f"활성 장비의 equipment_process_capabilities row가 없습니다. "
                    f"장비 모델/외주 여부/공정별 IT·Ra 검증 필요"
                )
                cand.equipment_verified_warning = True
                continue

            # 부모 EPC fallback 신호 — 자기/자식 직접 매칭 모두 부재하고 부모로만 매칭된 경우
            if allow_parent_fallback and not any(
                r.get("is_self_match") or r.get("is_child_match") for r in rows
            ):
                parent_proc = _PROCESS_PARENT.get(proc)
                cand.match_reasons.append(
                    f"[INFO_PARENT_FALLBACK] {proc}: 업체가 부모 공정 "
                    f"{parent_proc or '?'} EPC로 등록됨 (직접 자식 EPC 부재, 동일 장비로 수행 가능)"
                )

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
                # 정밀 공정이 있으면 중간 공정은 typical 범위 내이면 OK (override 전달)
                in_range, reason = _is_within_process_typical_range(
                    proc, best_eq_it, best_eq_ra, override=proc_override
                )
                if not in_range:
                    cand.equipment_verified = False
                    cand.match_reasons.append(f"{reason} (중간 공정 범위 초과)")
                    failed = True
                    break
                else:
                    cand.match_reasons.append(reason)
            else:
                # 정밀 공정이 없으면 중간 공정의 best IT/Ra를 추적 (override 전달)
                in_range, reason = _is_within_process_typical_range(
                    proc, best_eq_it, best_eq_ra, override=proc_override
                )
                cand.match_reasons.append(reason)
                if best_eq_it is not None:
                    best_intermediate_it = min(best_intermediate_it, best_eq_it) if best_intermediate_it is not None else best_eq_it
                if best_eq_ra is not None:
                    best_intermediate_ra = min(best_intermediate_ra, best_eq_ra) if best_intermediate_ra is not None else best_eq_ra

            # 공정별 달성 가능 범위 교차 검증 (override 전달)
            achievability_warnings = _check_process_achievability(
                proc, best_eq_it, best_eq_ra, override=proc_override
            )
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


# ══════════════════════════════════════════════════════════════════════════════
# ★ 공정 순서 검증 (온톨로지 계층 1 Step 1-2)
# ══════════════════════════════════════════════════════════════════════════════

# ── 룩업 JSON에서 PROCESS_SEQUENCE_CONSTRAINTS 로드 ──
_SEQUENCE_RULES: list[dict] = get_table("PROCESS_SEQUENCE_CONSTRAINTS")


def _parse_post_treatment(raw: str | None) -> list[str]:
    """post_treatment 원문을 파이프라인 process_code 리스트로 파싱한다.

    VLM이 추출하는 post_treatment 형태:
      "열처리", "도금", "열처리 후 연삭", "HRC 45-50", "크롬도금" 등

    Returns: 파이프라인 process_code 리스트 (순서 유지)
    """
    if not raw:
        return []

    text = raw.strip().lower()
    result: list[str] = []

    # 키워드 → process_code 매핑
    _PT_MAP = {
        "열처리": "heat_treatment",
        "담금질": "heat_treatment",
        "뜨임": "heat_treatment",
        "템퍼링": "heat_treatment",
        "침탄": "heat_treatment",
        "질화": "heat_treatment",
        "소입": "heat_treatment",
        "소둔": "heat_treatment",
        "풀림": "heat_treatment",
        "hrc": "heat_treatment",
        "도금": "surface_treatment",
        "도장": "surface_treatment",
        "크롬": "surface_treatment",
        "아노다이징": "surface_treatment",
        "산화": "surface_treatment",
        "코팅": "surface_treatment",
        "용접": "welding",
        "연삭": "grinding",
    }

    for keyword, code in _PT_MAP.items():
        if keyword in text and code not in result:
            result.append(code)

    return result


def check_process_sequence(part: ResolvedPart) -> list[str]:
    """required_processes + post_treatment의 공정 순서를 검증한다.

    PROCESS_SEQUENCE_CONSTRAINTS 룩업 테이블 참조.
    Returns: 경고 문자열 리스트
    """
    warnings: list[str] = []
    if not part.required_processes:
        return warnings

    # 공정 목록 구성: required_processes + post_treatment 파싱 결과 (중복 제거)
    full_sequence = list(part.required_processes)
    post_procs = _parse_post_treatment(part.post_treatment)
    for proc in post_procs:
        if proc not in full_sequence:
            full_sequence.append(proc)

    # 각 공정의 인덱스를 기록 (순서 비교용)
    proc_index: dict[str, int] = {}
    for i, p in enumerate(full_sequence):
        if p not in proc_index:
            proc_index[p] = i  # 첫 등장 위치

    # 파이프라인 코드 집합
    proc_set = set(full_sequence)

    for rule in _SEQUENCE_RULES:
        rule_type = rule.get("rule_type", "")
        rule_id = rule.get("rule_id", "")

        # stock_preparation 선행 규칙 → 스킵 (암묵적 전처리)
        if rule.get("predecessor_process") == "stock_preparation":
            continue

        # applies_to에 welded 조건 → welding이 공정 목록에 없으면 스킵
        applies_to = rule.get("applies_to", [])
        if any("welded" in str(a).lower() for a in applies_to):
            if "welding" not in proc_set:
                continue

        # applies_to에 tool_steel/high_precision 조건 → 조건 불충족 시 스킵
        if any(a in ("tool_steel", "high_precision_hardened_parts", "large_asymmetric_parts") for a in applies_to):
            is_tool_steel = (getattr(part, "category_code", None) == "tool_steel")
            is_high_precision = (getattr(part, "tightest_it", None) is not None and part.tightest_it <= 6)
            is_large = False  # 크기 판단은 향후 확장
            if not (is_tool_steel or is_high_precision or is_large):
                continue

        if rule_type == "absolute_rule":
            pred_stage = rule.get("predecessor_process", "")
            succ_stage = rule.get("successor_process", "")

            pred_codes = STAGE_TO_CODES.get(pred_stage, set())
            succ_codes = STAGE_TO_CODES.get(succ_stage, set())

            # predecessor 또는 successor stage가 빈 set이면 공정 매핑 자체가 없으므로 스킵
            # (예: electrically_conductive_workpiece는 재질 조건이지 공정이 아님)
            if not pred_codes or not succ_codes:
                continue

            # successor가 공정 목록에 존재하는지
            succ_present = proc_set & succ_codes
            pred_present = proc_set & pred_codes

            if succ_present and not pred_present:
                # predecessor 없이 successor가 있음 → 위반
                warnings.append(
                    f"[공정순서 위반] {rule_id}: "
                    f"{succ_stage} 공정이 있으나 선행 필수 공정 {pred_stage}이 부재. "
                    f"({rule.get('rationale', '')[:80]})"
                )

            # 둘 다 있는 경우: 순서 확인
            if succ_present and pred_present:
                pred_idx = min(proc_index.get(p, 999) for p in pred_present)
                succ_idx = min(proc_index.get(p, 999) for p in succ_present)
                if pred_idx > succ_idx:
                    warnings.append(
                        f"[공정순서 위반] {rule_id}: "
                        f"{pred_stage}({pred_present})이 "
                        f"{succ_stage}({succ_present})보다 뒤에 위치. "
                        f"({rule.get('rationale', '')[:80]})"
                    )

        elif rule_type == "recommended" and "sequence" in rule:
            seq = rule["sequence"]
            # 권장 순서의 공정들 중 파이프라인에 존재하는 것만 추출
            present_in_order = []
            for stage_name in seq:
                codes = STAGE_TO_CODES.get(stage_name, set())
                matched = proc_set & codes
                if matched:
                    # 가장 이른 인덱스
                    earliest = min(proc_index.get(p, 999) for p in matched)
                    present_in_order.append((stage_name, earliest))

            # 2개 이상 매칭된 경우에만 순서 확인
            if len(present_in_order) >= 2:
                for j in range(len(present_in_order) - 1):
                    if present_in_order[j][1] > present_in_order[j + 1][1]:
                        warnings.append(
                            f"[공정순서 권장위반] {rule_id}: "
                            f"권장 순서 {present_in_order[j][0]} → "
                            f"{present_in_order[j+1][0]}이 역전됨. "
                            f"({rule.get('rationale', '')[:80]})"
                        )
                        break  # 첫 위반만 보고

        elif rule_type == "recommended" and "predecessor_process" in rule:
            pred_stage = rule.get("predecessor_process", "")
            succ_stage = rule.get("successor_process", "")
            pred_codes = STAGE_TO_CODES.get(pred_stage, set())
            succ_codes = STAGE_TO_CODES.get(succ_stage, set())
            succ_present = proc_set & succ_codes
            pred_present = proc_set & pred_codes

            if succ_present and pred_present:
                pred_idx = min(proc_index.get(p, 999) for p in pred_present)
                succ_idx = min(proc_index.get(p, 999) for p in succ_present)
                if pred_idx > succ_idx:
                    warnings.append(
                        f"[공정순서 권장위반] {rule_id}: "
                        f"권장: {pred_stage} → {succ_stage}이나 역전됨. "
                        f"({rule.get('rationale', '')[:80]})"
                    )

        elif rule_type == "cannot_run_concurrently":
            # 공정 목록 수준에서는 검증 부적합 (피처-레벨 스케줄링 영역)
            # 순서대로 수행하면 문제없으므로 경고 비활성화
            pass

    return warnings
