"""IMMA Phase 1 매칭 파이프라인 — [4] 재질 해소, 일반공차 fallback, 형상 분류.

VlmPart를 ResolvedPart로 변환하는 단계.
재질 alias 2단계 해소, 일반공차 fallback, 축물/각형물 분기를 수행한다.
"""

import logging
import re

import db

logger = logging.getLogger(__name__)
from models import VlmPart, ResolvedPart
from lookup import (
    get_table, parse_it,
    PROC_NORMALIZE, PRECISION_PROCESSES, PROCESS_TO_LOOKUP_PROCESSES,
    CATEGORY_TEXT_TO_CODE,
)


# ── 룩업 JSON에서 KS_B_ISO_2768_1 로드하여 등급별 허용값 테이블 구성 ──
# {grade: [{over, up_to, plus, minus}, ...]}
_GENERAL_TOL_TABLE: dict[str, list[dict]] = {}

for _entry in get_table("KS_B_ISO_2768_1"):
    _grade = _entry.get("grade", "").lower()
    if not _grade:
        continue
    _tol = _entry.get("tolerance_mm")
    if _tol is None:
        continue
    _range = _entry.get("nominal_range_mm", {})
    _over = _range.get("over")
    _up_to = _range.get("up_to")
    if _over is None or _up_to is None:
        continue
    _GENERAL_TOL_TABLE.setdefault(_grade, []).append({
        "over": _over,
        "up_to": _up_to,
        "plus": _tol.get("plus", 0),
        "minus": _tol.get("minus", 0),
    })

# 등급별 대표 치수 구간(>30~120mm)에서의 허용 편차 폭 → ISO 286 IT 등급 추정
# ISO 286 IT 폭(50mm 기준): IT7=0.025, IT8=0.039, IT9=0.062, IT10=0.1,
#   IT11=0.16, IT12=0.25, IT13=0.39, IT14=0.62, IT15=1.0, IT16=1.6, IT17=2.5
_IT_WIDTHS_50MM = [
    (7, 0.025), (8, 0.039), (9, 0.062), (10, 0.1), (11, 0.16),
    (12, 0.25), (13, 0.39), (14, 0.62), (15, 1.0), (16, 1.6), (17, 2.5),
]


def _estimate_it_from_tolerance(grade: str) -> int | None:
    """룩업 테이블의 대표 구간(>30~120mm)에서 허용 편차 폭을 읽어 IT 등급을 추정한다."""
    entries = _GENERAL_TOL_TABLE.get(grade, [])
    # >30~120mm 범위의 항목 탐색
    tol_width = None
    for e in entries:
        if e["over"] >= 30 and e["up_to"] <= 120:
            tol_width = e["plus"] - e["minus"]
            break
    # 못 찾으면 >6~30 구간으로 fallback
    if tol_width is None:
        for e in entries:
            if e["over"] >= 6 and e["up_to"] <= 30:
                tol_width = e["plus"] - e["minus"]
                break
    if tol_width is None:
        return None

    # 가장 가까운 IT 등급 찾기
    best_it = None
    best_diff = float("inf")
    for it_grade, it_width in _IT_WIDTHS_50MM:
        diff = abs(tol_width - it_width)
        if diff < best_diff:
            best_diff = diff
            best_it = it_grade
    return best_it


# 등급별 대표 IT (룩업 기반 추정, 실패 시 하드코딩 fallback)
_GENERAL_TOL_GRADE_MAP: dict[str, int] = {}
for _g in ("f", "m", "c", "v"):
    _estimated = _estimate_it_from_tolerance(_g)
    if _estimated is not None:
        _GENERAL_TOL_GRADE_MAP[_g] = _estimated

# 추정 실패 시 하드코딩 fallback
_GENERAL_TOL_GRADE_MAP.setdefault("f", 12)
_GENERAL_TOL_GRADE_MAP.setdefault("m", 14)
_GENERAL_TOL_GRADE_MAP.setdefault("c", 16)
_GENERAL_TOL_GRADE_MAP.setdefault("v", 17)


# ── 룩업 JSON에서 MATERIAL_PROCESS_COMPATIBILITY 로드 ──
# {material_group: {process: {"compatibility": str, "machinability": str, "notes": str}}}
_MATERIAL_COMPAT: dict[str, dict[str, dict]] = {}

for _entry in get_table("MATERIAL_PROCESS_COMPATIBILITY"):
    _mg = _entry.get("material_group", "")
    _proc = _entry.get("process", "")
    if _mg and _proc:
        _MATERIAL_COMPAT.setdefault(_mg, {})[_proc] = {
            "compatibility": _entry.get("compatibility", ""),
            "machinability": _entry.get("machinability", ""),
            "notes": _entry.get("notes", ""),
        }



