"""
매칭 엔드포인트:
- GET  /match/{rfq_id}                                          (기존 MV 기반 v1)
- POST /api/match-v2                                            (기존 RAG pipeline + B-3 이력 저장)
- GET  /api/company/matches                                     (B-3b: 업체 수신 매칭 조회)
- PUT  /api/match-candidates/{match_run_id}/{company_id}/respond (B-4: 수락/거절)
"""

import json
import logging
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

logger = logging.getLogger(__name__)

from routers.deps import engine, SCHEMA, get_current_user, _create_notification

router = APIRouter()

# ---------------------------------------------------------------------------
# pipeline 모듈 경로 보장 후 lookup + pipeline_runner + graphrag 통합 import
# ---------------------------------------------------------------------------

PIPELINE_DIR = Path(__file__).resolve().parent.parent / "pipeline"
if PIPELINE_DIR.exists() and str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from lookup import FAIL_OPEN_PROCESSES, SAFE_PARENT_FALLBACK  # noqa: E402

try:
    from pipeline_runner import run_pipeline_from_dict
    from graphrag_transform import transform_vlm_raw
except Exception:
    run_pipeline_from_dict = None
    transform_vlm_raw = None


# ---------------------------------------------------------------------------
# Matching v1 (MV 기반)
# ---------------------------------------------------------------------------


@router.get("/match/{rfq_id}")
def match_suppliers(rfq_id: str, current_user: dict = Depends(get_current_user)):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        rfq_result = conn.execute(
            text(f"""
                SELECT
                    r.rfq_id, r.buyer_id,
                    rp.material_raw_text,
                    string_agg(DISTINCT rpp.process_code, ', '),
                    rp.quantity,
                    r.requested_delivery_date,
                    r.general_notes_jsonb->>'note'
                FROM {SCHEMA}.rfqs r
                LEFT JOIN {SCHEMA}.rfq_parts rp ON r.rfq_id = rp.rfq_id
                LEFT JOIN {SCHEMA}.rfq_part_processes rpp ON rp.rfq_part_id = rpp.rfq_part_id
                WHERE r.rfq_id = :rfq_id
                GROUP BY r.rfq_id, r.buyer_id, rp.material_raw_text, rp.quantity,
                         r.requested_delivery_date, r.general_notes_jsonb
            """),
            {"rfq_id": rfq_id},
        )
        rfq = rfq_result.fetchone()

        if rfq is None:
            raise HTTPException(status_code=404, detail="RFQ not found")

        # 소유권 검증: buyer=RFQ 소유자, supplier=매칭 후보, admin=전체
        rfq_buyer_id = str(rfq[1]) if rfq[1] else None
        role = current_user["role"]
        uid = current_user["id"]
        if role == "buyer":
            if rfq_buyer_id != uid:
                raise HTTPException(status_code=403, detail="본인 RFQ만 조회할 수 있습니다")
        elif role == "supplier":
            mc = conn.execute(
                text(f"""SELECT 1 FROM {SCHEMA}.match_candidates mc
                         JOIN {SCHEMA}.match_runs mr ON mr.match_run_id = mc.match_run_id
                         WHERE mr.rfq_id = CAST(:rid AS uuid)
                           AND mc.company_id = CAST(:cid AS uuid) LIMIT 1"""),
                {"rid": rfq_id, "cid": uid},
            ).fetchone()
            if mc is None:
                raise HTTPException(status_code=403, detail="매칭된 RFQ만 조회할 수 있습니다")
        elif role != "admin":
            raise HTTPException(status_code=403, detail="권한이 없습니다")

        material = rfq[2] or ""
        process_csv = rfq[3] or ""
        process_codes = [p.strip() for p in process_csv.split(",") if p.strip()]

        match_result = conn.execute(
            text(f"""
                SELECT
                    cs.company_id,
                    cs.company_name,
                    COALESCE(s.region, s.city, '') AS region,
                    COALESCE(c.company_size, '')    AS company_size,
                    cs.process_codes,
                    cs.best_it_grade,
                    cs.best_ra_um,
                    cs.avg_rating_overall,
                    cs.overall_status,
                    cs.material_codes,
                    cs.material_category_codes
                FROM {SCHEMA}.company_capability_summary cs
                JOIN {SCHEMA}.companies c ON cs.company_id = c.company_id
                LEFT JOIN {SCHEMA}.company_sites s
                    ON c.company_id = s.company_id AND s.is_primary = true
                WHERE cs.overall_status IN ('available', 'limited', 'unknown')
                  AND (
                      cs.material_codes @> ARRAY[upper(:material)]::text[]
                      OR cs.material_category_codes @> ARRAY[lower(:material)]::text[]
                  )
            """),
            {"material": material},
        )
        rows = match_result.fetchall()

    suppliers = []
    for row in rows:
        company_process_codes = row[4] or []
        matched = [p for p in process_codes if p in company_process_codes]
        if not matched and process_codes:
            continue

        best_it = row[5] or 10
        best_ra = row[6] or 3.2
        avg_rating = row[7] or 3.0

        match_score = 100
        match_score += (10 - best_it) * 4
        if best_ra <= 0.8:
            match_score += 15
        elif best_ra <= 1.6:
            match_score += 10
        match_score += int((avg_rating - 3.0) * 10)

        suppliers.append({
            "company_code": str(row[0]),
            "company_name": row[1],
            "region": row[2],
            "company_size": row[3],
            "match_score": match_score,
            "matched_processes": ", ".join(matched) if matched else process_csv,
            "service_mode": "in_house",
            "best_it_grade": best_it,
            "best_tolerance_mm": None,
            "best_ra_um": best_ra,
            "avg_lead_days": None,
            "matched_materials": ", ".join(row[9] or []),
            "score_reason": {
                "it_grade": "IT 등급 숫자가 낮을수록 정밀도 우수",
                "surface_roughness": "Ra 값이 낮을수록 표면 품질 우수",
                "rating": "업체 평점 반영",
            },
        })

    suppliers.sort(key=lambda x: x["match_score"], reverse=True)

    return {
        "rfq": {
            "id": str(rfq[0]),
            "buyer_code": str(rfq[1]) if rfq[1] else None,
            "material": rfq[2],
            "process": rfq[3],
            "quantity": rfq[4],
            "due_date": str(rfq[5]) if rfq[5] else None,
            "note": rfq[6],
        },
        "match_count": len(suppliers),
        "recommended_suppliers": suppliers,
    }


