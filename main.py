import os
import json
import sys
import hashlib
from pathlib import Path

from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return salt.hex() + ":" + hashed.hex()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.getenv("DATABASE_URL")

PIPELINE_DIR = Path(__file__).resolve().parent / "pipeline"
if PIPELINE_DIR.exists():
    sys.path.insert(0, str(PIPELINE_DIR))

try:
    from pipeline_runner import run_pipeline_from_dict
except Exception as e:
    run_pipeline_from_dict = None
    print("RAG pipeline import failed:", e)

engine = None
SessionLocal = None

if DATABASE_URL:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


SCHEMA = "imma"


@app.get("/")
def root():
    return {"message": "ok", "database_url_exists": DATABASE_URL is not None}


@app.post("/predict")
def predict(data: dict):
    value = data.get("value", 0)
    result = "anomaly" if value > 10 else "normal"
    return {"result": result}


@app.get("/db-test")
def db_test():
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        row = result.fetchone()
    return {"db_connected": True, "result": row[0]}


# ---------------------------------------------------------------------------
# Signup
# ---------------------------------------------------------------------------

@app.post("/signup")
def signup(data: dict):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    name = data.get("name")
    email = data.get("email")
    password = data.get("password")
    phone = data.get("phone")
    role = data.get("role", "buyer")

    if not name or not email or not password:
        raise HTTPException(status_code=400, detail="name, email, password are required")

    pw_hash = _hash_password(password)

    with engine.begin() as conn:
        if role == "supplier":
            result = conn.execute(
                text(f"""
                    INSERT INTO {SCHEMA}.companies
                        (company_name, main_email, login_password_hash, main_phone, status, onboarding_status)
                    VALUES (:name, :email, :pw_hash, :phone, 'active', 'draft')
                    RETURNING company_id, company_name, main_email, onboarding_status, created_at
                """),
                {"name": name, "email": email, "pw_hash": pw_hash, "phone": phone},
            )
            row = result.fetchone()
            company_id = row[0]

            conn.execute(
                text(f"""
                    INSERT INTO {SCHEMA}.company_availability_snapshot (company_id, overall_status)
                    VALUES (:cid, 'available')
                    ON CONFLICT (company_id) DO NOTHING
                """),
                {"cid": company_id},
            )

            return {
                "message": "signup success",
                "user": {
                    "id": str(company_id),
                    "name": row[1],
                    "email": row[2],
                    "role": "supplier",
                    "onboarding_status": row[3],
                    "created_at": str(row[4]),
                },
            }
        else:
            result = conn.execute(
                text(f"""
                    INSERT INTO {SCHEMA}.buyers
                        (buyer_name, email, password_hash, phone)
                    VALUES (:name, :email, :pw_hash, :phone)
                    RETURNING buyer_id, buyer_name, email, created_at
                """),
                {"name": name, "email": email, "pw_hash": pw_hash, "phone": phone},
            )
            row = result.fetchone()
            return {
                "message": "signup success",
                "user": {
                    "id": str(row[0]),
                    "name": row[1],
                    "email": row[2],
                    "role": "buyer",
                    "onboarding_status": "not_required",
                    "created_at": str(row[3]),
                },
            }


# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------

@app.get("/companies")
def get_companies():
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        result = conn.execute(text(f"""
            SELECT
                c.company_id    AS company_code,
                c.company_name,
                COALESCE(s.region, s.city, '') AS region,
                COALESCE(c.company_size, 'unknown') AS company_size,
                c.status
            FROM {SCHEMA}.companies c
            LEFT JOIN {SCHEMA}.company_sites s
                ON c.company_id = s.company_id AND s.is_primary = true
            ORDER BY c.company_name
        """))
        rows = result.fetchall()

    data = []
    for row in rows:
        data.append({
            "company_code": str(row[0]),
            "company_name": row[1],
            "company_type": "supplier",
            "region": row[2],
            "company_size": row[3],
            "status": row[4],
        })

    return {"count": len(data), "companies": data}


