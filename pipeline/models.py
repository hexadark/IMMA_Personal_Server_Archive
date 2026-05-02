"""IMMA Phase 1 매칭 파이프라인 — 데이터 클래스 정의."""

from dataclasses import dataclass, field


@dataclass
class VlmPart:
    """VLM JSON에서 파싱된 원시 부품 정보. DB 해소 전 상태."""

    part_no: int
    part_name: str
    material_raw_text: str
    material_type: str | None
    material_category: str | None
    quantity: int
    required_processes: list[str]
    max_envelope_mm: dict | None
    dimensions: list[dict]
    tolerances: list[dict]
    gdt: list[dict]
    surface_roughness: list[dict]
    post_treatment: str | None
    tightest_it: int | None = None
    finest_ra: float | None = None
    envelope_diameter: float | None = None
    envelope_length: float | None = None
    envelope_width: float | None = None
    envelope_height: float | None = None
    material_source: str = "vlm"          # "vlm" | "client_input"


@dataclass
class ResolvedPart:
    """재질 해소·일반공차 fallback·형상 분류가 완료된 부품 정보."""

    part_no: int
    part_name: str
    material_id: str | None          # uuid 문자열
    material_code: str | None
    category_code: str | None
    material_match_type: str | None   # "code" | "alias" | "category"
    quantity: int
    required_processes: list[str]
    tightest_it: int | None
    finest_ra: float | None
    envelope_diameter: float | None
    envelope_length: float | None
    envelope_width: float | None
    envelope_height: float | None
    shape_type: str                   # "turning" | "prismatic"
    general_tolerance_it: int | None
    post_treatment: str | None
    material_source: str = "vlm"     # "vlm" | "client_input"
    is_valid: bool = True
    missing_fields: list[str] = field(default_factory=list)


@dataclass
class MatchCandidate:
    """하드필터를 통과한 매칭 후보 업체."""

    company_id: str
    company_name: str
    material_codes: list[str] = field(default_factory=list)
    material_category_codes: list[str] = field(default_factory=list)
    process_codes: list[str] = field(default_factory=list)
    best_it_grade: int | None = None
    best_ra_um: float | None = None
    max_turning_diameter_mm: float | None = None
    max_turning_length_mm: float | None = None
    max_x_mm: float | None = None
    max_y_mm: float | None = None
    max_z_mm: float | None = None
    overall_status: str | None = None
    avg_rating_overall: float | None = None
    review_count: int = 0
    next_available_date: str | None = None
    material_match_type: str | None = None   # "code" | "category"
    match_reasons: list[str] = field(default_factory=list)
    equipment_verified: bool = True


@dataclass
class MatchResponse:
    """최종 매칭 응답 구조체."""

    rfq_id: str | None
    drawing_no: str
    delivery_date: str | None
    parts: list[dict] = field(default_factory=list)
    client_notes: dict | None = None
