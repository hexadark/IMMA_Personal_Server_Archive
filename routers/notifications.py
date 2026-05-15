"""
알림 엔드포인트:
- GET  /api/notifications              — 본인 알림 목록 (인증 기반)
- PUT  /api/notifications/{id}/read    — 읽음 처리
- GET  /api/notifications/unread-count — 미읽음 수
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from routers.deps import engine, SCHEMA, get_current_user

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/notifications — 본인 알림 목록
# ---------------------------------------------------------------------------


@router.get("/api/notifications")
def list_notifications(
    unread_only: bool = False,
    user: dict = Depends(get_current_user),
):
    """
    JWT의 role → recipient_type, JWT의 sub → recipient_id로 본인 알림만 조회.
    최근 100건, created_at DESC.
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    # role → recipient_type 매핑
    role = user["role"]
    if role == "buyer":
        recipient_type = "buyer"
    elif role == "supplier":
        recipient_type = "supplier"
    elif role == "admin":
        recipient_type = "admin"
    else:
        raise HTTPException(status_code=400, detail="알 수 없는 사용자 역할입니다")

    recipient_id = user["id"]

    with engine.connect() as conn:
        try:
            if unread_only:
                rows = conn.execute(
                    text(f"""
                        SELECT notification_id, event_type, title, message,
                               reference_id, reference_type, is_read, created_at
                        FROM {SCHEMA}.notifications
                        WHERE recipient_type = :rtype
                          AND recipient_id = :rid
                          AND is_read = false
                        ORDER BY created_at DESC
                        LIMIT 100
                    """),
                    {"rtype": recipient_type, "rid": recipient_id},
                ).fetchall()
            else:
                rows = conn.execute(
                    text(f"""
                        SELECT notification_id, event_type, title, message,
                               reference_id, reference_type, is_read, created_at
                        FROM {SCHEMA}.notifications
                        WHERE recipient_type = :rtype
                          AND recipient_id = :rid
                        ORDER BY created_at DESC
                        LIMIT 100
                    """),
                    {"rtype": recipient_type, "rid": recipient_id},
                ).fetchall()
        except Exception:
            # notifications 테이블이 아직 없음
            return []

    return [
        {
            "notification_id": str(r[0]),
            "event_type": r[1],
            "title": r[2],
            "message": r[3],
            "reference_id": str(r[4]) if r[4] else None,
            "reference_type": r[5],
            "is_read": r[6],
            "created_at": str(r[7]),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# PUT /api/notifications/{notification_id}/read — 읽음 처리
# ---------------------------------------------------------------------------


@router.put("/api/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: str,
    user: dict = Depends(get_current_user),
):
    """해당 알림의 recipient_id와 JWT sub 일치 확인 후 is_read = true UPDATE."""
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.begin() as conn:
        try:
            # 알림 존재 + 소유권 확인
            row = conn.execute(
                text(f"""
                    SELECT recipient_id
                    FROM {SCHEMA}.notifications
                    WHERE notification_id = :nid
                """),
                {"nid": notification_id},
            ).fetchone()
        except Exception:
            # notifications 테이블이 아직 없음
            raise HTTPException(
                status_code=500,
                detail="notifications 테이블이 아직 생성되지 않았습니다",
            )

        if row is None:
            raise HTTPException(status_code=404, detail="알림을 찾을 수 없습니다")

        # 소유권 검증
        if str(row[0]) != user["id"]:
            raise HTTPException(status_code=403, detail="본인의 알림만 읽음 처리할 수 있습니다")

        # is_read = true UPDATE
        conn.execute(
            text(f"""
                UPDATE {SCHEMA}.notifications
                SET is_read = true
                WHERE notification_id = :nid
            """),
            {"nid": notification_id},
        )

    return {"success": True}


# ---------------------------------------------------------------------------
# GET /api/notifications/unread-count — 미읽음 수
# ---------------------------------------------------------------------------


@router.get("/api/notifications/unread-count")
def get_unread_count(user: dict = Depends(get_current_user)):
    """COUNT WHERE recipient_id = JWT.sub AND is_read = false."""
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    recipient_id = user["id"]

    with engine.connect() as conn:
        try:
            row = conn.execute(
                text(f"""
                    SELECT COUNT(*)
                    FROM {SCHEMA}.notifications
                    WHERE recipient_id = :rid
                      AND is_read = false
                """),
                {"rid": recipient_id},
            ).fetchone()
        except Exception:
            # notifications 테이블이 아직 없음
            return {"unread_count": 0}

    return {"unread_count": row[0] if row else 0}