# ── 룩업 JSON에서 PROCESS_ACHIEVABLE_TOLERANCE 로드 (도면 피드백용) ──


_PROCESS_TOLERANCE_FOR_FEEDBACK: dict[str, dict] = {}

for _entry in get_table("PROCESS_ACHIEVABLE_TOLERANCE"):
    _proc = _entry.get("process", "")
    _normalized = PROC_NORMALIZE.get(_proc, _proc)
    _ait = _entry.get("achievable_IT", {})
    _PROCESS_TOLERANCE_FOR_FEEDBACK[_normalized] = {
        "precision_it_min": parse_it(_ait.get("precision", {}).get("min")),
        "precision_it_max": parse_it(_ait.get("precision", {}).get("max")),
    }




def resolve_material(raw_text: str) -> tuple[str | None, str | None, str | None, str | None]:
    """재질 원문을 DB의 재질 마스터와 대조하여 해소한다.

    Step 1: materials 테이블에서 material_code 직접 매칭
    Step 1-b: material_aliases에서 alias_text 매칭 → material_id → material_code
    Step 2: material_category_catalog에서 category_name_ko LIKE 매칭

    Returns:
        (material_id, material_code, category_code, match_type)
        match_type: "code" | "alias" | "category" | None
    """
    if not raw_text or not raw_text.strip():
        return (None, None, None, None)

    clean = raw_text.strip()

    # LIKE 패턴에서 특수문자 이스케이프 (Step 2에서 사용)
    clean_escaped = clean.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    # Step 1: material_code 직접 매칭
    rows = db.execute_query(
        """SELECT material_id, material_code, category_code
           FROM imma.materials
           WHERE material_code = %s AND is_active = true""",
        (clean,),
    )
    if rows:
        r = rows[0]
        return (str(r["material_id"]), r["material_code"], r["category_code"], "code")

    # Step 1-b: alias 매칭
    rows = db.execute_query(
        """SELECT m.material_id, m.material_code, m.category_code
           FROM imma.material_aliases a
           JOIN imma.materials m ON m.material_id = a.material_id
           WHERE a.alias_text = %s AND m.is_active = true""",
        (clean,),
    )
    if rows:
        r = rows[0]
        return (str(r["material_id"]), r["material_code"], r["category_code"], "alias")

    # Step 1-c: 규격 접미사를 제거한 코드로 재시도 (예: "СЧ 18-36 ГОСТ 1412-85" → "СЧ 18-36")
    code_only = re.split(r'\s+(ГОСТ|ГОСТ\s|KS|JIS|ASTM|AISI|DIN|EN|BS|ISO)\b', clean)[0].strip()
    if code_only != clean:
        rows = db.execute_query(
            """SELECT material_id, material_code, category_code
               FROM imma.materials
               WHERE material_code = %s AND is_active = true""",
            (code_only,),
        )
        if rows:
            r = rows[0]
            return (str(r["material_id"]), r["material_code"], r["category_code"], "code")
        rows = db.execute_query(
            """SELECT m.material_id, m.material_code, m.category_code
               FROM imma.material_aliases a
               JOIN imma.materials m ON m.material_id = a.material_id
               WHERE a.alias_text = %s AND m.is_active = true""",
            (code_only,),
        )
        if rows:
            r = rows[0]
            return (str(r["material_id"]), r["material_code"], r["category_code"], "alias")

    # Step 1-c-2: 템퍼 접미사 제거 (A6061-T6 → A6061)
    temper_stripped = re.sub(r'[-\s]?[TtHhOoFf]\d{1,4}$', '', clean).strip()
    if temper_stripped and temper_stripped != clean:
        rows = db.execute_query(
            """SELECT material_id, material_code, category_code
               FROM imma.materials
               WHERE material_code = %s AND is_active = true""",
            (temper_stripped,),
        )
        if rows:
            r = rows[0]
            return (str(r["material_id"]), r["material_code"], r["category_code"], "code")
        rows = db.execute_query(
            """SELECT m.material_id, m.material_code, m.category_code
               FROM imma.material_aliases a
               JOIN imma.materials m ON m.material_id = a.material_id
               WHERE a.alias_text = %s AND m.is_active = true""",
            (temper_stripped,),
        )
        if rows:
            r = rows[0]
            return (str(r["material_id"]), r["material_code"], r["category_code"], "alias")

    # Step 1-d: CATEGORY_TEXT_TO_CODE 딕셔너리 직접 lookup
    cat_code = CATEGORY_TEXT_TO_CODE.get(clean)
    if cat_code is not None:
        return (None, None, cat_code, "category")

    # Step 2: 카테고리 이름 LIKE 매칭 (특수문자 이스케이프 적용)
    rows = db.execute_query(
        """SELECT category_code
           FROM imma.material_category_catalog
           WHERE category_name_ko LIKE %s ESCAPE '\\' AND is_active = true""",
        (f"%{clean_escaped}%",),
    )
    if rows:
        return (None, None, rows[0]["category_code"], "category")

    # Step 3: 패턴 기반 카테고리 추정
    # AISI 쾌삭강 (12L14, 1213, 1215, 1144 등) → free_cutting_steel
    if re.match(r'^12L\d+', clean) or clean in ("1213", "1215"):
        return (None, None, "free_cutting_steel", "category")
    if re.match(r'^11[34]\d$', clean):  # 1144 등 중탄소쾌삭강 계열
        return (None, None, "free_cutting_steel", "category")
    # AISI 10xx/11xx 일반 탄소강 (1010, 1020, 1045 등)
    if re.match(r'^1[01]\d{2}$', clean):
        return (None, None, "carbon_steel", "category")
    # DIN/인도 합금강 (20MnCr, 20Mn.Cr. 등) → alloy_steel
    if re.match(r'^\d+Mn', clean, re.IGNORECASE):
        return (None, None, "alloy_steel", "category")
    # 러시아 회주철 (СЧ 18-36, СЧ 25 등) → gray_cast_iron
    if re.match(r'^СЧ\s*\d', clean):
        return (None, None, "gray_cast_iron", "category")

    logger.debug("재질 해소 실패: '%s'", raw_text)
    return (None, None, None, None)