@app.get("/companies/buyers")
def get_buyers():
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        result = conn.execute(text(f"""
            SELECT
                buyer_id         AS company_code,
                buyer_name       AS company_name,
                COALESCE(region, '') AS region,
                COALESCE(company_scale, '') AS company_size
            FROM {SCHEMA}.buyers
            ORDER BY buyer_name
        """))
        rows = result.fetchall()

    data = []
    for row in rows:
        data.append({
            "company_code": str(row[0]),
            "company_name": row[1],
            "region": row[2],
            "company_size": row[3],
        })

    return {"count": len(data), "buyers": data}


@app.get("/companies/suppliers")
def get_suppliers():
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        result = conn.execute(text(f"""
            SELECT
                c.company_id    AS company_code,
                c.company_name,
                COALESCE(s.region, s.city, '') AS region,
                COALESCE(c.company_size, '')    AS company_size
            FROM {SCHEMA}.companies c
            LEFT JOIN {SCHEMA}.company_sites s
                ON c.company_id = s.company_id AND s.is_primary = true
            WHERE c.status = 'active'
            ORDER BY c.company_name
        """))
        rows = result.fetchall()

    data = []
    for row in rows:
        data.append({
            "company_code": str(row[0]),
            "company_name": row[1],
            "region": row[2],
            "company_size": row[3],
        })

    return {"count": len(data), "suppliers": data}


# ---------------------------------------------------------------------------
# RFQ
# ---------------------------------------------------------------------------

@app.post("/rfq")
def create_rfq(data: dict):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    buyer_code = data.get("buyer_code")
    material = data.get("material")
    process = data.get("process")
    quantity = data.get("quantity")
    due_date = data.get("due_date")
    note = data.get("note")

    if not buyer_code or not material or not process or not quantity:
        raise HTTPException(
            status_code=400,
            detail="buyer_code, material, process, quantity are required",
        )

    with engine.begin() as conn:
        rfq_result = conn.execute(
            text(f"""
                INSERT INTO {SCHEMA}.rfqs (buyer_id, requested_delivery_date, general_notes_jsonb)
                VALUES (:buyer_id, :due_date, :notes::jsonb)
                RETURNING rfq_id, created_at
            """),
            {
                "buyer_id": buyer_code,
                "due_date": due_date,
                "notes": json.dumps({"note": note}, ensure_ascii=False),
            },
        )
        rfq_row = rfq_result.fetchone()
        rfq_id = rfq_row[0]
        created_at = rfq_row[1]

        part_result = conn.execute(
            text(f"""
                INSERT INTO {SCHEMA}.rfq_parts (rfq_id, part_name, material_raw_text, quantity)
                VALUES (:rfq_id, :part_name, :material, :quantity)
                RETURNING rfq_part_id
            """),
            {
                "rfq_id": rfq_id,
                "part_name": material,
                "material": material,
                "quantity": quantity,
            },
        )
        part_row = part_result.fetchone()
        rfq_part_id = part_row[0]

        for proc in process.split(","):
            proc = proc.strip()
            if proc:
                conn.execute(
                    text(f"""
                        INSERT INTO {SCHEMA}.rfq_part_processes (rfq_part_id, process_code)
                        VALUES (:rfq_part_id, :process_code)
                        ON CONFLICT DO NOTHING
                    """),
                    {"rfq_part_id": rfq_part_id, "process_code": proc},
                )

    return {
        "message": "rfq created",
        "rfq": {
            "id": str(rfq_id),
            "buyer_code": str(buyer_code),
            "material": material,
            "process": process,
            "quantity": quantity,
            "due_date": due_date,
            "note": note,
            "created_at": str(created_at),
        },
    }


@app.get("/rfqs")
def get_rfqs():
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        result = conn.execute(text(f"""
            SELECT
                r.rfq_id                    AS id,
                r.buyer_id                  AS buyer_code,
                rp.material_raw_text        AS material,
                string_agg(DISTINCT rpp.process_code, ', ') AS process,
                rp.quantity,
                r.requested_delivery_date   AS due_date,
                r.general_notes_jsonb->>'note' AS note,
                r.created_at
            FROM {SCHEMA}.rfqs r
            LEFT JOIN {SCHEMA}.rfq_parts rp ON r.rfq_id = rp.rfq_id
            LEFT JOIN {SCHEMA}.rfq_part_processes rpp ON rp.rfq_part_id = rpp.rfq_part_id
            GROUP BY r.rfq_id, r.buyer_id, rp.material_raw_text, rp.quantity,
                     r.requested_delivery_date, r.general_notes_jsonb, r.created_at
            ORDER BY r.created_at DESC
        """))
        rows = result.fetchall()

    data = []
    for row in rows:
        data.append({
            "id": str(row[0]),
            "buyer_code": str(row[1]) if row[1] else None,
            "material": row[2],
            "process": row[3],
            "quantity": row[4],
            "due_date": str(row[5]) if row[5] else None,
            "note": row[6],
            "created_at": str(row[7]),
        })

    return {"count": len(data), "rfqs": data}


