"""
로그인 + 사용자 정보 조회:
- POST /api/login
- GET  /api/me
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from routers.deps import (
    engine, SCHEMA,
    _verify_password, _create_token, get_current_user,
)

router = APIRouter()


def _user_payload(user_id, login_id, role, name=None, company_name=None, contact_name=None):
    payload = {
        "id": str(user_id),
        "login_id": login_id,
        "role": role,
    }
    if name:
        payload["name"] = name
    if company_name:
        payload["company_name"] = company_name
    if contact_name:
        payload["contact_name"] = contact_name
    return payload


@router.post("/api/login")
def login(data: dict):
    """
    login_id + password → buyers/companies 순차 조회 → JWT 발급.
    role은 login_id가 어느 테이블에 있는지로 자동 판별.
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    login_id = data.get("login_id")
    password = data.get("password")
    # UI에서 명시 전달한 role — 매칭 role 과 불일치 시 401 (cross-role 로그인 차단)
    expected_role = data.get("expected_role")  # 'buyer' | 'supplier' | None

    if not login_id or not password:
        raise HTTPException(status_code=400, detail="login_id와 password가 필요합니다")

    with engine.connect() as conn:
        buyer_row = conn.execute(
            text(f"""
                SELECT buyer_id, login_id, password_hash, buyer_name, company_name
                FROM {SCHEMA}.buyers
                WHERE login_id = :login_id
            """),
            {"login_id": login_id},
        ).fetchone()

        if buyer_row:
            if expected_role and expected_role != "buyer":
                raise HTTPException(status_code=401, detail="해당 역할의 계정이 아닙니다")
            if not _verify_password(password, buyer_row[2]):
                raise HTTPException(status_code=401, detail="비밀번호가 일치하지 않습니다")
            token = _create_token(sub=str(buyer_row[0]), login_id=buyer_row[1], role="buyer")
            user = _user_payload(buyer_row[0], buyer_row[1], "buyer", name=buyer_row[3], company_name=buyer_row[4])
            return {"access_token": token, "token_type": "bearer", "user": user}

        company_row = conn.execute(
            text(f"""
                SELECT c.company_id, c.login_id, c.password_hash, c.company_name,
                       cc.contact_name
                FROM {SCHEMA}.companies c
                LEFT JOIN LATERAL (
                    SELECT contact_name
                    FROM {SCHEMA}.company_contacts
                    WHERE company_id = c.company_id AND is_primary = true
                    ORDER BY created_at DESC
                    LIMIT 1
                ) cc ON true
                WHERE c.login_id = :login_id
            """),
            {"login_id": login_id},
        ).fetchone()

        if company_row:
            if expected_role and expected_role != "supplier":
                raise HTTPException(status_code=401, detail="해당 역할의 계정이 아닙니다")
            if not _verify_password(password, company_row[2]):
                raise HTTPException(status_code=401, detail="비밀번호가 일치하지 않습니다")
            token = _create_token(sub=str(company_row[0]), login_id=company_row[1], role="supplier")
            user = _user_payload(
                company_row[0], company_row[1], "supplier",
                name=company_row[4] or company_row[3], company_name=company_row[3], contact_name=company_row[4],
            )
            return {"access_token": token, "token_type": "bearer", "user": user}

    raise HTTPException(status_code=401, detail="존재하지 않는 계정입니다")


@router.get("/api/me")
def me(current_user: dict = Depends(get_current_user)):
    """JWT와 DB를 결합해 header 표시용 사용자 정보를 반환한다."""
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    role = current_user["role"]
    user_id = current_user["id"]
    with engine.connect() as conn:
        if role == "buyer":
            row = conn.execute(
                text(f"""
                    SELECT buyer_id, login_id, buyer_name, company_name
                    FROM {SCHEMA}.buyers
                    WHERE buyer_id = CAST(:uid AS uuid)
                """),
                {"uid": user_id},
            ).fetchone()
            if row:
                return _user_payload(row[0], row[1], "buyer", name=row[2], company_name=row[3])
        elif role == "supplier":
            row = conn.execute(
                text(f"""
                    SELECT c.company_id, c.login_id, c.company_name, cc.contact_name
                    FROM {SCHEMA}.companies c
                    LEFT JOIN LATERAL (
                        SELECT contact_name
                        FROM {SCHEMA}.company_contacts
                        WHERE company_id = c.company_id AND is_primary = true
                        ORDER BY created_at DESC
                        LIMIT 1
                    ) cc ON true
                    WHERE c.company_id = CAST(:uid AS uuid)
                """),
                {"uid": user_id},
            ).fetchone()
            if row:
                return _user_payload(row[0], row[1], "supplier", name=row[3] or row[2], company_name=row[2], contact_name=row[3])
        elif role == "admin":
            row = conn.execute(
                text(f"""
                    SELECT admin_id, login_id, name
                    FROM {SCHEMA}.admins
                    WHERE admin_id = CAST(:uid AS uuid)
                """),
                {"uid": user_id},
            ).fetchone()
            if row:
                return _user_payload(row[0], row[1], "admin", name=row[2])

    return {
        "id": current_user["id"],
        "login_id": current_user["login_id"],
        "role": current_user["role"],
    }