def resolve_general_tolerance(standards: list) -> int | None:
    """referenced_standards에서 일반공차 IT 등급을 추출한다.

    "KS B ISO 2768-m" → 등급 "m" → 룩업 테이블 기반 IT 추정 (e.g. IT14)
    IT 추정은 룩업 JSON의 KS_B_ISO_2768_1 데이터에서 대표 구간의 허용 편차 폭을
    ISO 286 IT 폭과 비교하여 수행한다.
    """
    for std in standards:
        text = std if isinstance(std, str) else str(std)
        if "2768" in text:
            match = re.search(r'2768[-\s]*([fmcvFMCV])', text)
            if match:
                grade_char = match.group(1).lower()
                return _GENERAL_TOL_GRADE_MAP.get(grade_char)
    return None


def get_general_tolerance_values(grade: str, nominal_mm: float | None = None) -> dict | None:
    """룩업 테이블에서 지정 등급/치수 구간의 실제 허용값을 반환한다.

    Args:
        grade: 등급 문자 (f/m/c/v)
        nominal_mm: 공칭 치수(mm). None이면 대표 구간(>30~120) 반환.

    Returns:
        {"plus": float, "minus": float, "over": float, "up_to": float} 또는 None
    """
    entries = _GENERAL_TOL_TABLE.get(grade.lower(), [])
    if not entries:
        return None

    if nominal_mm is not None:
        for e in entries:
            if e["over"] < nominal_mm <= e["up_to"]:
                return e
        return None

    # 대표 구간: >30~120
    for e in entries:
        if e["over"] >= 30 and e["up_to"] <= 120:
            return e
    return entries[0] if entries else None


def determine_shape_type(processes: list[str], diameter: float | None) -> str:
    """공정 목록과 외경 유무로 축물/각형물을 분류한다.

    turning 계열(turning, turning_rough, turning_finish)이 포함되고
    diameter가 존재하면 "turning", 아니면 "prismatic".
    """
    TURNING_FAMILY = {"turning", "turning_rough", "turning_finish"}
    if TURNING_FAMILY & set(processes) and diameter is not None:
        return "turning"
    return "prismatic"


def check_mandatory_fields(part: VlmPart) -> tuple[bool, list[str]]:
    """매칭에 필수인 필드가 채워져 있는지 검증한다.

    필수: material_raw_text (또는 material_category), required_processes, quantity (> 0)
    Returns: (is_valid, missing_fields)
    """
    missing = []
    if not (part.material_raw_text or "").strip() and not (part.material_category or "").strip():
        missing.append("material")
    if not part.required_processes:
        missing.append("required_processes")
    if part.quantity is None or part.quantity <= 0:
        missing.append("quantity")
    return (len(missing) == 0, missing)