# ---------------------------------------------------------------------------
# Matching v1 (imma MV 기반으로 개조)
# ---------------------------------------------------------------------------

@app.get("/match/{rfq_id}")
def match_suppliers(rfq_id: str):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        rfq_result = conn.execute(
            text(f"""
                SELECT
                    r.rfq_id, r.buyer_id,
                    rp.material_raw_text,
                    string_agg(DISTINCT rpp.process_code, ', '),
                    rp.quantity,
                    r.requested_delivery_date,
                    r.general_notes_jsonb->>'note'
                FROM {SCHEMA}.rfqs r
                LEFT JOIN {SCHEMA}.rfq_parts rp ON r.rfq_id = rp.rfq_id
                LEFT JOIN {SCHEMA}.rfq_part_processes rpp ON rp.rfq_part_id = rpp.rfq_part_id
                WHERE r.rfq_id = :rfq_id
                GROUP BY r.rfq_id, r.buyer_id, rp.material_raw_text, rp.quantity,
                         r.requested_delivery_date, r.general_notes_jsonb
            """),
            {"rfq_id": rfq_id},
        )
        rfq = rfq_result.fetchone()

        if rfq is None:
            raise HTTPException(status_code=404, detail="RFQ not found")

        material = rfq[2] or ""
        process_csv = rfq[3] or ""
        process_codes = [p.strip() for p in process_csv.split(",") if p.strip()]

        match_result = conn.execute(
            text(f"""
                SELECT
                    cs.company_id,
                    cs.company_name,
                    COALESCE(s.region, s.city, '') AS region,
                    COALESCE(c.company_size, '')    AS company_size,
                    cs.process_codes,
                    cs.best_it_grade,
                    cs.best_ra_um,
                    cs.avg_rating_overall,
                    cs.overall_status,
                    cs.material_codes,
                    cs.material_category_codes
                FROM {SCHEMA}.company_capability_summary cs
                JOIN {SCHEMA}.companies c ON cs.company_id = c.company_id
                LEFT JOIN {SCHEMA}.company_sites s
                    ON c.company_id = s.company_id AND s.is_primary = true
                WHERE cs.overall_status IN ('available', 'limited', 'unknown')
                  AND (
                      cs.material_codes @> ARRAY[upper(:material)]::text[]
                      OR cs.material_category_codes @> ARRAY[lower(:material)]::text[]
                  )
            """),
            {"material": material},
        )
        rows = match_result.fetchall()

    suppliers = []
    for row in rows:
        company_process_codes = row[4] or []
        matched = [p for p in process_codes if p in company_process_codes]
        if not matched and process_codes:
            continue

        best_it = row[5] or 10
        best_ra = row[6] or 3.2
        avg_rating = row[7] or 3.0

        match_score = 100
        match_score += (10 - best_it) * 4
        if best_ra <= 0.8:
            match_score += 15
        elif best_ra <= 1.6:
            match_score += 10
        match_score += int((avg_rating - 3.0) * 10)

        suppliers.append({
            "company_code": str(row[0]),
            "company_name": row[1],
            "region": row[2],
            "company_size": row[3],
            "match_score": match_score,
            "matched_processes": ", ".join(matched) if matched else process_csv,
            "service_mode": "in_house",
            "best_it_grade": best_it,
            "best_tolerance_mm": None,
            "best_ra_um": best_ra,
            "avg_lead_days": None,
            "matched_materials": ", ".join(row[9] or []),
            "score_reason": {
                "it_grade": "IT 등급 숫자가 낮을수록 정밀도 우수",
                "surface_roughness": "Ra 값이 낮을수록 표면 품질 우수",
                "rating": "업체 평점 반영",
            },
        })

    suppliers.sort(key=lambda x: x["match_score"], reverse=True)

    return {
        "rfq": {
            "id": str(rfq[0]),
            "buyer_code": str(rfq[1]) if rfq[1] else None,
            "material": rfq[2],
            "process": rfq[3],
            "quantity": rfq[4],
            "due_date": str(rfq[5]) if rfq[5] else None,
            "note": rfq[6],
        },
        "match_count": len(suppliers),
        "recommended_suppliers": suppliers,
    }