# ---------------------------------------------------------------------------
# Matching v2 (RAG pipeline + B-3 이력 저장)
# ---------------------------------------------------------------------------


@router.post("/api/match-v2")
def match_v2(data: dict, current_user: dict = Depends(get_current_user)):
    """RAG 파이프라인 실행 후 match_runs/match_candidates에 이력 저장 + 알림 발송"""
    if current_user["role"] not in ("buyer", "admin"):
        raise HTTPException(status_code=403, detail="buyer 또는 admin만 매칭을 실행할 수 있습니다")
    if run_pipeline_from_dict is None:
        raise HTTPException(status_code=500, detail="RAG pipeline is not loaded")
    if not data:
        raise HTTPException(status_code=400, detail="요청 본문이 비어 있습니다")

    # buyer role일 때만 _buyer_id 전달. admin 호출 시 buyers FK 위반 회피 (admin UUID는 buyers에 없음)
    if current_user["role"] == "buyer":
        data["_buyer_id"] = current_user["id"]

    # drawing_id 처리: 소유권 검증 + parts 미제공 시 GraphRAG 자동 호출
    structured = None  # GraphRAG 변환 결과 보존 (시연 모달 영역 응답 동봉용)
    d_vlm_raw = None
    if data.get("drawing_id"):
        if engine is None:
            raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
        # UUID 형식 사전 검증
        try:
            uuid.UUID(str(data["drawing_id"]))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="drawing_id가 유효한 UUID 형식이 아닙니다")
        with engine.connect() as conn:
            drawing_row = conn.execute(
                text(f"""SELECT buyer_id, vlm_result_jsonb
                         FROM {SCHEMA}.drawings WHERE drawing_id = CAST(:did AS uuid)"""),
                {"did": data["drawing_id"]},
            ).fetchone()
        if drawing_row is None:
            raise HTTPException(status_code=404, detail="도면을 찾을 수 없습니다")
        d_buyer_id = str(drawing_row[0]) if drawing_row[0] else None
        d_vlm_raw = drawing_row[1] or {}
        if current_user["role"] == "buyer" and d_buyer_id != current_user["id"]:
            raise HTTPException(status_code=403, detail="본인이 업로드한 도면만 사용할 수 있습니다")
        data["_drawing_id"] = data["drawing_id"]

        # parts 미제공 시 vlm_result_jsonb를 GraphRAG로 변환 (자동 분석 흐름)
        if "parts" not in data:
            if transform_vlm_raw is None:
                raise HTTPException(status_code=500, detail="GraphRAG transformer is not loaded")
            if not d_vlm_raw:
                raise HTTPException(status_code=400, detail="도면의 VLM 결과가 비어있어 자동 분석 불가")
            try:
                structured = transform_vlm_raw(d_vlm_raw)
            except Exception:
                logger.exception("GraphRAG transform 실패")
                raise HTTPException(status_code=500, detail="GraphRAG 변환 실패")
            # structured에서 parts + drawing_no + referenced_standards 등을 data에 병합
            for k, v in structured.items():
                if k not in data:
                    data[k] = v

    if "parts" not in data:
        raise HTTPException(status_code=400, detail="parts 필드 또는 drawing_id가 필요합니다")

    try:
        result = run_pipeline_from_dict(data)
    except Exception:
        logger.exception("match-v2 pipeline 실행 실패")
        raise HTTPException(status_code=500, detail="매칭 파이프라인 실행 실패")

    # --- B-3: 매칭 이력 저장 ---
    # result에서 rfq_id, buyer_id, 후보 목록을 추출하여 DB에 기록
    # pipeline 결과 구조에서 rfq_id와 후보 정보 추출 시도
    if engine is not None:
        try:
            _save_match_history(data, result)
        except Exception:
            logger.exception("match history 저장 실패")
            raise HTTPException(
                status_code=500,
                detail="매칭 결과 저장 또는 supplier 전송에 실패했습니다. 다시 실행해 주세요.",
            )

    # --- AI 처리 과정 시연용 메타 동봉 (drawing_id 경로 한정) ---
    # 4 단계 시각: 도면 → VLM raw → Gemini 변환 → 매칭 변환(match_input). 매칭 화면 모달에서 노출
    if isinstance(result, dict) and "_drawing_id" in data:
        result["drawing_id"] = data["_drawing_id"]  # frontend bindAiProcessModal resolveDrawingId 정합
        if structured is not None:
            result["graphrag_raw"] = structured
        if d_vlm_raw:
            result["vlm_raw"] = d_vlm_raw

    return result


