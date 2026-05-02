"""IMMA Phase 1 매칭 파이프라인 — [3] VLM JSON 파싱.

순수 변환 단계. DB를 건드리지 않으며, VLM이 출력한 raw JSON을
VlmPart 데이터 클래스 리스트로 정규화한다.
"""

import logging
import re

from models import VlmPart

logger = logging.getLogger(__name__)


def parse_vlm_json(raw_json: dict) -> list[VlmPart]:
    """VLM 출력 JSON 전체를 받아 parts 배열을 VlmPart 리스트로 변환한다."""
    parts = []
    for idx, p in enumerate(raw_json.get("parts", [])):
        mat = p.get("material", {})
        dims = p.get("dimensions", [])
        tols = p.get("tolerances", [])
        sr = p.get("surface_roughness", [])
        envelope = p.get("max_envelope_mm")

        tightest_it = extract_tightest_it(tols)
        finest_ra = extract_finest_ra(sr)
        envelope_diameter = extract_envelope_diameter(dims)

        envelope_length = None
        envelope_width = None
        envelope_height = None
        if envelope:
            envelope_length = envelope.get("length")
            envelope_width = envelope.get("width")
            envelope_height = envelope.get("height")

        parts.append(VlmPart(
            part_no=p.get("part_no", idx + 1),
            part_name=p.get("part_name", ""),
            material_raw_text=mat.get("raw_text", ""),
            material_type=mat.get("type"),
            material_category=mat.get("category"),
            quantity=p.get("quantity", 1),
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
    """
    # 구멍/축 단일 문자 + 숫자 1~2자리
    _FIT_SINGLE = re.compile(r'(?<![a-zA-Z])([A-HJKNPR-VX-Za-hjknpr-vx-z])(\d{1,2})(?!\d)')
    # JS/js + 숫자 1~2자리
    _FIT_JS = re.compile(r'(?<![a-zA-Z])([Jj][Ss])(\d{1,2})(?!\d)')

    grades = []
    for tol in tolerances:
        text = tol.get("text", "")
        for pattern in (_FIT_JS, _FIT_SINGLE):
            for m in pattern.finditer(text):
                val = int(m.group(2))
                if 1 <= val <= 18:
                    grades.append(val)
    return min(grades) if grades else None


def extract_finest_ra(surface_roughness: list) -> float | None:
    """표면 거칠기 리스트에서 가장 미세한(값이 작은) Ra를 추출한다.

    Ra 값이 null인 항목은 건너뛴다.
    """
    values = []
    for sr in surface_roughness:
        ra = sr.get("Ra")
        if ra is not None:
            values.append(float(ra))
    return min(values) if values else None


def extract_envelope_diameter(dimensions: list) -> float | None:
    """치수 리스트에서 type이 'diameter'인 최대 외경 값을 추출한다.

    외경이 없으면 None을 반환한다.
    """
    diameters = []
    for dim in dimensions:
        if dim.get("type") == "diameter" and dim.get("value") is not None:
            diameters.append(float(dim["value"]))
    return max(diameters) if diameters else None
