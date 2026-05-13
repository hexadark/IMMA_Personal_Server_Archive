"""IMMA Phase 1 매칭 파이프라인 — [3] VLM JSON 파싱.

순수 변환 단계. DB를 건드리지 않으며, VLM이 출력한 raw JSON을
VlmPart 데이터 클래스 리스트로 정규화한다.
"""

import logging
import re

from models import VlmPart

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# ± 치수공차 → IT 등급 환산 (ISO 286 IT width 테이블 기반)
# ══════════════════════════════════════════════════════════════════════════════

_IT_WIDTH_TABLE = None


def _get_it_width_table():
    """ISO_286_IT_WIDTH 룩업 테이블을 지연 로드하여 캐싱한다."""
    global _IT_WIDTH_TABLE
    if _IT_WIDTH_TABLE is None:
        from lookup import get_table
        _IT_WIDTH_TABLE = get_table("ISO_286_IT_WIDTH")
    return _IT_WIDTH_TABLE


def _tolerance_to_it_grade(nominal_mm: float, tolerance_width_mm: float) -> int | None:
    """nominal 치수(mm)와 공차폭(mm)으로 등가 IT 등급을 역산한다.

    해당 nominal 구간에서 tolerance_width_um >= 공차폭(µm)인 IT 등급 중
    가장 작은(가장 엄격한) 것을 반환한다.
    IT01, IT0은 실제 매칭에서 쓰이지 않으므로 스킵하고 IT1~IT18만 대상으로 한다.
    """
    table = _get_it_width_table()
    width_um = tolerance_width_mm * 1000

    # nominal에 해당하는 구간 필터
    candidates = [row for row in table
                  if row["nominal_range_mm"]["over"] < nominal_mm <= row["nominal_range_mm"]["up_to"]]

    if not candidates:
        return None

    # tolerance_width_um >= width_um인 것 중 가장 작은 IT 등급
    # IT01/IT0는 nominal ≥500mm에서 tolerance_width_um이 null이므로 비교 전에 스킵
    best = None
    for row in candidates:
        grade_str = row["it_grade"]  # "IT7" 등
        if grade_str in ("IT01", "IT0"):
            continue
        if row["tolerance_width_um"] >= width_um:
            grade_num = int(grade_str.replace("IT", ""))
            if best is None or grade_num < best:
                best = grade_num
    return best


# ± 패턴 regex
# 패턴 1: "120±0.05" 또는 "Ø45±0.02" (대칭 공차)
_PM_SYMMETRIC = re.compile(r'(\d+\.?\d*)\s*±\s*(\d+\.?\d*)')
# 패턴 2: "80 +0.03/-0.01" 또는 "80+0.03-0.01" (비대칭 공차)
_PM_ASYMMETRIC = re.compile(r'(\d+\.?\d*)\s*\+\s*(\d+\.?\d*)\s*/?\s*-\s*(\d+\.?\d*)')


def parse_vlm_json(raw_json: dict) -> list[VlmPart]:
    """VLM 출력 JSON 전체를 받아 parts 배열을 VlmPart 리스트로 변환한다."""
    parts = []
    for idx, p in enumerate(raw_json.get("parts", [])):
        mat = p.get("material") or {}
        dims = p.get("dimensions") or []
        tols = p.get("tolerances") or []
        sr = p.get("surface_roughness") or []
        envelope = p.get("max_envelope_mm")

        tightest_it = extract_tightest_it(tols)
        finest_ra = extract_finest_ra(sr)
        envelope_diameter = extract_envelope_diameter(dims)

        envelope_length = None
        envelope_width = None
        envelope_height = None
        if isinstance(envelope, dict):
            _el = envelope.get("length")
            envelope_length = float(_el) if _el is not None else None
            _ew = envelope.get("width")
            envelope_width = float(_ew) if _ew is not None else None
            _eh = envelope.get("height")
            envelope_height = float(_eh) if _eh is not None else None

        parts.append(VlmPart(
            part_no=p.get("part_no", idx + 1),
            part_name=p.get("part_name", ""),
            material_raw_text=mat.get("raw_text", ""),
            material_type=mat.get("type"),
            material_category=mat.get("category"),
            quantity=int(p.get("quantity") or 1),
            required_processes=p.get("required_processes", []),
            max_envelope_mm=envelope,
            dimensions=dims,
            tolerances=tols,
            gdt=p.get("gdt", []),
            surface_roughness=sr,
            post_treatment=p.get("post_treatment"),
            tightest_it=tightest_it,
            finest_ra=finest_ra,
            envelope_diameter=envelope_diameter,
            envelope_length=envelope_length,
            envelope_width=envelope_width,
            envelope_height=envelope_height,
            unsupported=bool(p.get("unsupported", False)),
            unsupported_reason=p.get("unsupported_reason"),
        ))
    return parts


