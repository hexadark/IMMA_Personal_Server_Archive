"""
VLM 도면 분석 엔드포인트:
- POST /vlm/analyze-upload — 도면 이미지를 Replicate VLM API로 분석하고 drawings 테이블에 raw_json 저장

흐름:
  multipart 이미지 + buyer JWT
  → Replicate API 호출 (REPLICATE_API_TOKEN, REPLICATE_MODEL_VERSION 환경변수)
  → polling (VLM_REPLICATE_TIMEOUT_SEC, VLM_REPLICATE_POLL_INTERVAL)
  → V.B raw JSON 추출
  → drawings 테이블에 INSERT (buyer_id 포함)
  → drawing_id 반환

drawing_id는 후속 POST /api/match-v2 호출의 drawing_id 파라미터로 사용된다.
"""

import logging
import os
import time
import json
import uuid
import base64
import hashlib
from pathlib import Path

import requests
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy import text

from routers.deps import engine, SCHEMA, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vlm", tags=["vlm"])


REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")
REPLICATE_MODEL_VERSION = os.getenv("REPLICATE_MODEL_VERSION")
REPLICATE_PREDICTIONS_URL = "https://api.replicate.com/v1/predictions"

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"


def _replicate_headers():
    if not REPLICATE_API_TOKEN:
        raise HTTPException(status_code=500, detail="REPLICATE_API_TOKEN is not set")
    return {
        "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
    }


@router.post("/analyze-upload")
def analyze_upload(
    image: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """도면 이미지 → Replicate VLM 분석 → drawings 테이블 저장 → drawing_id 반환.

    동기 def 로 정의 — Replicate API 의 sync requests + time.sleep 영역이
    async event loop 를 freeze 하지 않도록 FastAPI thread pool 에서 실행.
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
    if user["role"] != "buyer":
        raise HTTPException(status_code=403, detail="buyer만 도면을 분석할 수 있습니다")
    if not REPLICATE_MODEL_VERSION:
        raise HTTPException(status_code=500, detail="REPLICATE_MODEL_VERSION is not set")
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="이미지 파일만 허용됩니다")

    # ── ① 이미지 읽기 + 파일 저장 ──
    # sync 함수 영역에서는 UploadFile.file 영역의 동기 read 사용
    image_bytes = image.file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="빈 파일입니다")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_sha256 = hashlib.sha256(image_bytes).hexdigest()
    file_uuid = str(uuid.uuid4())
    original_filename = image.filename or "unknown"
    saved_name = f"{file_uuid}_{original_filename}"
    saved_path = UPLOAD_DIR / saved_name
    saved_path.write_bytes(image_bytes)
    file_uri = f"uploads/{saved_name}"

    # ── ② Replicate API 호출 ──
    encoded = base64.b64encode(image_bytes).decode("utf-8")
    image_data_uri = f"data:{image.content_type};base64,{encoded}"

    create_payload = {
        "version": REPLICATE_MODEL_VERSION,
        "input": {"image": image_data_uri},
    }
    try:
        create_res = requests.post(
            REPLICATE_PREDICTIONS_URL,
            headers=_replicate_headers(),
            json=create_payload,
            timeout=60,
        )
    except requests.RequestException:
        logger.exception("Replicate predictions create 요청 실패")
        raise HTTPException(status_code=502, detail="Replicate API 호출 실패")
    if create_res.status_code >= 400:
        logger.error("Replicate prediction create %d: %s", create_res.status_code, create_res.text[:500])
        raise HTTPException(
            status_code=create_res.status_code,
            detail={"message": "Replicate prediction create 실패", "response": create_res.text},
        )

    prediction = create_res.json()
    get_url = prediction.get("urls", {}).get("get")
    if not get_url:
        raise HTTPException(status_code=500, detail="Replicate prediction get URL 없음")

    # ── ③ Polling ──
    timeout_sec = int(os.getenv("VLM_REPLICATE_TIMEOUT_SEC", "300"))
    poll_interval = float(os.getenv("VLM_REPLICATE_POLL_INTERVAL", "2"))
    started = time.time()

    while True:
        try:
            poll_res = requests.get(get_url, headers=_replicate_headers(), timeout=60)
        except requests.RequestException:
            logger.exception("Replicate polling 요청 실패")
            raise HTTPException(status_code=502, detail="Replicate polling 호출 실패")
        if poll_res.status_code >= 400:
            logger.error("Replicate polling %d: %s", poll_res.status_code, poll_res.text[:500])
            raise HTTPException(
                status_code=poll_res.status_code,
                detail={"message": "Replicate polling 실패", "response": poll_res.text},
            )
        prediction = poll_res.json()
        status = prediction.get("status")
        if status in ("succeeded", "failed", "canceled"):
            break
        if time.time() - started > timeout_sec:
            raise HTTPException(
                status_code=504,
                detail={"message": "Replicate timeout", "prediction": prediction},
            )
        time.sleep(poll_interval)

    if prediction.get("status") != "succeeded":
        raise HTTPException(
            status_code=502,
            detail={"message": "Replicate 분석 실패", "prediction": prediction},
        )

    # ── ④ V.B raw JSON 추출 + drawings 저장 ──
    vlm_output = prediction.get("output") or {}
    if isinstance(vlm_output, str):
        # Replicate가 문자열로 반환하면 JSON parse 시도
        try:
            vlm_output = json.loads(vlm_output)
        except (json.JSONDecodeError, TypeError):
            vlm_output = {"raw_output": prediction.get("output")}

    title_block = vlm_output.get("title_block_1") or {}
    drawing_no = (
        vlm_output.get("drawing_id")
        or title_block.get("Drawing_No")
        or title_block.get("Project_ID")
        or Path(original_filename).stem
    )

    with engine.begin() as conn:
        row = conn.execute(
            text(f"""
                INSERT INTO {SCHEMA}.drawings
                    (drawing_no, file_uri, file_sha256, original_filename,
                     vlm_result_jsonb, buyer_id)
                VALUES (:drawing_no, :file_uri, :file_sha256, :original_filename,
                        CAST(:vlm_json AS JSONB), CAST(:buyer_id AS uuid))
                ON CONFLICT (file_sha256) DO UPDATE
                    SET vlm_result_jsonb = EXCLUDED.vlm_result_jsonb,
                        drawing_no = EXCLUDED.drawing_no
                RETURNING drawing_id
            """),
            {
                "drawing_no": drawing_no,
                "file_uri": file_uri,
                "file_sha256": file_sha256,
                "original_filename": original_filename,
                "vlm_json": json.dumps(vlm_output, ensure_ascii=False),
                "buyer_id": user["id"],
            },
        ).fetchone()
        drawing_id = str(row[0])

    return {
        "drawing_id": drawing_id,
        "drawing_no": drawing_no,
        "file_uri": file_uri,
        "file_sha256": file_sha256,
        "original_filename": original_filename,
        "prediction_id": prediction.get("id"),
        "status": prediction.get("status"),
        "vlm_output": vlm_output,
    }