# ---------------------------------------------------------------------------
# Matching v2 (RAG pipeline)
# ---------------------------------------------------------------------------

@app.post("/api/match-v2")
def match_v2(data: dict):
    if run_pipeline_from_dict is None:
        raise HTTPException(status_code=500, detail="RAG pipeline is not loaded")
    if not data:
        raise HTTPException(status_code=400, detail="요청 본문이 비어 있습니다")
    if "parts" not in data:
        raise HTTPException(status_code=400, detail="parts 필드가 필요합니다")
    try:
        return run_pipeline_from_dict(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# VLM Results
# ---------------------------------------------------------------------------

@app.post("/vlm-result")
def save_vlm_result(data: dict):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    drawing_no = data.get("drawing_no")

    with engine.begin() as conn:
        result = conn.execute(
            text(f"""
                INSERT INTO {SCHEMA}.drawings (drawing_no, vlm_result_jsonb)
                VALUES (:drawing_no, :raw_json::jsonb)
                RETURNING drawing_id, drawing_no, vlm_result_jsonb, created_at
            """),
            {
                "drawing_no": drawing_no,
                "raw_json": json.dumps(data, ensure_ascii=False),
            },
        )
        row = result.fetchone()

    return {
        "success": True,
        "message": "vlm result saved",
        "data": {
            "id": str(row[0]),
            "drawing_no": row[1],
            "raw_json": row[2],
            "created_at": str(row[3]),
        },
    }


@app.get("/vlm-results")
def get_vlm_results():
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        result = conn.execute(text(f"""
            SELECT drawing_id, drawing_no, vlm_result_jsonb, created_at
            FROM {SCHEMA}.drawings
            ORDER BY created_at DESC
        """))
        rows = result.fetchall()

    data = []
    for row in rows:
        data.append({
            "id": str(row[0]),
            "drawing_no": row[1],
            "raw_json": row[2],
            "created_at": str(row[3]),
        })

    return {"success": True, "count": len(data), "data": data}


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------

@app.get("/api/reviews")
def get_reviews():
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        result = conn.execute(text(f"""
            SELECT
                b.buyer_name,
                r.rating_overall,
                r.rating_quality,
                r.rating_delivery,
                r.rating_communication,
                r.comment
            FROM {SCHEMA}.reviews r
            LEFT JOIN {SCHEMA}.buyers b ON r.buyer_id = b.buyer_id
            ORDER BY r.rating_overall DESC
        """))
        rows = result.fetchall()

    data = []
    for row in rows:
        data.append({
            "buyer_name": row[0],
            "rating_overall": float(row[1]) if row[1] else None,
            "rating_quality": float(row[2]) if row[2] else None,
            "rating_delivery": float(row[3]) if row[3] else None,
            "rating_communication": float(row[4]) if row[4] else None,
            "comment": row[5],
        })

    return {"status": "success", "data": data}


# ---------------------------------------------------------------------------
# Selection APIs (from pipeline api.py)
# ---------------------------------------------------------------------------

@app.get("/api/processes")
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
    return [{"process_code": r[0], "process_name_ko": r[1],
             "process_name_en": r[2], "process_group": r[3]} for r in rows]


@app.get("/api/material-categories")
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
    data = [{"category_code": r[0], "category_name_ko": r[1],
             "category_name_en": r[2]} for r in rows]
    data.append({"category_code": "other", "category_name_ko": "기타 (직접 입력)",
                 "category_name_en": "Other (manual input)"})
    return data


@app.get("/api/materials")
def get_materials(category: str = ""):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
    with engine.connect() as conn:
        result = conn.execute(text(f"""
            SELECT material_code, material_name_ko
            FROM {SCHEMA}.materials
            WHERE category_code = :cat AND is_active = true
            ORDER BY material_code
        """), {"cat": category})
        rows = result.fetchall()
    data = [{"material_code": r[0], "material_name_ko": r[1]} for r in rows]
    data.append({"material_code": "__other__",
                 "material_name_ko": "기타 (직접 입력)"})
    return data


@app.get("/api/equipment-categories")
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
    return [{"equipment_category_code": r[0], "category_name_ko": r[1],
             "category_name_en": r[2]} for r in rows]


@app.get("/api/equipment-models")
def get_equipment_models(category: str = ""):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
    with engine.connect() as conn:
        result = conn.execute(text(f"""
            SELECT model_id, manufacturer, model_name
            FROM {SCHEMA}.equipment_model_catalog
            WHERE equipment_category_code = :cat
            ORDER BY manufacturer, model_name
        """), {"cat": category})
        rows = result.fetchall()
    return [{"model_id": r[0], "manufacturer": r[1], "model_name": r[2]} for r in rows]


@app.get("/api/health")
def health():
    if engine is None:
        return {"status": "error", "db": False}
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "db": True}
    except Exception:
        return {"status": "error", "db": False}