def _compute_availability_score(conn, company_id: str, rfq_id: str):
    """업체의 납기 가용성 점수를 산출.

    정밀화 로직:
    1) 부품 공정을 사내/외주(FAIL_OPEN)로 분리. 외주 lead는 시간합 검증 제외
    2) 사내 공정 가능 장비 풀로 시간합 좁힘 (parent fallback 양방향 + EXISTS 중복 제거)
    3) equipment_daily_schedule 시드 한계 인식 (부분 일치 시 0.7 폴백)

    반환: (availability_score, availability_info dict)
    """
    default_info = {
        "available_from": None,
        "available_days": None,
        "estimated_lead_days": None,
        "delivery_feasible": None,
    }

    # ① 납기일 조회
    try:
        rfq_row = conn.execute(
            text(f"SELECT requested_delivery_date FROM {SCHEMA}.rfqs WHERE rfq_id = :rid"),
            {"rid": rfq_id},
        ).fetchone()
    except Exception:
        logger.exception("availability: rfq 조회 실패 rfq_id=%s", rfq_id)
        return 0.5, default_info
    if not rfq_row or not rfq_row[0]:
        return 0.5, default_info
    requested_delivery = rfq_row[0]

    # ② 사내/외주 공정 분리 + lead 합산
    fail_open_list = list(FAIL_OPEN_PROCESSES)
    try:
        procs_row = conn.execute(
            text(f"""
                SELECT
                  COALESCE(SUM(CASE WHEN rpp.process_code = ANY(:fail_open)
                                    THEN COALESCE(cpc.typical_lead_days, 3) ELSE 0 END), 0) AS outsource_lead,
                  COALESCE(SUM(CASE WHEN rpp.process_code != ALL(:fail_open)
                                    THEN COALESCE(cpc.typical_lead_days, 3) ELSE 0 END), 0) AS inhouse_lead,
                  array_agg(DISTINCT rpp.process_code) FILTER
                      (WHERE rpp.process_code != ALL(:fail_open)) AS inhouse_procs,
                  COUNT(rp.rfq_part_id) AS parts_count
                FROM {SCHEMA}.rfq_parts rp
                LEFT JOIN {SCHEMA}.rfq_part_processes rpp
                    ON rp.rfq_part_id = rpp.rfq_part_id
                LEFT JOIN {SCHEMA}.company_process_capabilities cpc
                    ON cpc.company_id = :cid
                    AND cpc.process_code = rpp.process_code
                    AND cpc.is_active = true
                WHERE rp.rfq_id = :rid
            """),
            {"fail_open": fail_open_list, "cid": company_id, "rid": rfq_id},
        ).fetchone()
    except Exception:
        logger.exception("availability: procs 조회 실패 cid=%s rfq_id=%s", company_id, rfq_id)
        return 0.5, default_info

    if procs_row is None or (procs_row[3] or 0) == 0:
        return 0.5, default_info

    outsource_lead = int(procs_row[0] or 0)
    inhouse_lead = int(procs_row[1] or 0)
    inhouse_procs = procs_row[2] or []
    total_lead_days = outsource_lead + inhouse_lead
    if total_lead_days <= 0:
        total_lead_days = 3

    # ③ 시간 범위
    today = date.today()
    latest_start = requested_delivery - timedelta(days=total_lead_days)
    info = {
        "available_from": today.isoformat(),
        "available_days": None,
        "estimated_lead_days": total_lead_days,
        "delivery_feasible": None,
    }
    if latest_start < today:
        info["delivery_feasible"] = False
        info["available_days"] = 0
        return 0.3, info

    # ④ 시드 한계 + 부분 일치 폴백
    try:
        max_seed_row = conn.execute(
            text(f"SELECT MAX(schedule_date) FROM {SCHEMA}.equipment_daily_schedule WHERE company_id = :cid"),
            {"cid": company_id},
        ).fetchone()
        max_seed = max_seed_row[0] if max_seed_row else None
    except Exception:
        logger.exception("availability: max_seed 조회 실패 cid=%s", company_id)
        max_seed = None

    if max_seed is None or max_seed < today:
        # 시드 데이터 없음
        info["delivery_feasible"] = True
        info["available_days"] = (latest_start - today).days
        return 0.5, info
    if max_seed < latest_start:
        # 시드 부분 일치 — 시드 범위 외 납기는 검증 불가
        info["delivery_feasible"] = True
        info["available_days"] = (max_seed - today).days
        return 0.7, info

    # ⑤ 전외주 (사내 공정 0개) — 시간합 검증 무의미
    if not inhouse_procs:
        info["delivery_feasible"] = True
        return 0.9, info

    # ⑥ 사내 공정 가능 장비 시간합
    # parent fallback은 SAFE_PARENT_FALLBACK 화이트리스트(turning/milling rough/finish)에 한해 적용.
    # equipment_verification과 동일 정책으로 grinding 가족(gear_grinding 등)의 일반 grinder 매칭을 차단.
    safe_parent_procs = [p for p in inhouse_procs if p in SAFE_PARENT_FALLBACK]
    try:
        avail_row = conn.execute(
            text(f"""
                SELECT COALESCE(SUM(eds.available_hours), 0),
                       COUNT(DISTINCT eds.schedule_date)
                           FILTER (WHERE eds.status IN ('available','partially_booked'))
                FROM {SCHEMA}.equipment_daily_schedule eds
                JOIN {SCHEMA}.equipment e ON e.equipment_id = eds.equipment_id
                WHERE e.company_id = :cid
                  AND eds.schedule_date BETWEEN :d_from AND :d_to
                  AND eds.status IN ('available','partially_booked')
                  AND EXISTS (
                      SELECT 1 FROM {SCHEMA}.equipment_process_capabilities epc
                      WHERE epc.equipment_id = eds.equipment_id
                        AND (epc.process_code = ANY(:inhouse_procs)
                             OR epc.process_code IN (
                                 SELECT process_code FROM {SCHEMA}.process_catalog
                                 WHERE parent_process_code = ANY(:inhouse_procs))
                             OR epc.process_code IN (
                                 SELECT parent_process_code FROM {SCHEMA}.process_catalog
                                 WHERE process_code = ANY(:safe_parent_procs)))
                  )
            """),
            {"cid": company_id, "inhouse_procs": inhouse_procs,
             "safe_parent_procs": safe_parent_procs,
             "d_from": today, "d_to": latest_start},
        ).fetchone()
        total_available_hours = float(avail_row[0])
        available_days = int(avail_row[1])
    except Exception:
        logger.exception("availability: 시간합 조회 실패 cid=%s", company_id)
        info["delivery_feasible"] = True
        info["available_days"] = (latest_start - today).days
        return 0.5, info

    info["available_days"] = available_days
    info["delivery_feasible"] = True

    # ⑦ 사내 공정 신고했으나 가능 장비 0대 (현재 또는 시드 범위 내 가용 0)
    if total_available_hours == 0 and available_days == 0:
        info["delivery_feasible"] = False
        return 0.3, info

    required_hours = inhouse_lead * 8.0  # 사내 공정 lead만 시간 검증
    if required_hours <= 0:
        required_hours = 8.0

    # ⑧ 3단계 점수
    if total_available_hours >= required_hours:
        return 1.0, info
    elif total_available_hours >= required_hours * 0.5:
        return 0.7, info
    else:
        return 0.3, info


