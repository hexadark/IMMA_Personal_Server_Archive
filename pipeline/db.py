"""IMMA Phase 1 매칭 파이프라인 — psycopg2 커넥션 및 쿼리 헬퍼."""

import logging
from contextlib import contextmanager
from typing import Any, Generator

import psycopg2
import psycopg2.extras

import config

logger = logging.getLogger(__name__)


def get_connection() -> psycopg2.extensions.connection:
    """config.py의 접속 정보로 PostgreSQL 커넥션을 생성하여 반환한다.
    DATABASE_URL이 있으면 우선적으로 사용한다.
    """
    database_url = getattr(config, "DATABASE_URL", None)

    if database_url:
        try:
            return psycopg2.connect(
                database_url, options=f"-c search_path={config.SCHEMA},public"
            )
        except psycopg2.Error as e:
            logger.error("DATABASE_URL 연결 실패: %s", e)
            raise

    kwargs = {
        "dbname": config.DB_NAME,
        "user": config.DB_USER,
        "options": f"-c search_path={config.SCHEMA},public",
    }
    if config.DB_HOST:
        kwargs["host"] = config.DB_HOST
        kwargs["port"] = config.DB_PORT
    if config.DB_PASSWORD:
        kwargs["password"] = config.DB_PASSWORD
    try:
        return psycopg2.connect(**kwargs)
    except psycopg2.Error as e:
        logger.error("DB 연결 실패: %s", e)
        raise


def execute_query(sql: str, params: tuple | None = None) -> list[dict[str, Any]]:
    """SELECT 쿼리를 실행하고 결과를 dict 리스트로 반환한다."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def execute_insert(sql: str, params: tuple | None = None) -> None:
    """INSERT/UPDATE/DELETE 쿼리를 실행하고 커밋한다."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def execute_script(sql: str) -> None:
    """여러 SQL 문(DDL 등)을 한번에 실행한다."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    finally:
        conn.close()


def execute_returning(sql: str, params: tuple | None = None) -> Any:
    """INSERT ... RETURNING 쿼리를 실행하고 반환값을 돌려준다."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            result = cur.fetchone()
        conn.commit()
        return result
    finally:
        conn.close()


@contextmanager
def transaction() -> Generator[psycopg2.extensions.connection, None, None]:
    """트랜잭션 context manager. 블록 내 모든 쿼리를 하나의 트랜잭션으로 묶는다.

    정상 종료 시 커밋, 예외 발생 시 자동 롤백한다.
    conn을 반환하며, conn.cursor()로 직접 쿼리를 실행할 수 있다.
    """
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def execute_returning_with_conn(
    conn: psycopg2.extensions.connection, sql: str, params: tuple | None = None
) -> Any:
    """기존 커넥션에서 INSERT ... RETURNING 쿼리를 실행한다. 커밋하지 않는다."""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute_insert_with_conn(
    conn: psycopg2.extensions.connection, sql: str, params: tuple | None = None
) -> None:
    """기존 커넥션에서 INSERT/UPDATE/DELETE 쿼리를 실행한다. 커밋하지 않는다."""
    with conn.cursor() as cur:
        cur.execute(sql, params)


def execute_query_with_conn(
    conn: psycopg2.extensions.connection, sql: str, params: tuple | None = None
) -> list[dict[str, Any]]:
    """기존 커넥션에서 SELECT 쿼리를 실행하고 결과를 dict 리스트로 반환한다."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
