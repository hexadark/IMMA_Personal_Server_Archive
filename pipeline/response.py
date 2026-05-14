"""IMMA Phase 1 매칭 파이프라인 — [6] 후보 업체 목록 JSON 조립.

매칭 결과를 최종 응답 구조체로 변환한다.
"""

import logging

from models import ResolvedPart, MatchCandidate, MatchResponse
from lookup import FAIL_OPEN_PROCESSES

logger = logging.getLogger(__name__)

# 필수 필드 누락 시 거부 메시지
_MISSING_FIELD_MESSAGES: dict[str, str] = {
    "material": "재질을 입력해주세요",
    "required_processes": "필요 공정을 확인할 수 없습니다. 도면을 확인해주세요",
    "quantity": "수량을 입력해주세요",
}


def build_match_reasons(candidate: MatchCandidate, part: ResolvedPart) -> list[str]:
    """후보 업체가 선정된 이유를 사람이 읽을 수 있는 문자열 리스트로 생성한다.

    SQL 조건 통과 사실만 기술한다.
    """
    reasons = []

    # 재질 매칭
    if candidate.material_match_type == "code" and part.material_code:
        reasons.append(f"{part.material_code} 코드 매칭")
    elif candidate.material_match_type == "category" and part.category_code:
        # 후보가 요구 카테고리를 직접 보유 vs 상위 카테고리로 fallback된 경우 구분
        if part.category_code in (candidate.material_category_codes or []):
            reasons.append(f"{part.category_code} 카테고리 매칭")
        else:
            reasons.append(f"{part.category_code} 카테고리 매칭 (상위 카테고리 fallback)")

    # 공정 보유 (사내 공정과 외주 공정 분리 표시)
    if part.required_processes:
        inhouse = [p for p in part.required_processes if p not in FAIL_OPEN_PROCESSES]
        outsource = [p for p in part.required_processes if p in FAIL_OPEN_PROCESSES]
        if inhouse:
            reasons.append(f"사내 공정: {', '.join(inhouse)}")
        if outsource:
            reasons.append(f"외주 공정: {', '.join(outsource)} (업체 외주망 검토 필요)")

    # 크기 충족
    if part.shape_type == "turning":
        td = candidate.max_turning_diameter_mm
        tl = candidate.max_turning_length_mm
        if td is not None or tl is not None:
            td_s = f"Ø{td:.0f}" if td else "?"
            tl_s = f"{tl:.0f}mm" if tl else "?"
            req_d = f"Ø{part.envelope_diameter:.0f}" if part.envelope_diameter else "?"
            req_l = f"{part.envelope_length:.0f}mm" if part.envelope_length else "?"
            reasons.append(f"최대 선삭 {td_s} × {tl_s} (요구 {req_d} × {req_l} 충족)")
    else:
        mx = candidate.max_x_mm
        my = candidate.max_y_mm
        mz = candidate.max_z_mm
        if mx is not None or my is not None or mz is not None:
            cap = f"{mx or '?'}×{my or '?'}×{mz or '?'}mm"
            req = f"{part.envelope_length or '?'}×{part.envelope_width or '?'}×{part.envelope_height or '?'}mm"
            reasons.append(f"최대 밀링 {cap} (요구 {req} 충족)")

    # IT 등급
    if candidate.best_it_grade is not None:
        reasons.append(f"업체 최선 IT{candidate.best_it_grade}")

    # Ra
    if candidate.best_ra_um is not None and part.finest_ra is not None:
        reasons.append(f"업체 최선 Ra {candidate.best_ra_um}μm")

    # 장비 미검증
    if not candidate.equipment_verified:
        reasons.append("장비 수준 미검증 (IT/Ra 미달 가능)")

    return reasons