def _save_match_history(input_data: dict, pipeline_result):
    """매칭 결과를 match_runs / match_candidates에 저장하고 알림 발송.
    availability_score, technical_score, quality_score를 산출하여
    total_score에 가중 합산 반영."""
    # pipeline_result 가 dict 가 아닌 경우 건너뜀
    if not isinstance(pipeline_result, dict):
        return

    # rfq_id 추출 (pipeline 결과 또는 입력 데이터에서)
    rfq_id = (pipeline_result.get("rfq_id")
              or pipeline_result.get("rfq", {}).get("id")
              or input_data.get("rfq_id"))

    buyer_id = None
    if rfq_id and engine:
        with engine.connect() as conn:
            _buyer_row = conn.execute(
                text(f"SELECT buyer_id FROM {SCHEMA}.rfqs WHERE rfq_id = CAST(:rid AS uuid)"),
                {"rid": rfq_id},
            ).fetchone()
            if _buyer_row:
                buyer_id = str(_buyer_row[0])

    # 후보 목록 추출: result["parts"][i]["candidates"]를 순회하여 수집
    all_candidates = []
    for part in pipeline_result.get("parts", []):
        rpid = part.get("rfq_part_id")
        for cand in part.get("candidates", []):
            cand["_rfq_part_id"] = rpid
            all_candidates.append(cand)
    candidates = all_candidates

    # 입력 요약
    input_summary = {
        "parts_count": len(input_data.get("parts", [])),
        "parts": [
            {
                "material": p.get("material"),
                "processes": p.get("processes"),
                "quantity": p.get("quantity"),
            }
            for p in input_data.get("parts", [])[:5]  # 최대 5개만 요약
        ],
    }

    with engine.begin() as conn:
        # match_runs INSERT
        mr_row = conn.execute(
            text(f"""
                INSERT INTO {SCHEMA}.match_runs
                    (rfq_id, algorithm_version, mode, input_summary_jsonb)
                VALUES (:rfq_id, 'phase1_hard_filter', 'hard_filter',
                        CAST(:input_summary AS JSONB))
                RETURNING match_run_id
            """),
            {
                "rfq_id": rfq_id,
                "input_summary": json.dumps(input_summary, ensure_ascii=False),
            },
        ).fetchone()
        match_run_id = mr_row[0]

        # 각 후보별 스코어 산출 + match_candidates INSERT
        scored_candidates = []

        for cand in candidates:
            company_id = cand.get("company_code") or cand.get("company_id")
            if not company_id:
                continue

            # --- technical_score ---
            best_it = cand.get("best_it_grade")
            if best_it is not None and best_it < 99:
                technical_score = round((18 - best_it) / 18, 3)
            else:
                technical_score = 0.5

            # --- quality_score ---
            # 신규 가입 직후 업체는 리뷰가 누적되지 않아 avg_rating = NULL.
            # 데이터 부재를 *중립 0.5* 로 처리하면 신규 업체가 매칭 상위에서 누락되어
            # 온보딩 직후 첫 매칭 노출 영역이 차단된다. 신규 업체 fail-open 영역으로
            # 평점 부재 = 1.0 (5/5 기본) 폴백 — 첫 리뷰 누적 전까지의 신규 업체 버닝 정책.
            avg_rating = cand.get("avg_rating") or cand.get("avg_rating_overall")
            if avg_rating is not None and avg_rating > 0:
                quality_score = round(float(avg_rating) / 5.0, 3)
            else:
                quality_score = 1.0

            # --- availability_score ---
            # 신규 가입 직후 업체는 equipment_daily_schedule 시드가 부재하여
            # _compute_availability_score 내부에서 0.5 폴백이 반환된다. 평점과 동일하게
            # 데이터 부재 = 가용성 풀(가동률 널널 + 스케쥴 비어있음) 1.0 폴백으로 처리.
            if rfq_id:
                availability_score, availability_info = _compute_availability_score(
                    conn, str(company_id), str(rfq_id),
                )
                # 시드 부재 영역의 0.5 폴백 → 1.0 보정 (신규 업체 fail-open)
                if availability_score == 0.5 and availability_info.get("available_from") is None:
                    availability_score = 1.0
            else:
                availability_score = 1.0
                availability_info = {
                    "available_from": None,
                    "available_days": None,
                    "estimated_lead_days": None,
                    "delivery_feasible": None,
                }

            # --- total_score (가중 합산) ---
            total_score = round(
                technical_score * 0.4
                + availability_score * 0.3
                + quality_score * 0.3,
                3,
            )

            scored_candidates.append({
                "company_id": company_id,
                "_rfq_part_id": cand.get("_rfq_part_id"),
                "technical_score": technical_score,
                "availability_score": availability_score,
                "quality_score": quality_score,
                "total_score": total_score,
                "availability_info": availability_info,
                "explanation": cand.get("score_reason") or cand.get("explanation") or {},
            })

        # total_score 내림차순 정렬 → rank_no
        scored_candidates.sort(key=lambda x: x["total_score"], reverse=True)

        for rank, sc in enumerate(scored_candidates, 1):
            sc["rank_no"] = rank
            conn.execute(
                text(f"""
                    INSERT INTO {SCHEMA}.match_candidates
                        (match_run_id, company_id, rfq_part_id, hard_filter_pass,
                         technical_score, availability_score, quality_score,
                         total_score, rank_no, explanation_jsonb,
                         supplier_response)
                    VALUES (:mrid, :cid, CAST(:rpid AS uuid), true,
                            :tech, :avail, :qual,
                            :score, :rank,
                            CAST(:explanation AS JSONB), 'pending')
                    ON CONFLICT (match_run_id, company_id, rfq_part_id) DO NOTHING
                """),
                {
                    "mrid": match_run_id,
                    "cid": sc["company_id"],
                    "rpid": sc.get("_rfq_part_id"),
                    "tech": sc["technical_score"],
                    "avail": sc["availability_score"],
                    "qual": sc["quality_score"],
                    "score": sc["total_score"],
                    "rank": rank,
                    "explanation": json.dumps(sc["explanation"], ensure_ascii=False),
                },
            )

            # 알림: 매칭된 supplier에게 match_request
            _create_notification(
                conn,
                recipient_type="supplier",
                recipient_id=str(sc["company_id"]),
                event_type="match_request",
                title="새로운 제조 요청이 도착했습니다",
                message=f"RFQ {rfq_id}에 대한 매칭 요청입니다. 확인 후 수락/거절해 주세요.",
                ref_id=str(match_run_id),
                ref_type="match_run",
            )

        # 알림: buyer에게 match_completed
        if buyer_id:
            _create_notification(
                conn,
                recipient_type="buyer",
                recipient_id=str(buyer_id),
                event_type="match_completed",
                title="매칭이 완료되었습니다",
                message=f"RFQ {rfq_id}에 대해 {len(scored_candidates)}개 업체가 매칭되었습니다.",
                ref_id=str(match_run_id),
                ref_type="match_run",
            )

        # --- 매칭 결과 응답에 match_run_id/rfq_part_id/rank_no + score 보강 ---
        score_lookup = {
            (str(sc["company_id"]), str(sc.get("_rfq_part_id") or "")): sc
            for sc in scored_candidates
        }
        single_part = len([p for p in pipeline_result.get("parts", []) if p.get("rfq_part_id")]) <= 1

        for part in pipeline_result.get("parts", []):
            part_rpid = str(part.get("rfq_part_id") or "")
            for cand in part.get("candidates", []):
                cid = str(cand.get("company_code") or cand.get("company_id") or "")
                sc = score_lookup.get((cid, part_rpid))
                # part id가 없는 과거/단부품 결과에서만 company 단독 fallback을 허용한다.
                if sc is None and single_part:
                    sc = next((item for (company_key, _), item in score_lookup.items() if company_key == cid), None)
                cand["match_run_id"] = str(match_run_id)
                cand["rfq_part_id"] = part.get("rfq_part_id")
                if sc:
                    cand["rank_no"] = sc.get("rank_no")
                    cand["technical_score"] = sc["technical_score"]
                    cand["availability_score"] = sc["availability_score"]
                    cand["quality_score"] = sc["quality_score"]
                    cand["total_score"] = sc["total_score"]
                    cand["availability_info"] = sc["availability_info"]
                else:
                    cand["rank_no"] = None
                    cand["availability_score"] = None
                    cand["availability_info"] = None

            all_candidates = part.get("candidates", [])
            part["recommended_candidates"] = [
                c for c in all_candidates
                if c.get("equipment_verified") and not c.get("equipment_verified_warning")
            ]
            part["conditional_candidates"] = [
                c for c in all_candidates
                if not c.get("equipment_verified") or c.get("equipment_verified_warning")
            ]