# ---------------------------------------------------------------------------
# Onboarding APIs (업체 역량 등록)
# ---------------------------------------------------------------------------

def _refresh_mv(conn):
    conn.execute(text(f"REFRESH MATERIALIZED VIEW {SCHEMA}.company_capability_summary"))


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
                WHERE company_id = :cid AND onboarding_status IN ('draft', 'submitted')
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


@app.post("/api/equipment")
def register_equipment(data: dict):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    company_id = data.get("company_id")
    model_id = data.get("model_id")
    display_name = data.get("display_name")
    category_code = data.get("equipment_category_code")

    if not company_id or not display_name or not category_code:
        raise HTTPException(status_code=400, detail="company_id, display_name, equipment_category_code are required")

    with engine.begin() as conn:
        catalog_specs = None
        catalog_procs = []
        if model_id:
            cat_row = conn.execute(
                text(f"""
                    SELECT category_specs, process_capabilities
                    FROM {SCHEMA}.equipment_model_catalog WHERE model_id = :mid
                """),
                {"mid": model_id},
            ).fetchone()
            if cat_row:
                catalog_specs = cat_row[0] or {}
                catalog_procs = cat_row[1] or []

        cs = catalog_specs or {}
        best_it = None
        best_ra = None
        for pc in catalog_procs:
            it = pc.get("typical_it_grade")
            ra = pc.get("typical_ra_um")
            if it is not None and (best_it is None or it < best_it):
                best_it = it
            if ra is not None and (best_ra is None or ra < best_ra):
                best_ra = ra

        eq_row = conn.execute(
            text(f"""
                INSERT INTO {SCHEMA}.equipment
                    (company_id, equipment_category_code, model_id, display_name,
                     manufacturer, model_name,
                     max_turning_diameter_mm, max_turning_length_mm,
                     max_x_travel_mm, max_y_travel_mm, max_z_travel_mm,
                     max_workpiece_weight_kg,
                     best_achievable_it_grade, best_ra_um,
                     spindle_max_rpm, spindle_power_kw,
                     axis_count, status)
                VALUES (:cid, :cat, :mid, :dn,
                        :mfr, :mn,
                        :td, :tl, :mx, :my, :mz, :wt,
                        :it, :ra, :rpm, :kw, :ax, 'running')
                RETURNING equipment_id
            """),
            {
                "cid": company_id, "cat": category_code, "mid": model_id, "dn": display_name,
                "mfr": cs.get("manufacturer"), "mn": cs.get("model_name"),
                "td": cs.get("max_turning_diameter_mm"), "tl": cs.get("max_turning_length_mm") or cs.get("center_distance_mm"),
                "mx": cs.get("max_x_travel_mm"), "my": cs.get("max_y_travel_mm"), "mz": cs.get("max_z_travel_mm"),
                "wt": cs.get("max_workpiece_weight_kg"),
                "it": best_it, "ra": best_ra,
                "rpm": cs.get("spindle_max_rpm"), "kw": cs.get("spindle_power_kw"),
                "ax": cs.get("axis_count"),
            },
        ).fetchone()
        equipment_id = eq_row[0]

        for pc in catalog_procs:
            pcode = pc.get("process_code")
            if pcode:
                conn.execute(
                    text(f"""
                        INSERT INTO {SCHEMA}.equipment_process_capabilities
                            (equipment_id, process_code, best_achievable_it_grade, best_ra_um)
                        VALUES (:eid, :pc, :it, :ra)
                        ON CONFLICT (equipment_id, process_code) DO NOTHING
                    """),
                    {"eid": equipment_id, "pc": pcode,
                     "it": pc.get("typical_it_grade"), "ra": pc.get("typical_ra_um")},
                )

                conn.execute(
                    text(f"""
                        INSERT INTO {SCHEMA}.company_process_capabilities
                            (company_id, process_code, best_achievable_it_grade, best_ra_um)
                        VALUES (:cid, :pc, :it, :ra)
                        ON CONFLICT DO NOTHING
                    """),
                    {"cid": company_id, "pc": pcode,
                     "it": pc.get("typical_it_grade"), "ra": pc.get("typical_ra_um")},
                )

        status = _check_onboarding(conn, company_id)

    return {
        "success": True,
        "equipment_id": str(equipment_id),
        "auto_generated_processes": [pc.get("process_code") for pc in catalog_procs if pc.get("process_code")],
        "onboarding_status": status,
    }


