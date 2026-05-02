import os
from pathlib import Path

DATABASE_URL = os.getenv("DATABASE_URL")

DB_HOST = os.getenv("DB_HOST", "")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "imma")
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
SCHEMA = "imma"

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