# ---------------------------------------------------------------------------
# B-3b: GET /api/company/matches — 업체 수신 매칭 조회
# ---------------------------------------------------------------------------


@router.get("/api/company/matches")
def get_company_matches(user: dict = Depends(get_current_user)):
    """JWT의 company_id로 본인에게 온 매칭 요청 목록 조회"""
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    company_id = user["id"]

    with engine.connect() as conn:
        rows = conn.execute(
            text(f"""
                SELECT
                    mc.match_run_id,
                    mr.rfq_id,
                    rp.part_name,
                    rp.material_raw_text,
                    string_agg(DISTINCT rpp.process_code, ', ') AS processes,
                    mc.total_score,
                    mc.rank_no,
                    mc.supplier_response,
                    mc.responded_at,
                    mc.created_at
                FROM {SCHEMA}.match_candidates mc
                JOIN {SCHEMA}.match_runs mr ON mc.match_run_id = mr.match_run_id
                LEFT JOIN {SCHEMA}.rfqs r ON mr.rfq_id = r.rfq_id
                LEFT JOIN {SCHEMA}.rfq_parts rp ON mr.rfq_part_id = rp.rfq_part_id
                    OR (mr.rfq_part_id IS NULL AND rp.rfq_id = mr.rfq_id)
                LEFT JOIN {SCHEMA}.rfq_part_processes rpp ON rp.rfq_part_id = rpp.rfq_part_id
                WHERE mc.company_id = :cid
                GROUP BY mc.match_run_id, mr.rfq_id, rp.part_name,
                         rp.material_raw_text, mc.total_score, mc.rank_no,
                         mc.supplier_response, mc.responded_at, mc.created_at
                ORDER BY mc.created_at DESC
            """),
            {"cid": company_id},
        ).fetchall()

    matches = []
    for row in rows:
        matches.append({
            "match_run_id": str(row[0]),
            "rfq_id": str(row[1]) if row[1] else None,
            "part_name": row[2],
            "material": row[3],
            "processes": row[4],
            "total_score": float(row[5]) if row[5] is not None else None,
            "rank_no": row[6],
            "supplier_response": row[7],
            "responded_at": str(row[8]) if row[8] else None,
            "created_at": str(row[9]),
        })

    return {"count": len(matches), "matches": matches}