@app.post("/api/material-capability")
def register_material_capability(data: dict):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    company_id = data.get("company_id")
    materials = data.get("materials", [])
    categories = data.get("categories", [])

    if not company_id or (not materials and not categories):
        raise HTTPException(status_code=400, detail="company_id and at least one material or category required")

    with engine.begin() as conn:
        for mat_code in materials:
            mat_row = conn.execute(
                text(f"SELECT material_id FROM {SCHEMA}.materials WHERE material_code = :mc"),
                {"mc": mat_code},
            ).fetchone()
            if not mat_row:
                continue
            conn.execute(
                text(f"""
                    INSERT INTO {SCHEMA}.company_material_capabilities
                        (company_id, scope_type, material_id, capability_level)
                    VALUES (:cid, 'specific_material', :mid, 'regular')
                    ON CONFLICT DO NOTHING
                """),
                {"cid": company_id, "mid": mat_row[0]},
            )

        for cat_code in categories:
            conn.execute(
                text(f"""
                    INSERT INTO {SCHEMA}.company_material_capabilities
                        (company_id, scope_type, material_category_code, capability_level)
                    VALUES (:cid, 'material_category', :cc, 'regular')
                    ON CONFLICT DO NOTHING
                """),
                {"cid": company_id, "cc": cat_code},
            )

        status = _check_onboarding(conn, company_id)

    return {"success": True, "onboarding_status": status}


@app.post("/api/process-capability")
def register_process_capability(data: dict):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    company_id = data.get("company_id")
    processes = data.get("processes", [])

    if not company_id or not processes:
        raise HTTPException(status_code=400, detail="company_id and processes required")

    with engine.begin() as conn:
        for proc in processes:
            conn.execute(
                text(f"""
                    INSERT INTO {SCHEMA}.company_process_capabilities
                        (company_id, process_code, service_mode,
                         best_achievable_it_grade, best_ra_um, typical_lead_days)
                    VALUES (:cid, :pc, :sm, :it, :ra, :ld)
                    ON CONFLICT DO NOTHING
                """),
                {
                    "cid": company_id,
                    "pc": proc.get("process_code"),
                    "sm": proc.get("service_mode", "in_house"),
                    "it": proc.get("best_it_grade"),
                    "ra": proc.get("best_ra_um"),
                    "ld": proc.get("typical_lead_days"),
                },
            )

        status = _check_onboarding(conn, company_id)

    return {"success": True, "onboarding_status": status}


