import os
import json

from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

app = FastAPI()

DATABASE_URL = os.getenv("DATABASE_URL")

engine = None
SessionLocal = None

if DATABASE_URL:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    SessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine
    )

@app.get("/")
def root():
    return {"message": "ok", "database_url_exists": DATABASE_URL is not None}


@app.post("/predict")
def predict(data: dict):
    value = data.get("value", 0)

    if value > 10:
        result = "anomaly"
    else:
        result = "normal"

    return {"result": result}


@app.get("/db-test")
def db_test():
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        row = result.fetchone()

    return {
        "db_connected": True,
        "result": row[0]
    }


@app.post("/signup")
def signup(data: dict):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    name = data.get("name")
    email = data.get("email")
    phone = data.get("phone")

    if not name or not email:
        raise HTTPException(status_code=400, detail="name and email are required")

    with engine.begin() as conn:
        result = conn.execute(
            text("""
                INSERT INTO users (name, email, phone)
                VALUES (:name, :email, :phone)
                RETURNING id, name, email, phone, role, created_at
            """),
            {
                "name": name,
                "email": email,
                "phone": phone
            }
        )

        user = result.fetchone()

    return {
        "message": "signup success",
        "user": {
            "id": user[0],
            "name": user[1],
            "email": user[2],
            "phone": user[3],
            "role": user[4],
            "created_at": str(user[5])
        }
    }


# ---------------------------
# Companies API
# ---------------------------

@app.get("/companies")
def get_companies():
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT company_code, company_name, company_type, region, company_size, status
            FROM companies
            ORDER BY company_code
        """))

        rows = result.fetchall()

    data = []
    for row in rows:
        data.append({
            "company_code": row[0],
            "company_name": row[1],
            "company_type": row[2],
            "region": row[3],
            "company_size": row[4],
            "status": row[5]
        })

    return {"count": len(data), "companies": data}


@app.get("/companies/buyers")
def get_buyers():
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT company_code, company_name, region, company_size
            FROM companies
            WHERE company_type = 'buyer'
            ORDER BY company_code
        """))

        rows = result.fetchall()

    data = []
    for row in rows:
        data.append({
            "company_code": row[0],
            "company_name": row[1],
            "region": row[2],
            "company_size": row[3]
        })

    return {"count": len(data), "buyers": data}


@app.get("/companies/suppliers")
def get_suppliers():
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT company_code, company_name, region, company_size
            FROM companies
            WHERE company_type = 'supplier'
            ORDER BY company_code
        """))

        rows = result.fetchall()

    data = []
    for row in rows:
        data.append({
            "company_code": row[0],
            "company_name": row[1],
            "region": row[2],
            "company_size": row[3]
        })

    return {"count": len(data), "suppliers": data}


# ---------------------------
# RFQ API
# ---------------------------

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
            detail="buyer_code, material, process, quantity are required"
        )

    with engine.begin() as conn:
        result = conn.execute(
            text("""
                INSERT INTO rfqs
                (buyer_code, material, process, quantity, due_date, note)
                VALUES
                (:buyer_code, :material, :process, :quantity, :due_date, :note)
                RETURNING id, buyer_code, material, process, quantity, due_date, note, created_at
            """),
            {
                "buyer_code": buyer_code,
                "material": material,
                "process": process,
                "quantity": quantity,
                "due_date": due_date,
                "note": note
            }
        )

        rfq = result.fetchone()

    return {
        "message": "rfq created",
        "rfq": {
            "id": rfq[0],
            "buyer_code": rfq[1],
            "material": rfq[2],
            "process": rfq[3],
            "quantity": rfq[4],
            "due_date": rfq[5],
            "note": rfq[6],
            "created_at": str(rfq[7])
        }
    }


@app.get("/rfqs")
def get_rfqs():
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT id, buyer_code, material, process, quantity, due_date, note, created_at
            FROM rfqs
            ORDER BY id DESC
        """))

        rows = result.fetchall()

    data = []
    for row in rows:
        data.append({
            "id": row[0],
            "buyer_code": row[1],
            "material": row[2],
            "process": row[3],
            "quantity": row[4],
            "due_date": row[5],
            "note": row[6],
            "created_at": str(row[7])
        })

    return {"count": len(data), "rfqs": data}


# ---------------------------
# Matching API
# ---------------------------