def _check_material_process_compatibility(
    category_code: str | None,
    required_processes: list[str],
) -> list[str]:
    """재질 카테고리와 필수 공정 목록의 호환성을 검증한다.

    MATERIAL_PROCESS_COMPATIBILITY 룩업 테이블 참조.
    Returns: 경고 문자열 리스트 (비어 있으면 문제 없음)
    """
    warnings: list[str] = []
    if not category_code or not required_processes:
        return warnings

    mat_data = _MATERIAL_COMPAT.get(category_code)
    if mat_data is None:
        # 해당 재질 그룹의 호환성 데이터 없음 — 검증 불가, 경고 없이 통과
        return warnings

    for proc in required_processes:
        # 파이프라인 process_code → 룩업 process(들)로 변환
        lookup_procs = PROCESS_TO_LOOKUP_PROCESSES.get(proc, [proc])

        for lp in lookup_procs:
            info = mat_data.get(lp)
            if info is None:
                # 해당 공정의 호환성 데이터 없음 (비가공 공정 등) — 스킵
                continue

            compat = info["compatibility"]
            if compat == "unsuitable":
                warnings.append(
                    f"[재질-공정 부적합] {category_code} + {lp}: "
                    f"이 재질에 이 공정은 적용 불가 (unsuitable). "
                    f"{info['notes'][:80]}"
                )
            elif compat == "limited":
                warnings.append(
                    f"[재질-공정 호환성 주의] {category_code} + {lp}: "
                    f"compatibility={compat}, 가공성={info['machinability']}. "
                    f"{info['notes'][:80]}"
                )

    return warnings


def _check_drawing_feedback(
    vlm_part: VlmPart,
    tightest_it: int | None,
    finest_ra: float | None,
) -> list[str]:
    """도면 품질에 대한 정보성 경고를 생성한다.

    VlmPart의 각 필드를 검사하여 모순, 누락, 비정상 조합을 탐지한다.
    Returns: 경고 문자열 리스트
    """
    warnings: list[str] = []

    # ── 규칙 1: IT 등급 ≤ 6인데 정밀 공정(grinding, honing, lapping)이 없으면 경고 ──
    if tightest_it is not None and tightest_it <= 6:
        has_precision = any(p in PRECISION_PROCESSES for p in vlm_part.required_processes)
        if not has_precision:
            warnings.append(
                f"[도면 피드백] IT{tightest_it} 요구이나 정밀 공정(연삭/호닝/래핑)이 "
                f"공정 목록에 없음. 달성 가능성을 확인하세요."
            )

    # ── 규칙 2: Ra ≤ 0.4 µm인데 lapping/honing이 없으면 경고 ──
    if finest_ra is not None and finest_ra <= 0.4:
        has_super_finish = any(p in {"honing", "lapping"} for p in vlm_part.required_processes)
        if not has_super_finish:
            warnings.append(
                f"[도면 피드백] Ra {finest_ra}µm 요구이나 초정밀 공정(호닝/래핑)이 "
                f"공정 목록에 없음. 달성 가능성을 확인하세요."
            )

    # ── 규칙 3: 외형 치수 전부 누락 ──
    has_diameter = vlm_part.envelope_diameter is not None
    has_box = (vlm_part.envelope_length is not None or
               vlm_part.envelope_width is not None or
               vlm_part.envelope_height is not None)
    if not has_diameter and not has_box:
        warnings.append(
            "[도면 피드백] 외형 치수(외경, 길이×폭×높이) 전부 미추출. "
            "크기 기반 매칭이 비활성화됩니다."
        )

    # ── 규칙 4: turning 공정이 있는데 외경이 없음 ──
    if "turning" in vlm_part.required_processes and not has_diameter:
        warnings.append(
            "[도면 피드백] turning 공정이 있으나 외경(diameter) 미추출. "
            "축물 크기 매칭이 불가합니다."
        )

    # ── 규칙 5: post_treatment가 있는데 구체적 조건이 없음 ──
    if vlm_part.post_treatment:
        pt_lower = vlm_part.post_treatment.lower()
        # "열처리"만 있고 HRC/HV 등 경도 조건이 없으면
        if ("열처리" in pt_lower or "heat" in pt_lower) and \
           not any(kw in pt_lower for kw in ["hrc", "hv", "hrb", "경도"]):
            warnings.append(
                "[도면 피드백] 후처리에 열처리가 명시되었으나 경도 조건(HRC/HV 등)이 "
                "미기재. 업체 견적 시 확인 필요."
            )

    # ── 규칙 6: GDT 항목이 있는데 IT/Ra가 모두 None ──
    if vlm_part.gdt and tightest_it is None and finest_ra is None:
        warnings.append(
            "[도면 피드백] 기하공차(GD&T) 항목이 존재하나 치수공차(IT)와 "
            "표면조도(Ra)가 모두 미추출. 도면 판독 재확인 필요."
        )

    # ── 규칙 7: 공정별 달성 가능 공차 사전 경고 ──
    # PROCESS_ACHIEVABLE_TOLERANCE 참조
    # 정밀 공정이 이미 있으면 중간/황삭 공정의 "단독 달성 불가" 경고를 억제
    has_precision_proc = bool(set(vlm_part.required_processes) & PRECISION_PROCESSES)

    _SKIP_WHEN_PRECISION_EXISTS = {"turning", "milling", "drilling", "threading",
                                   "turning_rough", "turning_finish",
                                   "milling_rough", "milling_finish"}

    if tightest_it is not None:
        for proc in vlm_part.required_processes:
            lookup_procs = PROCESS_TO_LOOKUP_PROCESSES.get(proc, [proc])
            for lp in lookup_procs:
                # 정밀 공정이 이미 있으면 중간/황삭 공정의 경고 억제
                if has_precision_proc and lp in _SKIP_WHEN_PRECISION_EXISTS:
                    continue
                ref = _PROCESS_TOLERANCE_FOR_FEEDBACK.get(lp)
                if ref is None:
                    continue
                precision_min = ref.get("precision_it_min")
                if precision_min is not None and tightest_it < precision_min:
                    warnings.append(
                        f"[공차 달성 사전경고] {lp}: 도면 요구 IT{tightest_it} < "
                        f"이론적 precision 하한 IT{precision_min}. "
                        f"해당 공정 단독으로는 달성 불가."
                    )

    return warnings


