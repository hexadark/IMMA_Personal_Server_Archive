import os

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
    return {"message": "companies api ready"}


@app.get("/companies/buyers")
def get_buyers():
    return {"message": "buyers api ready"}


@app.get("/companies/suppliers")
def get_suppliers():
    return {"message": "suppliers api ready"}