# ---------------------------------------------------------------------------
# B-4: PUT /api/match-candidates/{match_run_id}/{company_id}/respond
# ---------------------------------------------------------------------------


@router.put("/api/match-candidates/{match_run_id}/{company_id}/respond")
def respond_to_match(match_run_id: str, company_id: str, data: dict,
                     user: dict = Depends(get_current_user)):
    """업체 수락/거절. JWT의 company_id와 path의 company_id 일치 검증."""
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    # 소유권 검증
    if user["id"] != company_id:
        raise HTTPException(status_code=403, detail="본인의 매칭 요청만 응답할 수 있습니다")

    response = data.get("response")
    if response not in ("accepted", "declined"):
        raise HTTPException(
            status_code=400,
            detail="response는 'accepted' 또는 'declined'이어야 합니다",
        )

    with engine.begin() as conn:
        # 매칭 후보 존재 확인
        mc_row = conn.execute(
            text(f"""
                SELECT mc.supplier_response, mr.rfq_id
                FROM {SCHEMA}.match_candidates mc
                JOIN {SCHEMA}.match_runs mr ON mc.match_run_id = mr.match_run_id
                WHERE mc.match_run_id = :mrid AND mc.company_id = :cid
            """),
            {"mrid": match_run_id, "cid": company_id},
        ).fetchone()

        if mc_row is None:
            raise HTTPException(status_code=404, detail="매칭 후보를 찾을 수 없습니다")

        current_response = mc_row[0]
        rfq_id = mc_row[1]

        # 이미 응답한 경우
        if current_response and current_response not in ("pending", None):
            raise HTTPException(
                status_code=400,
                detail=f"이미 '{current_response}'(으)로 응답하셨습니다",
            )

        # supplier_response + responded_at UPDATE
        conn.execute(
            text(f"""
                UPDATE {SCHEMA}.match_candidates
                SET supplier_response = :resp, responded_at = now()
                WHERE match_run_id = :mrid AND company_id = :cid
            """),
            {"resp": response, "mrid": match_run_id, "cid": company_id},
        )

        # buyer에게 알림 발송
        if rfq_id:
            buyer_row = conn.execute(
                text(f"SELECT buyer_id FROM {SCHEMA}.rfqs WHERE rfq_id = :rid"),
                {"rid": rfq_id},
            ).fetchone()

            if buyer_row and buyer_row[0]:
                event = "supplier_accepted" if response == "accepted" else "supplier_declined"
                title = ("업체가 매칭을 수락했습니다" if response == "accepted"
                         else "업체가 매칭을 거절했습니다")
                # 업체명 조회
                comp_row = conn.execute(
                    text(f"SELECT company_name FROM {SCHEMA}.companies WHERE company_id = :cid"),
                    {"cid": company_id},
                ).fetchone()
                comp_name = comp_row[0] if comp_row else company_id

                _create_notification(
                    conn,
                    recipient_type="buyer",
                    recipient_id=str(buyer_row[0]),
                    event_type=event,
                    title=title,
                    message=f"{comp_name}이(가) RFQ {rfq_id}에 대한 매칭을 {'수락' if response == 'accepted' else '거절'}했습니다.",
                    ref_id=str(rfq_id),
                    ref_type="rfq",
                )

    return {
        "success": True,
        "match_run_id": match_run_id,
        "company_id": company_id,
        "response": response,
    }
