"""
발주 엔드포인트:
- POST /api/orders                        (B-6: 업체 선택 + 발주 확정)
- GET  /api/orders/{order_id}             (B-6: 발주 상세 조회)
- PUT  /api/orders/{order_id}/status      (B-7: 상태 전이)
- POST /api/jobs                          (C-1: manufacturing_job 생성)
- PUT  /api/jobs/{job_id}/progress        (C-1: 가공 진행도 업데이트)
- GET  /api/orders/{order_id}/jobs        (C-1: 발주 건 진행도 조회)
- PUT  /api/orders/{order_id}/inspect     (C-1 추가: 발주자 검수)
- POST /api/jobs/{job_id}/images          (C-2: 납품 이미지 업로드)
- GET  /api/jobs/{job_id}/images          (C-2: 납품 이미지 조회)
- POST /api/orders/{order_id}/shipment    (C-3: 배송 정보 등록)
- GET  /api/orders/{order_id}/shipment    (C-3: 배송 정보 조회)
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy import text

from routers.deps import engine, SCHEMA, get_current_user, _create_notification

# 납품 이미지 저장 경로
DELIVERY_UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads" / "delivery"

router = APIRouter()


# ---------------------------------------------------------------------------
# B-6: POST /api/orders — 업체 선택 + 발주 확정
# ---------------------------------------------------------------------------


@router.post("/api/orders")
def create_order(data: dict, user: dict = Depends(get_current_user)):
    """quote_id 받아서 order 생성. 선택된 quote→accepted, 나머지→rejected, rfq→ordered"""
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    # buyer-only 엔드포인트
    if user["role"] != "buyer":
        raise HTTPException(status_code=403, detail="발주는 buyer만 할 수 있습니다")

    quote_id = data.get("quote_id")
    if not quote_id:
        raise HTTPException(status_code=400, detail="quote_id가 필요합니다")

    with engine.begin() as conn:
        # quote_responses에서 정보 조회
        q_row = conn.execute(
            text(f"""
                SELECT qr.quote_id, qr.rfq_id, qr.company_id, qr.total_price,
                       qr.proposed_delivery_date, qr.status,
                       r.buyer_id, c.company_name
                FROM {SCHEMA}.quote_responses qr
                JOIN {SCHEMA}.rfqs r ON qr.rfq_id = r.rfq_id
                JOIN {SCHEMA}.companies c ON qr.company_id = c.company_id
                WHERE qr.quote_id = :qid
            """),
            {"qid": quote_id},
        ).fetchone()

        if q_row is None:
            raise HTTPException(status_code=404, detail="견적을 찾을 수 없습니다")

        rfq_id = q_row[1]
        company_id = q_row[2]
        total_price = q_row[3]
        proposed_delivery_date = q_row[4]
        quote_status = q_row[5]
        buyer_id = q_row[6]
        company_name = q_row[7]

        # 소유권 검증: buyer만 발주 가능
        if user["role"] == "buyer" and user["id"] != str(buyer_id):
            raise HTTPException(status_code=403, detail="본인의 RFQ에 대해서만 발주할 수 있습니다")

        # 견적 상태 검증
        if quote_status not in ("submitted", "draft"):
            raise HTTPException(
                status_code=400,
                detail=f"'{quote_status}' 상태의 견적으로는 발주할 수 없습니다",
            )

        # orders INSERT
        order_row = conn.execute(
            text(f"""
                INSERT INTO {SCHEMA}.orders
                    (quote_id, rfq_id, buyer_id, company_id,
                     status, total_price, promised_delivery_date)
                VALUES (:qid, :rid, :bid, :cid,
                        'contracting', :price, :delivery_date)
                RETURNING order_id, status, created_at
            """),
            {
                "qid": quote_id,
                "rid": rfq_id,
                "bid": buyer_id,
                "cid": company_id,
                "price": total_price,
                "delivery_date": proposed_delivery_date,
            },
        ).fetchone()
        order_id = order_row[0]

        # 선택된 quote → accepted
        conn.execute(
            text(f"""
                UPDATE {SCHEMA}.quote_responses
                SET status = 'accepted', updated_at = now()
                WHERE quote_id = :qid
            """),
            {"qid": quote_id},
        )

        # 같은 rfq의 나머지 quotes → rejected
        conn.execute(
            text(f"""
                UPDATE {SCHEMA}.quote_responses
                SET status = 'rejected', updated_at = now()
                WHERE rfq_id = :rid AND quote_id != :qid
                  AND status NOT IN ('rejected', 'withdrawn', 'expired')
            """),
            {"rid": rfq_id, "qid": quote_id},
        )

        # rfqs.status → ordered
        conn.execute(
            text(f"""
                UPDATE {SCHEMA}.rfqs
                SET status = 'ordered', updated_at = now()
                WHERE rfq_id = :rid
            """),
            {"rid": rfq_id},
        )

        # 알림: supplier에게 order_confirmed
        _create_notification(
            conn,
            recipient_type="supplier",
            recipient_id=str(company_id),
            event_type="order_confirmed",
            title="발주가 확정되었습니다",
            message=f"RFQ {rfq_id}에 대한 발주가 확정되었습니다. 계약 절차를 진행해 주세요.",
            ref_id=str(order_id),
            ref_type="order",
        )

    return {
        "order_id": str(order_id),
        "status": "contracting",
        "company_name": company_name,
        "total_price": float(total_price) if total_price is not None else None,
    }


# ---------------------------------------------------------------------------
# B-6: GET /api/orders/{order_id} — 발주 상세 조회
# ---------------------------------------------------------------------------


@router.get("/api/orders/{order_id}")
def get_order_detail(order_id: str, current_user: dict = Depends(get_current_user)):
    """orders + companies + rfqs JOIN 반환"""
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        row = conn.execute(
            text(f"""
                SELECT
                    o.order_id, o.status, o.total_price, o.currency_code,
                    o.promised_delivery_date, o.actual_delivery_date,
                    o.production_start_date,
                    o.nda_signed_at, o.contract_signed_at,
                    o.created_at, o.updated_at,
                    c.company_id, c.company_name,
                    r.rfq_id, r.status AS rfq_status,
                    o.buyer_id, o.quote_id
                FROM {SCHEMA}.orders o
                JOIN {SCHEMA}.companies c ON o.company_id = c.company_id
                LEFT JOIN {SCHEMA}.rfqs r ON o.rfq_id = r.rfq_id
                WHERE o.order_id = :oid
            """),
            {"oid": order_id},
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="발주를 찾을 수 없습니다")

    # 소유권 검증: buyer 또는 해당 supplier만 조회 가능
    order_buyer_id = str(row[15]) if row[15] else None
    order_company_id = str(row[11])
    if current_user["role"] == "buyer" and current_user["id"] != order_buyer_id:
        raise HTTPException(status_code=403, detail="본인의 발주만 조회할 수 있습니다")
    if current_user["role"] == "supplier" and current_user["id"] != order_company_id:
        raise HTTPException(status_code=403, detail="본인에게 배정된 발주만 조회할 수 있습니다")

    return {
        "order_id": str(row[0]),
        "status": row[1],
        "total_price": float(row[2]) if row[2] is not None else None,
        "currency_code": row[3],
        "promised_delivery_date": str(row[4]) if row[4] else None,
        "actual_delivery_date": str(row[5]) if row[5] else None,
        "production_start_date": str(row[6]) if row[6] else None,
        "nda_signed_at": str(row[7]) if row[7] else None,
        "contract_signed_at": str(row[8]) if row[8] else None,
        "created_at": str(row[9]),
        "updated_at": str(row[10]),
        "company_id": str(row[11]),
        "company_name": row[12],
        "rfq_id": str(row[13]) if row[13] else None,
        "rfq_status": row[14],
        "buyer_id": str(row[15]) if row[15] else None,
        "quote_id": str(row[16]) if row[16] else None,
    }


# ---------------------------------------------------------------------------
# B-7: PUT /api/orders/{order_id}/status — Order 상태 전이
# ---------------------------------------------------------------------------

# 상태 전이 권한 매트릭스: {현재상태: {대상상태: 허용역할집합}}
_ORDER_TRANSITIONS = {
    "contracting": {
        "ordered":       {"buyer", "supplier"},
        "cancelled":     {"buyer", "admin"},
    },
    "ordered": {
        "in_production": {"supplier"},
        "cancelled":     {"buyer", "admin"},
    },
    "in_production": {
        "inspection":    {"supplier"},
        "cancelled":     {"buyer", "admin"},
        "disputed":      {"buyer", "supplier"},
    },
    "inspection": {
        "shipped":       {"supplier"},
        "in_production": {"buyer"},       # 검수 불합격 → 재작업
        "disputed":      {"buyer", "supplier"},
    },
    "shipped": {
        "delivered":     {"buyer"},
        "disputed":      {"buyer", "supplier"},
    },
    "delivered": {
        "completed":     {"buyer"},
        "disputed":      {"buyer", "supplier"},
    },
}


@router.put("/api/orders/{order_id}/status")
def update_order_status(order_id: str, data: dict,
                        user: dict = Depends(get_current_user)):
    """Order 상태 전이. cancelled 시 연쇄 처리 포함."""
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    new_status = data.get("status")
    if not new_status:
        raise HTTPException(status_code=400, detail="status 필드가 필요합니다")

    with engine.begin() as conn:
        # 현재 상태 + 소유권 정보 조회
        order_row = conn.execute(
            text(f"""
                SELECT status, buyer_id, company_id, rfq_id
                FROM {SCHEMA}.orders
                WHERE order_id = :oid
            """),
            {"oid": order_id},
        ).fetchone()

        if order_row is None:
            raise HTTPException(status_code=404, detail="발주를 찾을 수 없습니다")

        current_status = order_row[0]
        buyer_id = str(order_row[1]) if order_row[1] else None
        company_id = str(order_row[2])
        rfq_id = str(order_row[3]) if order_row[3] else None

        # 소유권 검증 (buyer 또는 supplier만 변경 가능)
        user_role = user["role"]
        user_id = user["id"]
        if user_role == "buyer" and user_id != buyer_id:
            raise HTTPException(status_code=403, detail="본인의 발주만 상태를 변경할 수 있습니다")
        if user_role == "supplier" and user_id != company_id:
            raise HTTPException(status_code=403, detail="본인에게 배정된 발주만 상태를 변경할 수 있습니다")

        # 전이 허용 검증
        transitions = _ORDER_TRANSITIONS.get(current_status, {})
        allowed_roles = transitions.get(new_status)

        if allowed_roles is None:
            allowed_targets = list(transitions.keys()) if transitions else []
            raise HTTPException(
                status_code=400,
                detail=f"'{current_status}' 상태에서 '{new_status}'(으)로 전이할 수 없습니다. "
                       f"허용: {sorted(allowed_targets) if allowed_targets else '없음'}",
            )

        if user_role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"'{user_role}' 역할은 '{current_status}'→'{new_status}' 전이 권한이 없습니다",
            )

        # 상태 변경
        conn.execute(
            text(f"""
                UPDATE {SCHEMA}.orders
                SET status = :new_status, updated_at = now()
                WHERE order_id = :oid
            """),
            {"oid": order_id, "new_status": new_status},
        )

        # cancelled 시 연쇄 처리
        if new_status == "cancelled":
            # manufacturing_jobs → cancelled
            try:
                conn.execute(
                    text(f"""
                        UPDATE {SCHEMA}.manufacturing_jobs
                        SET job_status = 'cancelled'
                        WHERE order_id = :oid
                    """),
                    {"oid": order_id},
                )
            except Exception:
                # job_status 컬럼이 아직 없을 수 있음
                pass

            # 알림: buyer + supplier 에게 order_cancelled
            _create_notification(
                conn,
                recipient_type="buyer",
                recipient_id=buyer_id,
                event_type="order_cancelled",
                title="발주가 취소되었습니다",
                message=f"발주 {order_id}가 취소되었습니다.",
                ref_id=str(order_id),
                ref_type="order",
            )
            _create_notification(
                conn,
                recipient_type="supplier",
                recipient_id=company_id,
                event_type="order_cancelled",
                title="발주가 취소되었습니다",
                message=f"발주 {order_id}가 취소되었습니다.",
                ref_id=str(order_id),
                ref_type="order",
            )

    return {
        "success": True,
        "order_id": order_id,
        "previous_status": current_status,
        "new_status": new_status,
    }


# ===========================================================================
# Phase C-1: POST /api/jobs — manufacturing_job 생성
# ===========================================================================


@router.post("/api/jobs")
def create_job(data: dict, user: dict = Depends(get_current_user)):
    """order_id 기반으로 manufacturing_job 생성. supplier만 가능."""
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    # supplier-only 엔드포인트
    if user["role"] != "supplier":
        raise HTTPException(status_code=403, detail="작업 생성은 supplier만 할 수 있습니다")

    order_id = data.get("order_id")
    part_name = data.get("part_name")
    material_raw_text = data.get("material_raw_text")
    quantity = data.get("quantity", 1)

    if not order_id:
        raise HTTPException(status_code=400, detail="order_id가 필요합니다")

    with engine.begin() as conn:
        # order 존재 + company_id 조회
        order_row = conn.execute(
            text(f"""
                SELECT company_id, buyer_id, status
                FROM {SCHEMA}.orders
                WHERE order_id = :oid
            """),
            {"oid": order_id},
        ).fetchone()

        if order_row is None:
            raise HTTPException(status_code=404, detail="발주를 찾을 수 없습니다")

        company_id = str(order_row[0])
        buyer_id = str(order_row[1]) if order_row[1] else None

        # 소유권 검증: supplier만 job 생성 가능
        if user["role"] == "supplier" and user["id"] != company_id:
            raise HTTPException(status_code=403, detail="본인에게 배정된 발주에만 작업을 생성할 수 있습니다")

        # manufacturing_jobs INSERT (job_status는 DDL에 아직 없을 수 있음)
        try:
            job_row = conn.execute(
                text(f"""
                    INSERT INTO {SCHEMA}.manufacturing_jobs
                        (order_id, company_id, part_name, material_raw_text,
                         quantity, job_status)
                    VALUES (:oid, :cid, :pname, :mtext, :qty, 'pending')
                    RETURNING job_id, created_at
                """),
                {
                    "oid": order_id,
                    "cid": company_id,
                    "pname": part_name,
                    "mtext": material_raw_text,
                    "qty": quantity,
                },
            ).fetchone()
        except Exception:
            # job_status 컬럼이 없는 경우 job_status 없이 INSERT
            job_row = conn.execute(
                text(f"""
                    INSERT INTO {SCHEMA}.manufacturing_jobs
                        (order_id, company_id, part_name, material_raw_text,
                         quantity)
                    VALUES (:oid, :cid, :pname, :mtext, :qty)
                    RETURNING job_id, created_at
                """),
                {
                    "oid": order_id,
                    "cid": company_id,
                    "pname": part_name,
                    "mtext": material_raw_text,
                    "qty": quantity,
                },
            ).fetchone()

    return {
        "job_id": str(job_row[0]),
        "order_id": order_id,
        "job_status": "pending",
        "created_at": str(job_row[1]),
    }


# ===========================================================================
# Phase C-1: PUT /api/jobs/{job_id}/progress — 가공 진행도 업데이트
# ===========================================================================

_VALID_JOB_STATUSES = {
    "pending", "material_received", "in_progress", "post_processing",
    "inspection", "packaging", "completed", "cancelled",
}


@router.put("/api/jobs/{job_id}/progress")
def update_job_progress(job_id: str, data: dict,
                        user: dict = Depends(get_current_user)):
    """manufacturing_jobs.job_status 업데이트. supplier만 가능. buyer에게 알림."""
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    new_status = data.get("job_status")
    if not new_status or new_status not in _VALID_JOB_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"job_status 필드가 필요합니다. 허용값: {sorted(_VALID_JOB_STATUSES)}",
        )

    with engine.begin() as conn:
        # job 존재 + 소유권 조회
        job_row = conn.execute(
            text(f"""
                SELECT mj.company_id, mj.order_id, o.buyer_id
                FROM {SCHEMA}.manufacturing_jobs mj
                LEFT JOIN {SCHEMA}.orders o ON mj.order_id = o.order_id
                WHERE mj.job_id = :jid
            """),
            {"jid": job_id},
        ).fetchone()

        if job_row is None:
            raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다")

        company_id = str(job_row[0])
        order_id = str(job_row[1]) if job_row[1] else None
        buyer_id = str(job_row[2]) if job_row[2] else None

        # 소유권 검증
        if user["role"] == "supplier" and user["id"] != company_id:
            raise HTTPException(status_code=403, detail="본인의 작업만 진행도를 변경할 수 있습니다")

        # job_status UPDATE (컬럼 미존재 시 안전 처리)
        try:
            conn.execute(
                text(f"""
                    UPDATE {SCHEMA}.manufacturing_jobs
                    SET job_status = :status
                    WHERE job_id = :jid
                """),
                {"jid": job_id, "status": new_status},
            )
        except Exception:
            raise HTTPException(
                status_code=500,
                detail="job_status 컬럼이 DDL에 아직 추가되지 않았습니다",
            )

        # completed 시 completed_at 기록
        if new_status == "completed":
            try:
                conn.execute(
                    text(f"""
                        UPDATE {SCHEMA}.manufacturing_jobs
                        SET completed_at = now()
                        WHERE job_id = :jid
                    """),
                    {"jid": job_id},
                )
            except Exception:
                pass

        # 알림: buyer에게 production_update
        if buyer_id:
            _create_notification(
                conn,
                recipient_type="buyer",
                recipient_id=buyer_id,
                event_type="production_update",
                title="생산 진행도가 업데이트되었습니다",
                message=f"작업 {job_id}의 상태가 '{new_status}'(으)로 변경되었습니다.",
                ref_id=job_id,
                ref_type="job",
            )

    return {
        "job_id": job_id,
        "job_status": new_status,
        "updated": True,
    }


# ===========================================================================
# Phase C-1: GET /api/orders/{order_id}/jobs — 발주 건 진행도 조회
# ===========================================================================


@router.get("/api/orders/{order_id}/jobs")
def get_order_jobs(order_id: str, user: dict = Depends(get_current_user)):
    """manufacturing_jobs + job_processes JOIN 반환. 발주 당사자(buyer/supplier) + admin만."""
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        # 소유권 검증
        owner_row = conn.execute(
            text(f"SELECT buyer_id, company_id FROM {SCHEMA}.orders WHERE order_id = CAST(:oid AS uuid)"),
            {"oid": order_id},
        ).fetchone()
        if owner_row is None:
            raise HTTPException(status_code=404, detail="발주를 찾을 수 없습니다")
        o_buyer_id = str(owner_row[0]) if owner_row[0] else None
        o_company_id = str(owner_row[1]) if owner_row[1] else None
        role = user["role"]
        uid = user["id"]
        if not (role == "admin"
                or (role == "buyer" and o_buyer_id == uid)
                or (role == "supplier" and o_company_id == uid)):
            raise HTTPException(status_code=403, detail="발주 당사자만 조회할 수 있습니다")

        # job_status 컬럼 포함 시도
        try:
            job_rows = conn.execute(
                text(f"""
                    SELECT mj.job_id, mj.part_name, mj.job_status,
                           mj.quality_status, mj.material_raw_text,
                           mj.quantity, mj.created_at
                    FROM {SCHEMA}.manufacturing_jobs mj
                    WHERE mj.order_id = :oid
                    ORDER BY mj.created_at
                """),
                {"oid": order_id},
            ).fetchall()
        except Exception:
            # job_status 컬럼 없으면 해당 컬럼 제외
            job_rows = conn.execute(
                text(f"""
                    SELECT mj.job_id, mj.part_name, NULL AS job_status,
                           mj.quality_status, mj.material_raw_text,
                           mj.quantity, mj.created_at
                    FROM {SCHEMA}.manufacturing_jobs mj
                    WHERE mj.order_id = :oid
                    ORDER BY mj.created_at
                """),
                {"oid": order_id},
            ).fetchall()

        result = []
        for jr in job_rows:
            job_id = str(jr[0])

            # job_processes 조회
            proc_rows = conn.execute(
                text(f"""
                    SELECT jp.process_code, jp.sequence_order, jp.was_outsourced
                    FROM {SCHEMA}.job_processes jp
                    WHERE jp.job_id = :jid
                    ORDER BY jp.sequence_order
                """),
                {"jid": job_id},
            ).fetchall()

            processes = [
                {
                    "process_code": pr[0],
                    "sequence_order": pr[1],
                    "was_outsourced": pr[2],
                }
                for pr in proc_rows
            ]

            result.append({
                "job_id": job_id,
                "part_name": jr[1],
                "job_status": jr[2],
                "quality_status": jr[3],
                "material_raw_text": jr[4],
                "quantity": jr[5],
                "created_at": str(jr[6]),
                "processes": processes,
            })

    return result


# ===========================================================================
# Phase C-1 추가: PUT /api/orders/{order_id}/inspect — 발주자 검수
# ===========================================================================


@router.put("/api/orders/{order_id}/inspect")
def inspect_order(order_id: str, data: dict,
                  user: dict = Depends(get_current_user)):
    """
    발주자 검수: pass/fail/rework.
    - pass   → orders.status → completed, quality_status='pass'
    - fail   → orders.status → disputed, quality_status='fail'
    - rework → orders.status → in_production, quality_status='fail', rework_count +1
    inspection, shipped, delivered 상태에서 호출 가능.
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    # buyer-only 엔드포인트
    if user["role"] != "buyer":
        raise HTTPException(status_code=403, detail="검수는 buyer만 할 수 있습니다")

    result_val = data.get("result")
    notes = data.get("notes", "")

    if result_val not in ("pass", "fail", "rework"):
        raise HTTPException(
            status_code=400,
            detail="result 필드는 'pass', 'fail', 'rework' 중 하나여야 합니다",
        )

    with engine.begin() as conn:
        # order 조회
        order_row = conn.execute(
            text(f"""
                SELECT status, buyer_id, company_id
                FROM {SCHEMA}.orders
                WHERE order_id = :oid
            """),
            {"oid": order_id},
        ).fetchone()

        if order_row is None:
            raise HTTPException(status_code=404, detail="발주를 찾을 수 없습니다")

        current_status = order_row[0]
        buyer_id = str(order_row[1]) if order_row[1] else None
        company_id = str(order_row[2])

        # 소유권 검증: buyer만 검수 가능
        if user["role"] == "buyer" and user["id"] != buyer_id:
            raise HTTPException(status_code=403, detail="본인의 발주만 검수할 수 있습니다")

        # 상태 검증: inspection 상태에서만 검수 가능
        if current_status not in ("inspection", "shipped", "delivered"):
            raise HTTPException(
                status_code=400,
                detail=f"'{current_status}' 상태에서는 검수할 수 없습니다. "
                       f"'inspection' 상태에서만 가능합니다.",
            )

        # result에 따라 상태 전이 결정
        if result_val == "pass":
            new_status = "completed"
            quality_status = "pass"
        elif result_val == "fail":
            new_status = "disputed"
            quality_status = "fail"
        else:  # rework
            new_status = "in_production"
            quality_status = "fail"

        # orders.status 업데이트
        conn.execute(
            text(f"""
                UPDATE {SCHEMA}.orders
                SET status = :new_status, updated_at = now()
                WHERE order_id = :oid
            """),
            {"oid": order_id, "new_status": new_status},
        )

        # manufacturing_jobs quality_status 일괄 업데이트
        try:
            conn.execute(
                text(f"""
                    UPDATE {SCHEMA}.manufacturing_jobs
                    SET quality_status = :qs
                    WHERE order_id = :oid
                """),
                {"oid": order_id, "qs": quality_status},
            )
        except Exception:
            pass

        # rework 시 rework_count +1
        if result_val == "rework":
            try:
                conn.execute(
                    text(f"""
                        UPDATE {SCHEMA}.manufacturing_jobs
                        SET rework_count = rework_count + 1
                        WHERE order_id = :oid
                    """),
                    {"oid": order_id},
                )
            except Exception:
                pass

        # 알림: supplier에게 inspect_completed
        _create_notification(
            conn,
            recipient_type="supplier",
            recipient_id=company_id,
            event_type="inspect_completed",
            title="검수 결과가 등록되었습니다",
            message=f"발주 {order_id}의 검수 결과: {result_val}. {notes}",
            ref_id=str(order_id),
            ref_type="order",
        )

    return {
        "order_id": order_id,
        "status": new_status,
        "quality_status": quality_status,
        "notes": notes,
    }


