"""
공통 모듈: DB engine, SCHEMA, 인증 의존성, 유틸 함수
"""

import os
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
# DB 접속
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL")

engine = None
SessionLocal = None

if DATABASE_URL:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

SCHEMA = "imma"

# ---------------------------------------------------------------------------
# JWT 설정
# ---------------------------------------------------------------------------

JWT_SECRET = os.getenv("JWT_SECRET", "imma-dev-secret")
_DEPLOY_ENV = (os.getenv("ENV") or os.getenv("RAILWAY_ENVIRONMENT") or "").lower()
_IS_DEPLOYED = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"))
if JWT_SECRET == "imma-dev-secret" and (_DEPLOY_ENV in {"production", "prod"} or _IS_DEPLOYED):
    raise RuntimeError("JWT_SECRET must be set in deployed/production environment")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24

_bearer_scheme = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------------------
# 비밀번호 해싱 / 검증
# ---------------------------------------------------------------------------


def _hash_password(password: str) -> str:
    """PBKDF2-SHA256 해싱. 반환값: 'salt_hex:hash_hex'"""
    salt = os.urandom(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return salt.hex() + ":" + hashed.hex()


def _verify_password(password: str, stored_hash: str) -> bool:
    """stored_hash ('salt_hex:hash_hex') 에서 salt 분리 후 PBKDF2 비교"""
    if not stored_hash or ":" not in stored_hash:
        return False
    salt_hex, hash_hex = stored_hash.split(":", 1)
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    computed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return hmac.compare_digest(computed, expected)


# ---------------------------------------------------------------------------
# 온보딩 자동 전환
# ---------------------------------------------------------------------------


def _check_onboarding(conn, company_id: str):
    row = conn.execute(
        text(f"""
            SELECT
                (SELECT business_registration_no FROM {SCHEMA}.companies WHERE company_id = :cid) IS NOT NULL AS has_brn,
                (SELECT count(*) FROM {SCHEMA}.equipment WHERE company_id = :cid) > 0 AS has_equip,
                (SELECT count(*) FROM {SCHEMA}.company_material_capabilities WHERE company_id = :cid) > 0 AS has_mat,
                (SELECT region FROM {SCHEMA}.company_sites WHERE company_id = :cid AND is_primary = true) IS NOT NULL AS has_region
        """),
        {"cid": company_id},
    ).fetchone()

    if row and all([row[0], row[1], row[2], row[3]]):
        conn.execute(
            text(f"""
                UPDATE {SCHEMA}.companies SET onboarding_status = 'verified'
                WHERE company_id = :cid AND onboarding_status IN ('draft', 'submitted', 'rejected')
            """),
            {"cid": company_id},
        )
        _refresh_mv(conn)
        return "verified"

    has_any = row and (row[1] or row[2])
    if has_any:
        conn.execute(
            text(f"""
                UPDATE {SCHEMA}.companies SET onboarding_status = 'submitted'
                WHERE company_id = :cid AND onboarding_status = 'draft'
            """),
            {"cid": company_id},
        )
        return "submitted"
    return "draft"


# ---------------------------------------------------------------------------
# MV REFRESH
# ---------------------------------------------------------------------------


def _refresh_mv(conn):
    conn.execute(text(f"REFRESH MATERIALIZED VIEW {SCHEMA}.company_capability_summary"))


# ---------------------------------------------------------------------------
# 알림 생성 헬퍼
# ---------------------------------------------------------------------------


def _create_notification(conn, recipient_type, recipient_id, event_type,
                         title, message=None, ref_id=None, ref_type=None):
    """notifications 테이블 INSERT. DDL 미적용 시에도 에러 없이 넘어감."""
    try:
        conn.execute(
            text(f"""
                INSERT INTO {SCHEMA}.notifications
                    (recipient_type, recipient_id, event_type, title, message,
                     reference_id, reference_type)
                VALUES (:rt, :rid, :et, :title, :msg, :ref_id, :ref_type)
            """),
            {
                "rt": recipient_type,
                "rid": recipient_id,
                "et": event_type,
                "title": title,
                "msg": message,
                "ref_id": ref_id,
                "ref_type": ref_type,
            },
        )
    except Exception:
        # notifications 테이블이 아직 없을 수 있음 — 무시
        pass


# ---------------------------------------------------------------------------
# JWT 토큰 생성 / 디코딩
# ---------------------------------------------------------------------------


def _create_token(sub: str, login_id: str, role: str) -> str:
    """JWT 액세스 토큰 생성"""
    payload = {
        "sub": sub,
        "login_id": login_id,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_token(token: str) -> dict:
    """JWT 디코딩. 실패 시 HTTPException."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="토큰이 만료되었습니다",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않은 토큰입니다",
        )


# ---------------------------------------------------------------------------
# 인증 의존성
# ---------------------------------------------------------------------------


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> dict:
    """JWT에서 사용자 정보 반환: {id, login_id, role}"""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증 토큰이 필요합니다",
        )
    payload = _decode_token(credentials.credentials)
    return {
        "id": payload["sub"],
        "login_id": payload["login_id"],
        "role": payload["role"],
    }


def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> dict:
    """admin 전용 의존성"""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증 토큰이 필요합니다",
        )
    payload = _decode_token(credentials.credentials)
    if payload.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="관리자 권한이 필요합니다",
        )
    return {
        "id": payload["sub"],
        "login_id": payload["login_id"],
        "role": payload["role"],
    }