def extract_tightest_it(tolerances: list) -> int | None:
    """공차 리스트에서 가장 엄격한(숫자가 작은) IT 등급을 추출한다.

    끼워맞춤 표기 "Ø15k5" → 숫자 부분 5가 IT 등급.
    ISO 286 끼워맞춤 문자만 인식하고 나사(M10), 치수(Ø15) 등은 제외한다.
      구멍(대문자): A-H, J, K, N, P, R-V, X-Z
      축(소문자):   a-h, j, k, n, p, r-v, x-z
      JS/js:       대소문자 2글자 + 숫자
    I/L/M/O/Q/W (대소문자) — ISO 286 미사용 또는 나사 접두어 — 제외.

    나사 표기(M10x1.5-6g, G1/2 등) 및 단독 반지름 표기(R5) 는
    끼워맞춤이 아니므로 IT 추출 대상에서 제외한다.
    """
    # 구멍/축 단일 문자 + 숫자 1~2자리
    # 'x'/'X'는 곱셈·패턴 표기("3x3", "5x10")와 충돌하여 오인식 위험이 크고,
    # ISO 286 'x' 등급 끼워맞춤은 실용 영역에서 거의 사용되지 않으므로 제외.
    _FIT_SINGLE = re.compile(r'(?<![a-zA-Z])([A-HJKNPR-VYZa-hjknpr-vyz])(\d{1,2})(?!\d)')
    # JS/js + 숫자 1~2자리
    _FIT_JS = re.compile(r'(?<![a-zA-Z])([Jj][Ss])(\d{1,2})(?!\d)')
    # 나사/관용 패턴 — 이 패턴이 text에 있으면 해당 text에서 IT 추출 스킵
    _THREAD_PATTERN = re.compile(r'(?:^|\b)(?:M\d|G\d|Rc|PT\d|NPT|UNC|UNF|BSP)', re.IGNORECASE)
    # 단독 반지름: "R5", "R10" (대문자 R + 숫자만, 단독)
    _RADIUS_PATTERN = re.compile(r'^R\d+(\.\d+)?$', re.IGNORECASE)

    grades = []
    for tol in tolerances:
        text = tol.get("text", "")
        # 나사 표기가 포함된 text는 스킵
        if _THREAD_PATTERN.search(text):
            continue
        # 단독 반지름 표기는 스킵
        if _RADIUS_PATTERN.match(text.strip()):
            continue
        # ── 기존: 끼워맞춤 표기에서 IT 추출 ──
        for pattern in (_FIT_JS, _FIT_SINGLE):
            for m in pattern.finditer(text):
                val = int(m.group(2))
                if 1 <= val <= 18:
                    grades.append(val)
        # ── 추가: ± 치수공차에서 IT 등급 역산 ──
        # 대칭 ±
        for m in _PM_SYMMETRIC.finditer(text):
            nominal = float(m.group(1))
            pm = float(m.group(2))
            width = pm * 2
            if nominal > 0 and width > 0:
                it = _tolerance_to_it_grade(nominal, width)
                if it is not None and 1 <= it <= 18:
                    grades.append(it)
        # 비대칭 +/-
        for m in _PM_ASYMMETRIC.finditer(text):
            nominal = float(m.group(1))
            plus = float(m.group(2))
            minus = float(m.group(3))
            width = plus + minus
            if nominal > 0 and width > 0:
                it = _tolerance_to_it_grade(nominal, width)
                if it is not None and 1 <= it <= 18:
                    grades.append(it)
    return min(grades) if grades else None


_TRIANGLE_RA = {"▽": 25.0, "▽▽": 6.3, "▽▽▽": 1.6, "▽▽▽▽": 0.4}


def extract_finest_ra(surface_roughness: list) -> float | None:
    """표면 거칠기 리스트에서 가장 미세한(값이 작은) Ra를 추출한다.

    Ra 값이 null인 항목은 건너뛴다.
    ▽ 기호가 Ra 값으로 들어온 경우 JIS/KS 매핑으로 변환한다.
    """
    values = []
    for sr in surface_roughness:
        ra = sr.get("Ra")
        if ra is None:
            continue
        if isinstance(ra, str) and "▽" in ra:
            mapped = _TRIANGLE_RA.get(ra.strip())
            if mapped is not None:
                values.append(mapped)
            continue
        try:
            values.append(float(ra))
        except (TypeError, ValueError):
            continue
    return min(values) if values else None


def extract_envelope_diameter(dimensions: list) -> float | None:
    """치수 리스트에서 type이 'outer_diameter'인 최대 외경 값을 추출한다.

    outer_diameter를 우선하되, 하위 호환으로 'diameter'도 허용한다.
    외경이 없으면 None을 반환한다.
    """
    diameters = []
    for dim in dimensions:
        if dim.get("type") in ("outer_diameter", "diameter") and dim.get("value") is not None:
            diameters.append(float(dim["value"]))
    return max(diameters) if diameters else None