# ===========================================================================
# Phase C-2: POST /api/jobs/{job_id}/images — 납품 이미지 업로드
# ===========================================================================

_VALID_IMAGE_TYPES = {"final_product", "packaging", "inspection_report"}


@router.post("/api/jobs/{job_id}/images")
async def upload_job_image(
    job_id: str,
    file: UploadFile = File(...),
    image_type: str = Form("final_product"),
    user: dict = Depends(get_current_user),
):
    """
    multipart 이미지 업로드 → uploads/delivery/ 저장 → delivery_images INSERT.
    delivery_images 테이블이 없으면 try/except로 안전 처리.
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    if image_type not in _VALID_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"image_type은 {sorted(_VALID_IMAGE_TYPES)} 중 하나여야 합니다",
        )

    # uploads/delivery/ 폴더 자동 생성
    DELIVERY_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # 파일 읽기
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="빈 파일입니다")

    # 파일 저장
    file_uuid = str(uuid.uuid4())
    original_filename = file.filename or "unknown"
    saved_name = f"{file_uuid}_{original_filename}"
    saved_path = DELIVERY_UPLOAD_DIR / saved_name
    saved_path.write_bytes(content)

    file_uri = f"uploads/delivery/{saved_name}"

    with engine.begin() as conn:
        # job 존재 확인
        job_row = conn.execute(
            text(f"""
                SELECT company_id
                FROM {SCHEMA}.manufacturing_jobs
                WHERE job_id = :jid
            """),
            {"jid": job_id},
        ).fetchone()

        if job_row is None:
            raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다")

        # 소유권 검증
        if user["role"] == "supplier" and user["id"] != str(job_row[0]):
            raise HTTPException(status_code=403, detail="본인의 작업에만 이미지를 업로드할 수 있습니다")

        # delivery_images INSERT (테이블 미존재 시 안전 처리)
        image_id = None
        uploaded_at = None
        try:
            img_row = conn.execute(
                text(f"""
                    INSERT INTO {SCHEMA}.delivery_images
                        (job_id, image_type, file_uri)
                    VALUES (:jid, :itype, :furi)
                    RETURNING image_id, uploaded_at
                """),
                {"jid": job_id, "itype": image_type, "furi": file_uri},
            ).fetchone()
            image_id = str(img_row[0])
            uploaded_at = str(img_row[1])
        except Exception:
            # delivery_images 테이블이 아직 없음 — 파일은 저장됨
            image_id = file_uuid
            uploaded_at = None

    return {
        "image_id": image_id,
        "job_id": job_id,
        "image_type": image_type,
        "file_uri": file_uri,
        "uploaded_at": uploaded_at,
    }


# ===========================================================================
# Phase C-2: GET /api/jobs/{job_id}/images — 납품 이미지 조회
# ===========================================================================


@router.get("/api/jobs/{job_id}/images")
def get_job_images(job_id: str, user: dict = Depends(get_current_user)):
    """delivery_images WHERE job_id 조회. 발주 당사자(buyer/supplier) + admin만."""
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        # 소유권 검증: job → order → buyer/company
        owner_row = conn.execute(
            text(f"""SELECT o.buyer_id, o.company_id
                     FROM {SCHEMA}.manufacturing_jobs mj
                     JOIN {SCHEMA}.orders o ON o.order_id = mj.order_id
                     WHERE mj.job_id = CAST(:jid AS uuid)"""),
            {"jid": job_id},
        ).fetchone()
        if owner_row is None:
            raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다")
        o_buyer_id = str(owner_row[0]) if owner_row[0] else None
        o_company_id = str(owner_row[1]) if owner_row[1] else None
        role = user["role"]
        uid = user["id"]
        if not (role == "admin"
                or (role == "buyer" and o_buyer_id == uid)
                or (role == "supplier" and o_company_id == uid)):
            raise HTTPException(status_code=403, detail="발주 당사자만 조회할 수 있습니다")

        try:
            rows = conn.execute(
                text(f"""
                    SELECT image_id, image_type, file_uri, uploaded_at
                    FROM {SCHEMA}.delivery_images
                    WHERE job_id = :jid
                    ORDER BY uploaded_at
                """),
                {"jid": job_id},
            ).fetchall()
        except Exception:
            # delivery_images 테이블이 아직 없음
            return []

    return [
        {
            "image_id": str(r[0]),
            "image_type": r[1],
            "file_uri": r[2],
            "uploaded_at": str(r[3]) if r[3] else None,
        }
        for r in rows
    ]


# ===========================================================================
# Phase C-3: POST /api/orders/{order_id}/shipment — 배송 정보 등록
# ===========================================================================


@router.post("/api/orders/{order_id}/shipment")
def create_shipment(order_id: str, data: dict,
                    user: dict = Depends(get_current_user)):
    """
    배송 정보 등록. supplier만 가능.
    shipments 테이블 미존재 시 try/except 안전 처리.
    inspection 이후 상태인 경우 orders.status → shipped 전이.
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    shipping_method = data.get("shipping_method")
    tracking_number = data.get("tracking_number")
    delivery_address = data.get("delivery_address")
    notes = data.get("notes")

    with engine.begin() as conn:
        # order 존재 + 소유권 조회
        order_row = conn.execute(
            text(f"""
                SELECT status, buyer_id, company_id
                FROM {SCHEMA}.orders
                WHERE order_id = :oid
            """),
            {"oid": order_id},
        ).fetchone()

        if order_row is None:
            raise HTTPException(status_code=404, detail="발주를 찾을 수 없습니다")

        current_status = order_row[0]
        buyer_id = str(order_row[1]) if order_row[1] else None
        company_id = str(order_row[2])

        # 소유권 검증: supplier만 배송 등록 가능
        if user["role"] == "supplier" and user["id"] != company_id:
            raise HTTPException(status_code=403, detail="본인에게 배정된 발주에만 배송 정보를 등록할 수 있습니다")

        # shipments INSERT (테이블 미존재 시 안전 처리)
        shipment_id = None
        shipped_at = None
        try:
            ship_row = conn.execute(
                text(f"""
                    INSERT INTO {SCHEMA}.shipments
                        (order_id, shipping_method, tracking_number,
                         shipped_at, delivery_address, notes)
                    VALUES (:oid, :sm, :tn, now(), :da, :notes)
                    RETURNING shipment_id, shipped_at
                """),
                {
                    "oid": order_id,
                    "sm": shipping_method,
                    "tn": tracking_number,
                    "da": delivery_address,
                    "notes": notes,
                },
            ).fetchone()
            shipment_id = str(ship_row[0])
            shipped_at = str(ship_row[1])
        except Exception:
            # shipments 테이블이 아직 없음
            shipment_id = str(uuid.uuid4())
            shipped_at = None

        # inspection 이후 상태인 경우 orders.status → shipped 전이
        if current_status in ("inspection", "in_production"):
            conn.execute(
                text(f"""
                    UPDATE {SCHEMA}.orders
                    SET status = 'shipped', updated_at = now()
                    WHERE order_id = :oid
                """),
                {"oid": order_id},
            )

        # 알림: buyer에게 delivery_shipped
        if buyer_id:
            _create_notification(
                conn,
                recipient_type="buyer",
                recipient_id=buyer_id,
                event_type="delivery_shipped",
                title="배송이 시작되었습니다",
                message=f"발주 {order_id}의 배송이 시작되었습니다. "
                        f"배송방법: {shipping_method or '-'}, "
                        f"송장번호: {tracking_number or '-'}",
                ref_id=str(order_id),
                ref_type="order",
            )

    return {
        "shipment_id": shipment_id,
        "order_id": order_id,
        "shipping_method": shipping_method,
        "tracking_number": tracking_number,
        "shipped_at": shipped_at,
    }


