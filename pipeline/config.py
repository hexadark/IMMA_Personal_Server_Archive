"""IMMA Phase 1 매칭 파이프라인 — DB 연결 정보 및 경로 설정."""

import os
from pathlib import Path

DATABASE_URL = os.getenv("DATABASE_URL")

DB_HOST = os.getenv("DB_HOST", os.environ.get("IMMA_DB_HOST", ""))
DB_PORT = int(os.getenv("DB_PORT", os.environ.get("IMMA_DB_PORT", "5432")))
DB_NAME = os.getenv("DB_NAME", os.environ.get("IMMA_DB_NAME", "imma"))
DB_USER = os.getenv("DB_USER", os.environ.get("IMMA_DB_USER", "tae-hun-kim"))
DB_PASSWORD = os.getenv("DB_PASSWORD", os.environ.get("IMMA_DB_PASSWORD", ""))
SCHEMA = os.getenv("SCHEMA", os.environ.get("IMMA_SCHEMA", "imma"))

LOOKUP_TABLE_PATH = str(
    Path(__file__).resolve().parent.parent
    / "lookup_tables"
    / "lookup_data.json"
)

EQUIPMENT_CATALOG_PATH = str(
    Path(__file__).resolve().parent.parent
    / "lookup_tables"
    / "equipment_catalog.json"
)