def resolve_part(vlm_part: VlmPart, standards: list) -> ResolvedPart:
    """VlmPart 하나를 ResolvedPart로 변환한다."""

    # 재질 해소
    mat_id, mat_code, cat_code, match_type = resolve_material(vlm_part.material_raw_text)

    # 카테고리 코드: materials에서 온 것이 없으면 VLM의 category 텍스트로 재시도
    if cat_code is None and vlm_part.material_category:
        cat_text = (vlm_part.material_category or "").strip()
        # CATEGORY_TEXT_TO_CODE 딕셔너리로 먼저 직접 변환 시도
        direct_cat = CATEGORY_TEXT_TO_CODE.get(cat_text)
        if direct_cat is not None:
            cat_code = direct_cat
            if match_type is None:
                match_type = "category"
        else:
            _, _, cat_code, mt = resolve_material(cat_text)
            if mt == "category" and match_type is None:
                match_type = "category"

    # IT 등급: 끼워맞춤에서 추출
    tightest_it = vlm_part.tightest_it

    # 일반공차 fallback
    general_tol_it = resolve_general_tolerance(standards)
    if tightest_it is None and general_tol_it is not None:
        tightest_it = general_tol_it

    # 형상 분류
    shape = determine_shape_type(vlm_part.required_processes, vlm_part.envelope_diameter)

    # 필수 필드 검증
    is_valid, missing = check_mandatory_fields(vlm_part)

    # ★ 재질-공정 호환성 검증
    ontology_warnings = _check_material_process_compatibility(
        cat_code, vlm_part.required_processes
    )

    # ★ 도면 피드백 확장
    drawing_warnings = _check_drawing_feedback(
        vlm_part, tightest_it, vlm_part.finest_ra
    )
    ontology_warnings.extend(drawing_warnings)

    return ResolvedPart(
        part_no=vlm_part.part_no,
        part_name=vlm_part.part_name,
        material_id=mat_id,
        material_code=mat_code,
        category_code=cat_code,
        material_match_type=match_type,
        material_source=vlm_part.material_source,
        quantity=vlm_part.quantity,
        required_processes=vlm_part.required_processes,
        tightest_it=tightest_it,
        finest_ra=vlm_part.finest_ra,
        envelope_diameter=vlm_part.envelope_diameter,
        envelope_length=vlm_part.envelope_length,
        envelope_width=vlm_part.envelope_width,
        envelope_height=vlm_part.envelope_height,
        shape_type=shape,
        general_tolerance_it=general_tol_it,
        post_treatment=vlm_part.post_treatment,
        is_valid=is_valid,
        missing_fields=missing,
        ontology_warnings=ontology_warnings,
    )
