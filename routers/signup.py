"""
회원가입 엔드포인트
- POST /signup
"""

from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from routers.deps import engine, SCHEMA, _hash_password

router = APIRouter()


@router.post("/signup")
def signup(data: dict):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    login_id = data.get("login_id")
    name = data.get("name")
    company_name = data.get("company_name")
    email = data.get("email")
    password = data.get("password")
    phone = data.get("phone")
    role = data.get("role", "buyer")

    if role == "supplier":
        if not login_id or not name or not company_name or not email or not password:
            raise HTTPException(
                status_code=400,
                detail="login_id, name, company_name, email, password are required",
            )
    else:
        if not login_id or not name or not email or not password:
            raise HTTPException(
                status_code=400,
                detail="login_id, name, email, password are required",
            )

    pw_hash = _hash_password(password)

    with engine.connect() as conn_check:
        existing = conn_check.execute(
            text(f"""
                SELECT 'buyer' AS src FROM {SCHEMA}.buyers WHERE login_id = :lid
                UNION ALL
                SELECT 'company' AS src FROM {SCHEMA}.companies WHERE login_id = :lid
                LIMIT 1
            """),
            {"lid": login_id},
        ).fetchone()
        if existing is not None:
            raise HTTPException(status_code=409, detail="이미 가입된 ID입니다")

    try:
        with engine.begin() as conn:
            if role == "supplier":
                result = conn.execute(
                    text(f"""
                        INSERT INTO {SCHEMA}.companies
                            (login_id, company_name, main_email, password_hash,
                             main_phone, status, onboarding_status)
                        VALUES (:login_id, :company_name, :email, :pw_hash,
                                :phone, 'active', 'submitted')
                        RETURNING company_id, company_name, main_email,
                                  onboarding_status, created_at
                    """),
                    {
                        "login_id": login_id,
                        "company_name": company_name,
                        "email": email,
                        "pw_hash": pw_hash,
                        "phone": phone,
                    },
                )
                row = result.fetchone()
                company_id = row[0]

                conn.execute(
                    text(f"""
                        INSERT INTO {SCHEMA}.company_contacts
                            (company_id, contact_name, role_title, phone, email,
                             is_primary, receives_rfq)
                        VALUES (:cid, :contact_name, '가입 담당자', :phone, :email,
                                true, true)
                        ON CONFLICT (company_id, contact_name) DO NOTHING
                    """),
                    {
                        "cid": company_id,
                        "contact_name": name,
                        "phone": phone,
                        "email": email,
                    },
                )

                conn.execute(
                    text(f"""
                        INSERT INTO {SCHEMA}.company_availability_snapshot
                            (company_id, overall_status)
                        VALUES (:cid, 'available')
                        ON CONFLICT (company_id) DO NOTHING
                    """),
                    {"cid": company_id},
                )

                return {
                    "message": "signup success",
                    "user": {
                        "id": str(company_id),
                        "login_id": login_id,
                        "name": name,
                        "company_name": row[1],
                        "email": row[2],
                        "role": "supplier",
                        "onboarding_status": row[3],
                        "created_at": str(row[4]),
                    },
                }

            result = conn.execute(
                text(f"""
                    INSERT INTO {SCHEMA}.buyers
                        (login_id, buyer_name, company_name, email, password_hash, phone)
                    VALUES (:login_id, :name, :company_name, :email, :pw_hash, :phone)
                    RETURNING buyer_id, buyer_name, company_name, email, created_at
                """),
                {
                    "login_id": login_id,
                    "name": name,
                    "company_name": company_name,
                    "email": email,
                    "pw_hash": pw_hash,
                    "phone": phone,
                },
            )
            row = result.fetchone()
            return {
                "message": "signup success",
                "user": {
                    "id": str(row[0]),
                    "login_id": login_id,
                    "name": row[1],
                    "company_name": row[2],
                    "email": row[3],
                    "role": "buyer",
                    "onboarding_status": "not_required",
                    "created_at": str(row[4]),
                },
            }
    except IntegrityError:
        raise HTTPException(status_code=409, detail="이미 가입된 ID 또는 사업자번호입니다")