def build_match_input(part: ResolvedPart) -> dict:
    """매칭에 사용된 입력 파라미터를 요약 dict로 반환한다."""
    result = {
        "material": part.material_code or part.category_code or "미확인",
        "material_match_type": part.material_match_type,
        "material_source": part.material_source,
        "processes": part.required_processes,
        "quantity": part.quantity,
    }

    if part.shape_type == "turning":
        d_str = f"Ø{part.envelope_diameter:.0f}" if part.envelope_diameter else None
        l_str = f"{part.envelope_length:.0f}mm" if part.envelope_length else None
        if d_str and l_str:
            result["envelope"] = f"{d_str} × {l_str}"
    else:
        parts_str = []
        if part.envelope_length:
            parts_str.append(f"{part.envelope_length:.0f}")
        if part.envelope_width:
            parts_str.append(f"{part.envelope_width:.0f}")
        if part.envelope_height:
            parts_str.append(f"{part.envelope_height:.0f}")
        if parts_str:
            result["envelope"] = "×".join(parts_str) + "mm"

    if part.tightest_it is not None:
        result["tightest_it"] = part.tightest_it

    if part.finest_ra is not None:
        result["finest_ra_um"] = part.finest_ra

    if part.post_treatment:
        result["post_treatment"] = part.post_treatment

    # 미검증 표시
    warnings = []
    if part.shape_type == "turning":
        if part.envelope_diameter is None and part.envelope_length is None:
            warnings.append("크기 미검증")
    else:
        if part.envelope_length is None or part.envelope_width is None or part.envelope_height is None:
            warnings.append("크기 미검증")
    if part.tightest_it is None:
        warnings.append("공차 미검증")
    if part.finest_ra is None:
        warnings.append("조도 미검증")
    # ★ 온톨로지 검증 경고 합류
    if part.ontology_warnings:
        warnings.extend(part.ontology_warnings)
    if warnings:
        result["warnings"] = warnings

    return result


def assemble_response(
    rfq_id: str | None,
    drawing_no: str,
    delivery_date: str | None,
    parts_with_candidates: list[tuple[ResolvedPart, list[MatchCandidate]]],
    client_notes: dict | None = None,
) -> MatchResponse:
    """전체 부품-후보 쌍을 MatchResponse 구조체로 조립한다."""
    parts = []
    for resolved, candidates in parts_with_candidates:
        part_dict = {
            "rfq_part_id": None,
            "part_name": resolved.part_name,
            "match_input": build_match_input(resolved),
            "candidates": [],
        }

        if not resolved.is_valid:
            messages = [
                _MISSING_FIELD_MESSAGES.get(f, f"{f} 정보가 누락되었습니다")
                for f in resolved.missing_fields
            ]
            part_dict["status"] = "rejected"
            unsup_warnings = [w for w in resolved.ontology_warnings if w.startswith("[unsupported]")]
            if unsup_warnings:
                part_dict["rejection_reason"] = unsup_warnings[0]
            else:
                part_dict["rejection_reason"] = "필수 정보 누락"
            part_dict["missing_fields"] = resolved.missing_fields
            part_dict["message"] = "; ".join(messages)
        else:
            for cand in candidates:
                # 장비 검증 단계에서 추가된 경고를 보존한 뒤 합침
                existing_warnings = list(cand.match_reasons)
                cand.match_reasons = build_match_reasons(cand, resolved)
                cand.match_reasons.extend(existing_warnings)
                part_dict["candidates"].append({
                    "company_id": cand.company_id,
                    "company_name": cand.company_name,
                    "match_reasons": cand.match_reasons,
                    "material_match_type": cand.material_match_type,
                    "best_it_grade": cand.best_it_grade,
                    "best_ra_um": cand.best_ra_um,
                    "overall_status": cand.overall_status,
                    "avg_rating": round(cand.avg_rating_overall, 1) if cand.avg_rating_overall is not None else None,
                    "review_count": cand.review_count,
                    "next_available_date": cand.next_available_date,
                    "equipment_verified": cand.equipment_verified,
                    "equipment_verified_warning": cand.equipment_verified_warning,
                    "equipment_summary": cand.equipment_summary,
                })

        # 후보를 recommended / conditional로 분리 (기존 candidates 키 유지)
        all_cands = part_dict.get("candidates", [])
        part_dict["recommended_candidates"] = [
            c for c in all_cands
            if c.get("equipment_verified") and not c.get("equipment_verified_warning")
        ]
        part_dict["conditional_candidates"] = [
            c for c in all_cands
            if not c.get("equipment_verified") or c.get("equipment_verified_warning")
        ]

        parts.append(part_dict)

    return MatchResponse(
        rfq_id=rfq_id,
        drawing_no=drawing_no,
        delivery_date=delivery_date,
        parts=parts,
        client_notes=client_notes,
    )


def to_json(response: MatchResponse) -> dict:
    """MatchResponse를 JSON 직렬화 가능한 dict로 변환한다."""
    result = {
        "rfq_id": response.rfq_id,
        "drawing_no": response.drawing_no,
        "requested_delivery_date": response.delivery_date,
        "parts": response.parts,
    }
    if response.client_notes is not None:
        result["client_notes"] = response.client_notes
    return result
