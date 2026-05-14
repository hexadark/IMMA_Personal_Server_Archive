"""
견적 엔드포인트:
- POST /api/quote                  (기존 + B-7 자동 전이 + 보안 검증)
- GET  /api/rfq/{rfq_id}/quotes    (B-5: 견적 비교 조회)
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from routers.deps import engine, SCHEMA, get_current_user, _create_notification

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /api/quote — 견적 제출 (기존 + 자동 전이 + 보안 검증 + 알림)
# ---------------------------------------------------------------------------


@router.post("/api/quote")
def create_quote(data: dict, current_user: dict = Depends(get_current_user)):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    rfq_id = data.get("rfq_id")
    company_id = data.get("company_id")
    total_price = data.get("total_price")
    estimated_lead_days = data.get("estimated_lead_days")

    if not rfq_id or not company_id:
        raise HTTPException(status_code=400, detail="rfq_id and company_id are required")

    # role 화이트리스트: supplier만 견적 제출 가능
    if current_user["role"] != "supplier":
        raise HTTPException(status_code=403, detail="supplier만 견적을 제출할 수 있습니다")
    if current_user["id"] != company_id:
        raise HTTPException(status_code=403, detail="본인의 업체로만 견적을 제출할 수 있습니다")

    with engine.begin() as conn:
        # 매칭 수락 검증: match_candidates.supplier_response='accepted' 필요
        mc_row = conn.execute(
            text(f"""
                SELECT supplier_response
                FROM {SCHEMA}.match_candidates mc
                JOIN {SCHEMA}.match_runs mr ON mc.match_run_id = mr.match_run_id
                WHERE mr.rfq_id = :rfq_id AND mc.company_id = :cid
                LIMIT 1
            """),
            {"rfq_id": rfq_id, "cid": company_id},
        ).fetchone()

        if mc_row is None or mc_row[0] != "accepted":
            raise HTTPException(
                status_code=403,
                detail="매칭을 수락한 업체만 견적을 제출할 수 있습니다",
            )

        # 견적 INSERT
        q_row = conn.execute(
            text(f"""
                INSERT INTO {SCHEMA}.quote_responses
                    (rfq_id, company_id, total_price, estimated_lead_days,
                     proposed_delivery_date, validity_until, assumptions,
                     status, submitted_at)
                VALUES (:rid, :cid, :tp, :eld, :pdd, :vu, :asm,
                        'submitted', now())
                RETURNING quote_id, created_at
            """),
            {
                "rid": rfq_id, "cid": company_id,
                "tp": total_price, "eld": estimated_lead_days,
                "pdd": data.get("proposed_delivery_date"),
                "vu": data.get("validity_until"),
                "asm": data.get("assumptions"),
            },
        ).fetchone()
        quote_id = q_row[0]

        for item in data.get("line_items", []):
            # process_code 방어층: CSV/array → 첫 단일 코드 정규화 + FK 검증
            raw_pc = item.get("process_code")
            if isinstance(raw_pc, str) and "," in raw_pc:
                raw_pc = raw_pc.split(",")[0].strip()
            elif isinstance(raw_pc, list):
                raw_pc = raw_pc[0] if raw_pc else None
            # process_catalog 존재 검증 (없으면 NULL — FK 컬럼 nullable)
            if raw_pc:
                exists = conn.execute(
                    text(f"SELECT 1 FROM {SCHEMA}.process_catalog WHERE process_code = :pc"),
                    {"pc": raw_pc},
                ).fetchone()
                if not exists:
                    raw_pc = None
            conn.execute(
                text(f"""
                    INSERT INTO {SCHEMA}.quote_line_items
                        (quote_id, rfq_part_id, process_code, item_description,
                         quantity, unit_price, line_total, notes)
                    VALUES (:qid, :rpid, :pc, :desc, :qty, :up, :lt, :notes)
                """),
                {
                    "qid": quote_id,
                    "rpid": item.get("rfq_part_id"),
                    "pc": raw_pc,
                    "desc": item.get("description"),
                    "qty": item.get("quantity"),
                    "up": item.get("unit_price"),
                    "lt": item.get("line_total"),
                    "notes": item.get("notes"),
                },
            )

        # B-7 자동 전이: 첫 견적 도착 시 rfqs.status open → quoted
        rfq_row = conn.execute(
            text(f"SELECT status, buyer_id FROM {SCHEMA}.rfqs WHERE rfq_id = :rid"),
            {"rid": rfq_id},
        ).fetchone()

        if rfq_row and rfq_row[0] == "open":
            conn.execute(
                text(f"""
                    UPDATE {SCHEMA}.rfqs
                    SET status = 'quoted', updated_at = now()
                    WHERE rfq_id = :rid AND status = 'open'
                """),
                {"rid": rfq_id},
            )

        # 알림: buyer에게 quote_received
        if rfq_row and rfq_row[1]:
            # 업체명 조회
            comp_row = conn.execute(
                text(f"SELECT company_name FROM {SCHEMA}.companies WHERE company_id = :cid"),
                {"cid": company_id},
            ).fetchone()
            comp_name = comp_row[0] if comp_row else company_id

            _create_notification(
                conn,
                recipient_type="buyer",
                recipient_id=str(rfq_row[1]),
                event_type="quote_received",
                title="새로운 견적이 도착했습니다",
                message=f"{comp_name}에서 RFQ {rfq_id}에 대한 견적을 제출했습니다.",
                ref_id=str(quote_id),
                ref_type="quote",
            )

    return {
        "success": True,
        "quote_id": str(quote_id),
        "created_at": str(q_row[1]),
    }


# ---------------------------------------------------------------------------
# B-5: GET /api/rfq/{rfq_id}/quotes — 견적 비교 조회
# ---------------------------------------------------------------------------


@router.get("/api/rfq/{rfq_id}/quotes")
def get_rfq_quotes(rfq_id: str, current_user: dict = Depends(get_current_user)):
    """해당 RFQ의 모든 견적 + 업체 정보 반환"""
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        # RFQ 존재 + 소유권 확인
        rfq_row = conn.execute(
            text(f"SELECT buyer_id FROM {SCHEMA}.rfqs WHERE rfq_id = :rid"),
            {"rid": rfq_id},
        ).fetchone()

        if rfq_row is None:
            raise HTTPException(status_code=404, detail="RFQ를 찾을 수 없습니다")

        rfq_buyer_id = str(rfq_row[0]) if rfq_row[0] else None
        role = current_user["role"]
        uid = current_user["id"]

        # 역할별 분기: buyer=자기 RFQ만 전체 견적, supplier=자기 견적만, admin=전체
        if role == "buyer":
            if rfq_buyer_id != uid:
                raise HTTPException(status_code=403, detail="본인 RFQ만 조회할 수 있습니다")
            where_extra = ""
            params = {"rfq_id": rfq_id}
        elif role == "supplier":
            where_extra = " AND qr.company_id = CAST(:uid AS uuid)"
            params = {"rfq_id": rfq_id, "uid": uid}
        elif role == "admin":
            where_extra = ""
            params = {"rfq_id": rfq_id}
        else:
            raise HTTPException(status_code=403, detail="권한이 없습니다")

        rows = conn.execute(
            text(f"""
                SELECT
                    qr.quote_id,
                    qr.company_id,
                    c.company_name,
                    qr.total_price,
                    qr.estimated_lead_days,
                    qr.proposed_delivery_date,
                    qr.validity_until,
                    qr.assumptions,
                    qr.status,
                    qr.submitted_at,
                    qr.created_at
                FROM {SCHEMA}.quote_responses qr
                JOIN {SCHEMA}.companies c ON qr.company_id = c.company_id
                WHERE qr.rfq_id = :rfq_id{where_extra}
                ORDER BY qr.total_price ASC NULLS LAST, qr.created_at
            """),
            params,
        ).fetchall()

    quotes = []
    for row in rows:
        quotes.append({
            "quote_id": str(row[0]),
            "company_id": str(row[1]),
            "company_name": row[2],
            "total_price": float(row[3]) if row[3] is not None else None,
            "estimated_lead_days": row[4],
            "proposed_delivery_date": str(row[5]) if row[5] else None,
            "validity_until": str(row[6]) if row[6] else None,
            "assumptions": row[7],
            "status": row[8],
            "submitted_at": str(row[9]) if row[9] else None,
            "created_at": str(row[10]),
        })

    return {"rfq_id": rfq_id, "count": len(quotes), "quotes": quotes}
