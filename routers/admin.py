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