# ===========================================================================
# Phase C-3: GET /api/orders/{order_id}/shipment — 배송 정보 조회
# ===========================================================================


@router.get("/api/orders/{order_id}/shipment")
def get_shipment(order_id: str, user: dict = Depends(get_current_user)):
    """shipments WHERE order_id 조회. 발주 당사자(buyer/supplier) + admin만."""
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        # 소유권 검증
        owner_row = conn.execute(
            text(f"SELECT buyer_id, company_id FROM {SCHEMA}.orders WHERE order_id = CAST(:oid AS uuid)"),
            {"oid": order_id},
        ).fetchone()
        if owner_row is None:
            raise HTTPException(status_code=404, detail="발주를 찾을 수 없습니다")
        o_buyer_id = str(owner_row[0]) if owner_row[0] else None
        o_company_id = str(owner_row[1]) if owner_row[1] else None
        role = user["role"]
        uid = user["id"]
        if not (role == "admin"
                or (role == "buyer" and o_buyer_id == uid)
                or (role == "supplier" and o_company_id == uid)):
            raise HTTPException(status_code=403, detail="발주 당사자만 조회할 수 있습니다")

        try:
            row = conn.execute(
                text(f"""
                    SELECT shipment_id, shipping_method, tracking_number,
                           shipped_at, delivery_address, notes, created_at
                    FROM {SCHEMA}.shipments
                    WHERE order_id = :oid
                    ORDER BY created_at DESC
                    LIMIT 1
                """),
                {"oid": order_id},
            ).fetchone()
        except Exception:
            # shipments 테이블이 아직 없음
            return {"detail": "shipments 테이블이 아직 생성되지 않았습니다"}

    if row is None:
        raise HTTPException(status_code=404, detail="배송 정보를 찾을 수 없습니다")

    return {
        "shipment_id": str(row[0]),
        "order_id": order_id,
        "shipping_method": row[1],
        "tracking_number": row[2],
        "shipped_at": str(row[3]) if row[3] else None,
        "delivery_address": row[4],
        "notes": row[5],
        "created_at": str(row[6]),
    }


# ===========================================================================
# ===========================================================================
# (제거됨) _auto_book_equipment_schedule
# 장비 스케줄은 업체가 직접 관리 (POST /api/equipment/{id}/schedule)
# ===========================================================================


