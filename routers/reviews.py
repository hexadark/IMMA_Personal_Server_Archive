"""
리뷰 엔드포인트 (기존 2개):
- GET  /api/reviews
- POST /api/reviews
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from routers.deps import engine, SCHEMA, get_current_user, _refresh_mv, _create_notification

router = APIRouter()


def _mask_buyer_name(name: str | None) -> str | None:
    """리뷰 작성자 이름을 PII 보호를 위해 마스킹한다 (예: '김철수' → '김**')."""
    if not name:
        return name
    if len(name) <= 1:
        return name
    return name[0] + "*" * (len(name) - 1)


@router.get("/api/reviews")
def get_reviews(company_id: str = ""):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        if company_id:
            result = conn.execute(text(f"""
                SELECT
                    r.company_id,
                    c.company_name,
                    b.buyer_name,
                    r.rating_overall,
                    r.rating_quality,
                    r.rating_delivery,
                    r.rating_communication,
                    r.rating_price,
                    r.comment
                FROM {SCHEMA}.reviews r
                LEFT JOIN {SCHEMA}.buyers b ON r.buyer_id = b.buyer_id
                LEFT JOIN {SCHEMA}.companies c ON r.company_id = c.company_id
                WHERE r.company_id = :cid
                ORDER BY r.created_at DESC
            """), {"cid": company_id})
        else:
            result = conn.execute(text(f"""
                SELECT
                    r.company_id,
                    c.company_name,
                    b.buyer_name,
                    r.rating_overall,
                    r.rating_quality,
                    r.rating_delivery,
                    r.rating_communication,
                    r.rating_price,
                    r.comment
                FROM {SCHEMA}.reviews r
                LEFT JOIN {SCHEMA}.buyers b ON r.buyer_id = b.buyer_id
                LEFT JOIN {SCHEMA}.companies c ON r.company_id = c.company_id
                ORDER BY r.rating_overall DESC
            """))
        rows = result.fetchall()

    data = []
    for row in rows:
        data.append({
            "company_id": str(row[0]) if row[0] else None,
            "company_name": row[1],
            "buyer_name": _mask_buyer_name(row[2]),
            "rating_overall": float(row[3]) if row[3] else None,
            "rating_quality": float(row[4]) if row[4] else None,
            "rating_delivery": float(row[5]) if row[5] else None,
            "rating_communication": float(row[6]) if row[6] else None,
            "rating_price": float(row[7]) if row[7] else None,
            "comment": row[8],
        })

    return {"status": "success", "data": data}


@router.post("/api/reviews")
def create_review(data: dict, current_user: dict = Depends(get_current_user)):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
    if current_user["role"] != "buyer":
        raise HTTPException(status_code=403, detail="buyer만 리뷰를 작성할 수 있습니다")

    company_id = data.get("company_id")
    buyer_id = current_user["id"]  # body에서 받지 않고 인증된 사용자의 id 사용
    rating_overall = data.get("rating_overall")

    if not company_id or not rating_overall:
        raise HTTPException(
            status_code=400,
            detail="company_id and rating_overall are required",
        )

    with engine.begin() as conn:
        # 발주 완료(delivered/completed) 검증: 해당 buyer가 해당 company에 실제 발주 완료한 이력이 있어야 리뷰 가능
        order_check = conn.execute(
            text(f"""
                SELECT 1 FROM {SCHEMA}.orders
                WHERE buyer_id = CAST(:bid AS uuid)
                  AND company_id = CAST(:cid AS uuid)
                  AND status IN ('delivered', 'completed')
                LIMIT 1
            """),
            {"bid": buyer_id, "cid": company_id},
        ).fetchone()
        if order_check is None:
            raise HTTPException(
                status_code=403,
                detail="해당 업체에 납품 완료된 발주가 있어야 리뷰를 작성할 수 있습니다",
            )

        result = conn.execute(
            text(f"""
                INSERT INTO {SCHEMA}.reviews
                    (company_id, buyer_id, rating_overall, rating_quality,
                     rating_delivery, rating_communication, rating_price, comment)
                VALUES (:cid, :bid, :ro, :rq, :rd, :rc, :rp, :cm)
                RETURNING review_id
            """),
            {
                "cid": company_id, "bid": buyer_id,
                "ro": rating_overall,
                "rq": data.get("rating_quality"),
                "rd": data.get("rating_delivery"),
                "rc": data.get("rating_communication"),
                "rp": data.get("rating_price"),
                "cm": data.get("comment"),
            },
        )
        review_row = result.fetchone()
        review_id = str(review_row[0]) if review_row else None

        _refresh_mv(conn)

        # supplier에게 review_received 알림 발송
        _create_notification(
            conn,
            recipient_type="supplier",
            recipient_id=str(company_id),
            event_type="review_received",
            title="새 리뷰가 등록되었습니다",
            message=f"평점: {rating_overall}",
            ref_id=review_id,
            ref_type="review",
        )

    return {"success": True}