@app.get("/match/{rfq_id}")
def match_suppliers(rfq_id: int):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        rfq_result = conn.execute(
            text("""
                SELECT id, buyer_code, material, process, quantity, due_date, note
                FROM rfqs
                WHERE id = :rfq_id
            """),
            {"rfq_id": rfq_id}
        )

        rfq = rfq_result.fetchone()

        if rfq is None:
            raise HTTPException(status_code=404, detail="RFQ not found")

        match_result = conn.execute(
            text("""
                SELECT
                    c.company_code,
                    c.company_name,
                    c.region,
                    c.company_size,
                    pc.processes,
                    pc.service_mode,
                    pc.best_it_grade,
                    pc.best_tolerance_mm,
                    pc.best_ra_um,
                    pc.avg_lead_days,
                    mc.materials
                FROM companies c
                JOIN process_capabilities pc
                    ON c.company_code = pc.company_code
                JOIN material_capabilities mc
                    ON c.company_code = mc.company_code
                WHERE c.company_type = 'supplier'
                  AND pc.processes ILIKE '%' || :process || '%'
                  AND mc.materials ILIKE '%' || :material || '%'
            """),
            {
                "process": rfq[3],
                "material": rfq[2]
            }
        )

        rows = match_result.fetchall()

    suppliers = []
    for row in rows:
        best_it_grade = row[6]
        best_tolerance_mm = row[7]
        best_ra_um = row[8]
        avg_lead_days = row[9]

        match_score = 100
        match_score -= avg_lead_days * 3
        match_score += (10 - best_it_grade) * 4

        if best_tolerance_mm <= 0.01:
            match_score += 15
        elif best_tolerance_mm <= 0.02:
            match_score += 10

        if best_ra_um <= 0.8:
            match_score += 15
        elif best_ra_um <= 1.6:
            match_score += 10

        suppliers.append({
            "company_code": row[0],
            "company_name": row[1],
            "region": row[2],
            "company_size": row[3],
            "match_score": match_score,
            "matched_processes": row[4],
            "service_mode": row[5],
            "best_it_grade": best_it_grade,
            "best_tolerance_mm": best_tolerance_mm,
            "best_ra_um": best_ra_um,
            "avg_lead_days": avg_lead_days,
            "matched_materials": row[10],
            "score_reason": {
                "lead_time": "납기가 짧을수록 높은 점수",
                "it_grade": "IT 등급 숫자가 낮을수록 정밀도 우수",
                "tolerance": "공차 mm 값이 작을수록 높은 점수",
                "surface_roughness": "Ra 값이 낮을수록 표면 품질 우수"
            }
        })

    suppliers = sorted(
        suppliers,
        key=lambda x: x["match_score"],
        reverse=True
    )

    return {
        "rfq": {
            "id": rfq[0],
            "buyer_code": rfq[1],
            "material": rfq[2],
            "process": rfq[3],
            "quantity": rfq[4],
            "due_date": rfq[5],
            "note": rfq[6]
        },
        "match_count": len(suppliers),
        "recommended_suppliers": suppliers
    }

@app.post("/vlm-result")
def save_vlm_result(data: dict):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    drawing_no = data.get("drawing_no")

    with engine.begin() as conn:
        result = conn.execute(
            text("""
                INSERT INTO vlm_rag_results (drawing_no, raw_json)
                VALUES (:drawing_no, CAST(:raw_json AS jsonb))
                RETURNING id, drawing_no, raw_json, created_at
            """),
            {
                "drawing_no": drawing_no,
                "raw_json": json.dumps(data, ensure_ascii=False)
            }
        )

        row = result.fetchone()

    return {
        "success": True,
        "message": "vlm result saved",
        "data": {
            "id": row[0],
            "drawing_no": row[1],
            "raw_json": row[2],
            "created_at": str(row[3])
        }
    }


@app.get("/vlm-results")
def get_vlm_results():
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT id, drawing_no, raw_json, created_at
            FROM vlm_rag_results
            ORDER BY id DESC
        """))

        rows = result.fetchall()

    data = []
    for row in rows:
        data.append({
            "id": row[0],
            "drawing_no": row[1],
            "raw_json": row[2],
            "created_at": str(row[3])
        })

    return {
        "success": True,
        "count": len(data),
        "data": data
    }
