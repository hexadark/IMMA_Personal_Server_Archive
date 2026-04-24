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
