"""
카탈로그 조회 엔드포인트 (기존 6개, 인증 불필요):
- GET /api/processes
- GET /api/material-categories
- GET /api/materials
- GET /api/equipment-categories
- GET /api/equipment-models
- GET /api/health
"""

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from routers.deps import engine, SCHEMA

router = APIRouter()


@router.get("/api/processes")
def get_processes():
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
    with engine.connect() as conn:
        result = conn.execute(text(f"""
            SELECT process_code, process_name_ko, process_name_en, process_group
            FROM {SCHEMA}.process_catalog
            WHERE is_active = true
            ORDER BY process_group, process_code
        """))
        rows = result.fetchall()
    return [
        {
            "process_code": r[0], "process_name_ko": r[1],
            "process_name_en": r[2], "process_group": r[3],
        }
        for r in rows
    ]


@router.get("/api/material-categories")
def get_material_categories():
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
    with engine.connect() as conn:
        result = conn.execute(text(f"""
            SELECT category_code, category_name_ko, category_name_en
            FROM {SCHEMA}.material_category_catalog
            WHERE is_active = true
            ORDER BY category_code
        """))
        rows = result.fetchall()
    data = [
        {
            "category_code": r[0], "category_name_ko": r[1],
            "category_name_en": r[2],
        }
        for r in rows
    ]
    data.append({
        "category_code": "other",
        "category_name_ko": "기타 (직접 입력)",
        "category_name_en": "Other (manual input)",
    })
    return data


@router.get("/api/materials")
def get_materials(category: str = ""):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
    with engine.connect() as conn:
        result = conn.execute(
            text(f"""
                SELECT material_code, material_name_ko
                FROM {SCHEMA}.materials
                WHERE category_code = :cat AND is_active = true
                ORDER BY material_code
            """),
            {"cat": category},
        )
        rows = result.fetchall()
    data = [{"material_code": r[0], "material_name_ko": r[1]} for r in rows]
    data.append({
        "material_code": "__other__",
        "material_name_ko": "기타 (직접 입력)",
    })
    return data


@router.get("/api/equipment-categories")
def get_equipment_categories():
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
    with engine.connect() as conn:
        result = conn.execute(text(f"""
            SELECT equipment_category_code, category_name_ko, category_name_en
            FROM {SCHEMA}.equipment_category_catalog
            WHERE is_active = true
            ORDER BY equipment_category_code
        """))
        rows = result.fetchall()
    return [
        {
            "equipment_category_code": r[0], "category_name_ko": r[1],
            "category_name_en": r[2],
        }
        for r in rows
    ]


@router.get("/api/equipment-models")
def get_equipment_models(category: str = ""):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
    with engine.connect() as conn:
        result = conn.execute(
            text(f"""
                SELECT model_id, manufacturer, model_name
                FROM {SCHEMA}.equipment_model_catalog
                WHERE equipment_category_code = :cat
                ORDER BY manufacturer, model_name
            """),
            {"cat": category},
        )
        rows = result.fetchall()
    return [{"model_id": r[0], "manufacturer": r[1], "model_name": r[2]} for r in rows]


@router.get("/api/health")
def health():
    if engine is None:
        return {"status": "error", "db": False}
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "db": True}
    except Exception:
        return {"status": "error", "db": False}
