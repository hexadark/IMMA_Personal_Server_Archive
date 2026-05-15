"""
Phase E-2: 관리자 엔드포인트
- POST /api/admin/login                        — 관리자 로그인
- GET  /api/admin/companies/pending            — 검수 대기 업체 목록
- PUT  /api/admin/companies/{id}/verify        — 업체 수동 승인
- PUT  /api/admin/companies/{id}/reject        — 업체 반려 + 사유
- GET  /api/admin/rfqs                         — 전체 RFQ 현황
- GET  /api/admin/orders                       — 전체 발주 현황
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

logger = logging.getLogger(__name__)

from routers.deps import (
    engine, SCHEMA,
    _verify_password, _create_token,
    get_current_admin, _refresh_mv, _create_notification,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# E-2: POST /api/admin/login — 관리자 로그인
# ---------------------------------------------------------------------------


@router.post("/api/admin/login")
def admin_login(data: dict):
    """admins 테이블에서 login_id 조회 → _verify_password → JWT 발급 (role='admin')."""
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    login_id = data.get("login_id")
    password = data.get("password")

    if not login_id or not password:
        raise HTTPException(status_code=400, detail="login_id와 password가 필요합니다")

    with engine.connect() as conn:
        try:
            row = conn.execute(
                text(f"""
                    SELECT admin_id, login_id, password_hash, role, name
                    FROM {SCHEMA}.admins
                    WHERE login_id = :login_id
                """),
                {"login_id": login_id},
            ).fetchone()
        except Exception:
            # admins 테이블이 아직 없음
            raise HTTPException(
                status_code=500,
                detail="admins 테이블이 아직 생성되지 않았습니다",
            )

    if row is None:
        raise HTTPException(status_code=401, detail="존재하지 않는 관리자 계정입니다")

    admin_id = str(row[0])
    stored_login_id = row[1]
    password_hash = row[2]
    admin_role = row[3]
    admin_name = row[4]

    if not _verify_password(password, password_hash):
        raise HTTPException(status_code=401, detail="비밀번호가 일치하지 않습니다")

    token = _create_token(
        sub=admin_id,
        login_id=stored_login_id,
        role="admin",
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": admin_id,
            "login_id": stored_login_id,
            "role": "admin",
            "name": admin_name,
            "admin_role": admin_role,
        },
    }


# ---------------------------------------------------------------------------
# E-2: GET /api/admin/companies/pending — 검수 대기 업체 목록
# ---------------------------------------------------------------------------


@router.get("/api/admin/companies/pending")
def get_pending_companies(admin: dict = Depends(get_current_admin)):
    """
    companies WHERE onboarding_status = 'submitted'
    + company_sites JOIN (primary site의 region 포함).

    검수 대기 정의: submitted 단일 단계.
    verified 는 이미 승인 완료이므로 pending 목록에서 제외.
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        rows = conn.execute(
            text(f"""
                SELECT c.company_id, c.company_name, c.main_email,
                       c.onboarding_status, c.created_at,
                       cs.region
                FROM {SCHEMA}.companies c
                LEFT JOIN {SCHEMA}.company_sites cs
                    ON c.company_id = cs.company_id AND cs.is_primary = true
                WHERE c.onboarding_status = 'submitted'
                ORDER BY c.created_at DESC
            """),
        ).fetchall()

    return [
        {
            "company_id": str(r[0]),
            "company_name": r[1],
            "main_email": r[2],
            "onboarding_status": r[3],
            "created_at": str(r[4]),
            "region": r[5],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# E-2: PUT /api/admin/companies/{company_id}/verify — 업체 수동 승인
# ---------------------------------------------------------------------------


@router.put("/api/admin/companies/{company_id}/verify")
def verify_company(company_id: str, admin: dict = Depends(get_current_admin)):
    """companies.onboarding_status = 'verified' UPDATE + _refresh_mv.

    이미 verified/draft/rejected 영역인 업체에 대한 재승인을 차단.
    submitted 단계의 업체만 승인 가능.
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.begin() as conn:
        # 업체 존재 + 현재 status 확인 — submitted 만 verify 진입 허용
        row = conn.execute(
            text(f"""
                SELECT onboarding_status
                FROM {SCHEMA}.companies
                WHERE company_id = :cid
            """),
            {"cid": company_id},
        ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="업체를 찾을 수 없습니다")

        current_status = row[0]
        if current_status != "submitted":
            # 상태별 메시지 분기 — supplier 운용 영역 안내 정합
            if current_status == "verified":
                detail_msg = "이미 승인된 업체입니다"
            elif current_status == "draft":
                detail_msg = "온보딩 미완료 — supplier 가 정보 입력 영역 진행 중"
            elif current_status == "rejected":
                detail_msg = "이미 반려된 업체. 반려 사유 확인 후 재신청 영역"
            else:
                detail_msg = f"승인 불가 상태: {current_status}"
            raise HTTPException(status_code=400, detail=detail_msg)

        # onboarding_status → verified
        conn.execute(
            text(f"""
                UPDATE {SCHEMA}.companies
                SET onboarding_status = 'verified', updated_at = now()
                WHERE company_id = :cid
            """),
            {"cid": company_id},
        )

        # MV REFRESH — SAVEPOINT로 격리 (실패 시 outer 트랜잭션 abort 방지)
        nested = conn.begin_nested()
        try:
            _refresh_mv(conn)
            nested.commit()
        except Exception:
            nested.rollback()
            logger.exception("MV refresh 실패 — 후속 작업은 계속 진행")

    return {
        "success": True,
        "company_id": company_id,
        "onboarding_status": "verified",
    }


# ---------------------------------------------------------------------------
# E-2: PUT /api/admin/companies/{company_id}/reject — 업체 반려 + 사유
# ---------------------------------------------------------------------------


@router.put("/api/admin/companies/{company_id}/reject")
def reject_company(
    company_id: str,
    data: dict,
    admin: dict = Depends(get_current_admin),
):
    """
    companies.onboarding_status = 'rejected' UPDATE + notes에 거부 사유 기록.
    _refresh_mv 호출 (MV에서 제외). supplier에게 알림.
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    reason = data.get("reason", "")

    with engine.begin() as conn:
        # 업체 존재 확인
        row = conn.execute(
            text(f"""
                SELECT onboarding_status
                FROM {SCHEMA}.companies
                WHERE company_id = :cid
            """),
            {"cid": company_id},
        ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="업체를 찾을 수 없습니다")

        # onboarding_status → rejected + notes에 사유 기록
        conn.execute(
            text(f"""
                UPDATE {SCHEMA}.companies
                SET onboarding_status = 'rejected',
                    notes = :reason,
                    updated_at = now()
                WHERE company_id = :cid
            """),
            {"cid": company_id, "reason": reason},
        )

        # MV REFRESH — SAVEPOINT로 격리
        nested = conn.begin_nested()
        try:
            _refresh_mv(conn)
            nested.commit()
        except Exception:
            nested.rollback()
            logger.exception("MV refresh 실패 — 후속 작업은 계속 진행")

        # 알림: supplier에게 onboarding_rejected
        _create_notification(
            conn,
            recipient_type="supplier",
            recipient_id=company_id,
            event_type="onboarding_rejected",
            title="업체 등록이 반려되었습니다",
            message=f"반려 사유: {reason}" if reason else "업체 등록이 반려되었습니다. 관리자에게 문의해 주세요.",
            ref_id=company_id,
            ref_type="company",
        )

    return {
        "success": True,
        "company_id": company_id,
        "onboarding_status": "rejected",
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# E-2: GET /api/admin/rfqs — 전체 RFQ 현황
# ---------------------------------------------------------------------------


@router.get("/api/admin/rfqs")
def get_admin_rfqs(
    status: str = None,
    admin: dict = Depends(get_current_admin),
):
    """전체 RFQ 현황 (rfqs + buyers JOIN). status 쿼리 파라미터로 필터 가능."""
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        if status:
            rows = conn.execute(
                text(f"""
                    SELECT r.rfq_id, b.buyer_name, r.status,
                           r.requested_delivery_date, r.created_at
                    FROM {SCHEMA}.rfqs r
                    LEFT JOIN {SCHEMA}.buyers b ON r.buyer_id = b.buyer_id
                    WHERE r.status = :status
                    ORDER BY r.created_at DESC
                """),
                {"status": status},
            ).fetchall()
        else:
            rows = conn.execute(
                text(f"""
                    SELECT r.rfq_id, b.buyer_name, r.status,
                           r.requested_delivery_date, r.created_at
                    FROM {SCHEMA}.rfqs r
                    LEFT JOIN {SCHEMA}.buyers b ON r.buyer_id = b.buyer_id
                    ORDER BY r.created_at DESC
                """),
            ).fetchall()

    return [
        {
            "rfq_id": str(r[0]),
            "buyer_name": r[1],
            "status": r[2],
            "requested_delivery_date": str(r[3]) if r[3] else None,
            "created_at": str(r[4]),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# E-2: GET /api/admin/orders — 전체 발주 현황
# ---------------------------------------------------------------------------


@router.get("/api/admin/orders")
def get_admin_orders(
    status: str = None,
    admin: dict = Depends(get_current_admin),
):
    """전체 발주 현황 (orders + companies + buyers JOIN). status 쿼리 파라미터로 필터 가능."""
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        if status:
            rows = conn.execute(
                text(f"""
                    SELECT o.order_id, c.company_name, b.buyer_name,
                           o.status, o.total_price, o.created_at
                    FROM {SCHEMA}.orders o
                    JOIN {SCHEMA}.companies c ON o.company_id = c.company_id
                    LEFT JOIN {SCHEMA}.buyers b ON o.buyer_id = b.buyer_id
                    WHERE o.status = :status
                    ORDER BY o.created_at DESC
                """),
                {"status": status},
            ).fetchall()
        else:
            rows = conn.execute(
                text(f"""
                    SELECT o.order_id, c.company_name, b.buyer_name,
                           o.status, o.total_price, o.created_at
                    FROM {SCHEMA}.orders o
                    JOIN {SCHEMA}.companies c ON o.company_id = c.company_id
                    LEFT JOIN {SCHEMA}.buyers b ON o.buyer_id = b.buyer_id
                    ORDER BY o.created_at DESC
                """),
            ).fetchall()

    return [
        {
            "order_id": str(r[0]),
            "company_name": r[1],
            "buyer_name": r[2],
            "status": r[3],
            "total_price": float(r[4]) if r[4] is not None else None,
            "created_at": str(r[5]),
        }
        for r in rows
    ]


@router.post("/api/admin/mv/refresh")
def admin_refresh_mv(admin: dict = Depends(get_current_admin)):
    """admin 전용 — company_capability_summary MV 강제 refresh. 진단 / 격차 시정용."""
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
    try:
        with engine.begin() as conn:
            _refresh_mv(conn)
    except Exception as exc:
        logger.exception("MV refresh 실패")
        raise HTTPException(status_code=500, detail=f"MV refresh fail: {exc}")
    return {"status": "refreshed", "mv": "company_capability_summary"}


# ---------------------------------------------------------------------------
# admin 진단 endpoint — MV 영역 진입 부재 격차 직접 점검
# ---------------------------------------------------------------------------


@router.get("/api/admin/mv/inspect/{company_id}")
def admin_inspect_company(company_id: str, admin: dict = Depends(get_current_admin)):
    """단일 company_id 영역의 MV 진입 조건 + MV row + 원본 테이블 row 동시 dump.

    진단 항목:
      - companies (status / onboarding_status / accepting_orders / business_registration_no)
      - company_sites (primary site 영역의 region NOT NULL)
      - company_material_capabilities (row 수 + capability_level 영역)
      - company_process_capabilities (row 수 + service_mode 영역)
      - equipment (row 수 + status='running'|'idle' 영역)
      - company_availability_snapshot (overall_status)
      - MV row 존재 여부
      - _check_onboarding 4 조건 (has_brn / has_equip / has_mat / has_region)
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        # companies 본체
        comp_row = conn.execute(
            text(f"""
                SELECT company_id, company_name, login_id, status, onboarding_status,
                       accepting_orders, business_registration_no, created_at
                FROM {SCHEMA}.companies
                WHERE company_id = CAST(:cid AS uuid)
            """),
            {"cid": company_id},
        ).fetchone()
        if comp_row is None:
            raise HTTPException(status_code=404, detail="업체를 찾을 수 없습니다")

        # company_sites primary
        site_row = conn.execute(
            text(f"""
                SELECT site_id, site_name, is_primary, region, city
                FROM {SCHEMA}.company_sites
                WHERE company_id = CAST(:cid AS uuid) AND is_primary = true
                LIMIT 1
            """),
            {"cid": company_id},
        ).fetchone()

        # company_material_capabilities
        mat_rows = conn.execute(
            text(f"""
                SELECT scope_type, material_id, material_category_code,
                       capability_level, is_active
                FROM {SCHEMA}.company_material_capabilities
                WHERE company_id = CAST(:cid AS uuid)
            """),
            {"cid": company_id},
        ).fetchall()

        # company_process_capabilities
        proc_rows = conn.execute(
            text(f"""
                SELECT process_code, service_mode, capability_level, is_active
                FROM {SCHEMA}.company_process_capabilities
                WHERE company_id = CAST(:cid AS uuid)
            """),
            {"cid": company_id},
        ).fetchall()

        # equipment
        eq_rows = conn.execute(
            text(f"""
                SELECT equipment_id, equipment_category_code, display_name, status,
                       max_x_travel_mm, max_y_travel_mm, max_z_travel_mm,
                       max_turning_diameter_mm, max_turning_length_mm
                FROM {SCHEMA}.equipment
                WHERE company_id = CAST(:cid AS uuid)
            """),
            {"cid": company_id},
        ).fetchall()

        # company_availability_snapshot
        av_row = conn.execute(
            text(f"""
                SELECT overall_status, next_available_date, last_updated_at
                FROM {SCHEMA}.company_availability_snapshot
                WHERE company_id = CAST(:cid AS uuid)
            """),
            {"cid": company_id},
        ).fetchone()

        # MV row 존재 여부
        mv_row = conn.execute(
            text(f"""
                SELECT company_id, company_name,
                       material_codes, material_category_codes,
                       process_codes, inhouse_process_codes, outsourced_process_codes,
                       max_x_mm, max_y_mm, max_z_mm,
                       max_turning_diameter_mm, max_turning_length_mm,
                       best_it_grade, best_ra_um, active_equipment_count,
                       overall_status, next_available_date
                FROM {SCHEMA}.company_capability_summary
                WHERE company_id = CAST(:cid AS uuid)
            """),
            {"cid": company_id},
        ).fetchone()

        # _check_onboarding 4 조건 재계산
        ob_row = conn.execute(
            text(f"""
                SELECT
                    (SELECT business_registration_no FROM {SCHEMA}.companies WHERE company_id = CAST(:cid AS uuid)) IS NOT NULL AS has_brn,
                    (SELECT count(*) FROM {SCHEMA}.equipment WHERE company_id = CAST(:cid AS uuid)) > 0 AS has_equip,
                    (SELECT count(*) FROM {SCHEMA}.company_material_capabilities WHERE company_id = CAST(:cid AS uuid)) > 0 AS has_mat,
                    (SELECT region FROM {SCHEMA}.company_sites WHERE company_id = CAST(:cid AS uuid) AND is_primary = true) IS NOT NULL AS has_region
            """),
            {"cid": company_id},
        ).fetchone()

    # WHERE 조건 통과 여부 분해
    mv_where_pass = {
        "status_active": comp_row[3] == "active",
        "onboarding_verified": comp_row[4] == "verified",
        "accepting_orders_true": bool(comp_row[5]),
        "all_pass": (
            comp_row[3] == "active"
            and comp_row[4] == "verified"
            and bool(comp_row[5])
        ),
    }

    return {
        "company": {
            "company_id": str(comp_row[0]),
            "company_name": comp_row[1],
            "login_id": comp_row[2],
            "status": comp_row[3],
            "onboarding_status": comp_row[4],
            "accepting_orders": comp_row[5],
            "business_registration_no": comp_row[6],
            "created_at": str(comp_row[7]),
        },
        "primary_site": (
            {
                "site_id": str(site_row[0]),
                "site_name": site_row[1],
                "is_primary": site_row[2],
                "region": site_row[3],
                "city": site_row[4],
            }
            if site_row
            else None
        ),
        "material_capabilities": [
            {
                "scope_type": r[0],
                "material_id": str(r[1]) if r[1] else None,
                "material_category_code": r[2],
                "capability_level": r[3],
                "is_active": r[4],
            }
            for r in mat_rows
        ],
        "process_capabilities": [
            {
                "process_code": r[0],
                "service_mode": r[1],
                "capability_level": r[2],
                "is_active": r[3],
            }
            for r in proc_rows
        ],
        "equipment": [
            {
                "equipment_id": str(r[0]),
                "equipment_category_code": r[1],
                "display_name": r[2],
                "status": r[3],
                "max_x_travel_mm": float(r[4]) if r[4] is not None else None,
                "max_y_travel_mm": float(r[5]) if r[5] is not None else None,
                "max_z_travel_mm": float(r[6]) if r[6] is not None else None,
                "max_turning_diameter_mm": float(r[7]) if r[7] is not None else None,
                "max_turning_length_mm": float(r[8]) if r[8] is not None else None,
            }
            for r in eq_rows
        ],
        "availability_snapshot": (
            {
                "overall_status": av_row[0],
                "next_available_date": str(av_row[1]) if av_row[1] else None,
                "last_updated_at": str(av_row[2]) if av_row[2] else None,
            }
            if av_row
            else None
        ),
        "mv_where_pass": mv_where_pass,
        "onboarding_conditions": {
            "has_brn": bool(ob_row[0]) if ob_row else False,
            "has_equip": bool(ob_row[1]) if ob_row else False,
            "has_mat": bool(ob_row[2]) if ob_row else False,
            "has_region": bool(ob_row[3]) if ob_row else False,
            "all_pass": bool(ob_row and all([ob_row[0], ob_row[1], ob_row[2], ob_row[3]])),
        },
        "mv_row_present": mv_row is not None,
        "mv_row": (
            {
                "company_id": str(mv_row[0]),
                "company_name": mv_row[1],
                "material_codes": list(mv_row[2]) if mv_row[2] else [],
                "material_category_codes": list(mv_row[3]) if mv_row[3] else [],
                "process_codes": list(mv_row[4]) if mv_row[4] else [],
                "inhouse_process_codes": list(mv_row[5]) if mv_row[5] else [],
                "outsourced_process_codes": list(mv_row[6]) if mv_row[6] else [],
                "max_x_mm": float(mv_row[7]) if mv_row[7] is not None else None,
                "max_y_mm": float(mv_row[8]) if mv_row[8] is not None else None,
                "max_z_mm": float(mv_row[9]) if mv_row[9] is not None else None,
                "max_turning_diameter_mm": float(mv_row[10]) if mv_row[10] is not None else None,
                "max_turning_length_mm": float(mv_row[11]) if mv_row[11] is not None else None,
                "best_it_grade": int(mv_row[12]) if mv_row[12] is not None else None,
                "best_ra_um": float(mv_row[13]) if mv_row[13] is not None else None,
                "active_equipment_count": int(mv_row[14]) if mv_row[14] is not None else 0,
                "overall_status": mv_row[15],
                "next_available_date": str(mv_row[16]) if mv_row[16] else None,
            }
            if mv_row
            else None
        ),
    }


# ---------------------------------------------------------------------------
# admin 시정 endpoint — accepting_orders / overall_status / MV refresh 일괄
# ---------------------------------------------------------------------------


@router.post("/api/admin/mv/repair/{company_id}")
def admin_repair_company(company_id: str, admin: dict = Depends(get_current_admin)):
    """단일 company_id 영역의 MV 진입 격차 일괄 시정.

    조치:
      1. companies.accepting_orders = true (NULL/false 영역 보정)
      2. company_availability_snapshot 영역 row 부재 시 INSERT (overall_status='available')
         + 영역 row 존재하나 overall_status='unknown'/NULL 영역 → 'available' 로 보정
      3. _refresh_mv 호출
      4. 시정 후 MV row 존재 여부 반환
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.begin() as conn:
        comp_row = conn.execute(
            text(f"""
                SELECT company_id FROM {SCHEMA}.companies
                WHERE company_id = CAST(:cid AS uuid)
            """),
            {"cid": company_id},
        ).fetchone()
        if comp_row is None:
            raise HTTPException(status_code=404, detail="업체를 찾을 수 없습니다")

        # 1) accepting_orders 보정
        conn.execute(
            text(f"""
                UPDATE {SCHEMA}.companies
                SET accepting_orders = true, updated_at = now()
                WHERE company_id = CAST(:cid AS uuid)
                  AND (accepting_orders IS NULL OR accepting_orders = false)
            """),
            {"cid": company_id},
        )

        # 2) availability snapshot INSERT or UPDATE
        # NULL IN (...) 영역은 UNKNOWN 평가 → COALESCE 로 NULL 영역 'unknown' 정합 후 IN 비교
        conn.execute(
            text(f"""
                INSERT INTO {SCHEMA}.company_availability_snapshot
                    (company_id, overall_status)
                VALUES (CAST(:cid AS uuid), 'available')
                ON CONFLICT (company_id) DO UPDATE SET
                    overall_status = CASE
                        WHEN COALESCE({SCHEMA}.company_availability_snapshot.overall_status, 'unknown') = 'unknown'
                            THEN 'available'
                        ELSE {SCHEMA}.company_availability_snapshot.overall_status
                    END,
                    last_updated_at = now()
            """),
            {"cid": company_id},
        )

        # 3) MV refresh — SAVEPOINT 격리
        nested = conn.begin_nested()
        try:
            _refresh_mv(conn)
            nested.commit()
            mv_refresh_ok = True
        except Exception as exc:
            nested.rollback()
            logger.exception("MV refresh 실패")
            mv_refresh_ok = False

        # 4) 시정 후 MV row 확인
        mv_present = conn.execute(
            text(f"""
                SELECT 1 FROM {SCHEMA}.company_capability_summary
                WHERE company_id = CAST(:cid AS uuid)
            """),
            {"cid": company_id},
        ).fetchone()

    return {
        "success": True,
        "company_id": company_id,
        "mv_refresh_ok": mv_refresh_ok,
        "mv_row_present": mv_present is not None,
    }