@app.put("/api/company/profile")
def update_company_profile(data: dict):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    company_id = data.get("company_id")
    if not company_id:
        raise HTTPException(status_code=400, detail="company_id is required")

    with engine.begin() as conn:
        fields = {}
        set_clauses = []
        for col in ["business_registration_no", "representative_name", "main_phone",
                     "company_size", "employee_count", "established_year", "website_url"]:
            if col in data:
                fields[col] = data[col]
                set_clauses.append(f"{col} = :{col}")

        if set_clauses:
            conn.execute(
                text(f"""
                    UPDATE {SCHEMA}.companies SET {', '.join(set_clauses)}, updated_at = now()
                    WHERE company_id = :company_id
                """),
                {**fields, "company_id": company_id},
            )

        if any(k in data for k in ["region", "city", "address", "postal_code"]):
            conn.execute(
                text(f"""
                    INSERT INTO {SCHEMA}.company_sites
                        (company_id, site_name, is_primary, region, city, address_line1, postal_code)
                    VALUES (:cid, '본사/공장', true, :region, :city, :addr, :postal)
                    ON CONFLICT (company_id, site_name) DO UPDATE SET
                        region = EXCLUDED.region, city = EXCLUDED.city,
                        address_line1 = EXCLUDED.address_line1, postal_code = EXCLUDED.postal_code
                """),
                {
                    "cid": company_id,
                    "region": data.get("region"),
                    "city": data.get("city"),
                    "addr": data.get("address"),
                    "postal": data.get("postal_code"),
                },
            )

        if any(k in data for k in ["contact_name", "contact_phone", "contact_email", "role_title"]):
            conn.execute(
                text(f"""
                    INSERT INTO {SCHEMA}.company_contacts
                        (company_id, contact_name, role_title, phone, email, is_primary)
                    VALUES (:cid, :cn, :rt, :cp, :ce, true)
                    ON CONFLICT (company_id, contact_name) DO UPDATE SET
                        role_title = EXCLUDED.role_title, phone = EXCLUDED.phone, email = EXCLUDED.email
                """),
                {
                    "cid": company_id,
                    "cn": data.get("contact_name"),
                    "rt": data.get("role_title"),
                    "cp": data.get("contact_phone"),
                    "ce": data.get("contact_email"),
                },
            )

        status = _check_onboarding(conn, company_id)

    return {"success": True, "onboarding_status": status}


@app.put("/api/company/availability")
def update_availability(data: dict):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    company_id = data.get("company_id")
    if not company_id:
        raise HTTPException(status_code=400, detail="company_id is required")

    with engine.begin() as conn:
        conn.execute(
            text(f"""
                UPDATE {SCHEMA}.company_availability_snapshot
                SET overall_status = :st,
                    next_available_date = :nad,
                    current_utilization_pct = :util,
                    min_lead_time_days = :mlt,
                    last_updated_at = now()
                WHERE company_id = :cid
            """),
            {
                "cid": company_id,
                "st": data.get("overall_status", "available"),
                "nad": data.get("next_available_date"),
                "util": data.get("current_utilization_pct"),
                "mlt": data.get("min_lead_time_days"),
            },
        )
        _refresh_mv(conn)

    return {"success": True}


@app.post("/api/reviews")
def create_review(data: dict):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    company_id = data.get("company_id")
    buyer_id = data.get("buyer_id")
    rating_overall = data.get("rating_overall")

    if not company_id or not rating_overall:
        raise HTTPException(status_code=400, detail="company_id and rating_overall are required")

    with engine.begin() as conn:
        conn.execute(
            text(f"""
                INSERT INTO {SCHEMA}.reviews
                    (company_id, buyer_id, rating_overall, rating_quality,
                     rating_delivery, rating_communication, rating_price, comment)
                VALUES (:cid, :bid, :ro, :rq, :rd, :rc, :rp, :cm)
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
        _refresh_mv(conn)

    return {"success": True}


@app.post("/api/quote")
def create_quote(data: dict):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    rfq_id = data.get("rfq_id")
    company_id = data.get("company_id")
    total_price = data.get("total_price")
    estimated_lead_days = data.get("estimated_lead_days")

    if not rfq_id or not company_id:
        raise HTTPException(status_code=400, detail="rfq_id and company_id are required")

    with engine.begin() as conn:
        q_row = conn.execute(
            text(f"""
                INSERT INTO {SCHEMA}.quote_responses
                    (rfq_id, company_id, total_price, estimated_lead_days,
                     proposed_delivery_date, validity_until, assumptions, status)
                VALUES (:rid, :cid, :tp, :eld, :pdd, :vu, :asm, 'submitted')
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
                    "pc": item.get("process_code"),
                    "desc": item.get("description"),
                    "qty": item.get("quantity"),
                    "up": item.get("unit_price"),
                    "lt": item.get("line_total"),
                    "notes": item.get("notes"),
                },
            )

    return {
        "success": True,
        "quote_id": str(quote_id),
        "created_at": str(q_row[1]),
    }
