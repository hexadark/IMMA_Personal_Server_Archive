"""
도면 관련 엔드포인트:
- POST /vlm-result            (기존)
- GET  /vlm-results            (기존)
- POST /api/drawings/upload    (도면 파일 업로드)
- GET  /api/drawings/{id}/download  (도면 접근 권한 제어 + 다운로드)
"""

import json
import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy import text

from routers.deps import engine, SCHEMA, get_current_user

router = APIRouter()

# uploads/ 폴더 경로
UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"


# ---------------------------------------------------------------------------
# 기존: VLM Result 저장
# ---------------------------------------------------------------------------


@router.post("/vlm-result")
def save_vlm_result(data: dict, user: dict = Depends(get_current_user)):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
    if user["role"] not in ("buyer", "admin"):
        raise HTTPException(status_code=403, detail="buyer 또는 admin만 도면을 저장할 수 있습니다")

    raw_json = data.get("raw_json", data)

    drawing_no = (
        data.get("drawing_no")
        or data.get("drawing_id")
        or raw_json.get("drawing_id")
        or "unknown"
    )

    file_uri = (
        data.get("file_uri")
        or data.get("image_path")
        or raw_json.get("image_path")
    )

    original_filename = None
    if file_uri:
        original_filename = file_uri.split("/")[-1]

    buyer_id_value = user["id"] if user["role"] == "buyer" else None

    with engine.begin() as conn:
        result = conn.execute(
            text(f"""
                INSERT INTO {SCHEMA}.drawings
                    (drawing_no, file_uri, original_filename, vlm_result_jsonb, buyer_id)
                VALUES
                    (:drawing_no, :file_uri, :original_filename, CAST(:raw_json AS JSONB),
                     CAST(:buyer_id AS uuid))
                RETURNING
                    drawing_id, drawing_no, file_uri, original_filename,
                    vlm_result_jsonb, created_at
            """),
            {
                "drawing_no": drawing_no,
                "file_uri": file_uri,
                "original_filename": original_filename,
                "raw_json": json.dumps(raw_json, ensure_ascii=False),
                "buyer_id": buyer_id_value,
            },
        )
        row = result.fetchone()

    return {
        "success": True,
        "message": "vlm result saved",
        "data": {
            "id": str(row[0]),
            "drawing_no": row[1],
            "file_uri": row[2],
            "original_filename": row[3],
            "raw_json": row[4],
            "created_at": str(row[5]),
        },
    }


# ---------------------------------------------------------------------------
# 기존: VLM Results 조회
# ---------------------------------------------------------------------------


@router.get("/vlm-results")
def get_vlm_results(user: dict = Depends(get_current_user)):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        if user["role"] == "admin":
            result = conn.execute(text(f"""
                SELECT drawing_id, drawing_no, vlm_result_jsonb, created_at
                FROM {SCHEMA}.drawings
                ORDER BY created_at DESC
            """))
        elif user["role"] == "buyer":
            result = conn.execute(
                text(f"""
                    SELECT drawing_id, drawing_no, vlm_result_jsonb, created_at
                    FROM {SCHEMA}.drawings
                    WHERE buyer_id = CAST(:uid AS uuid)
                    ORDER BY created_at DESC
                """),
                {"uid": user["id"]},
            )
        else:
            raise HTTPException(status_code=403, detail="supplier는 도면 목록을 조회할 수 없습니다")
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
# 도면 파일 업로드
# ---------------------------------------------------------------------------


@router.post("/api/drawings/upload")
async def upload_drawing(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """
    multipart 파일 업로드 → uploads/ 폴더 저장 → drawings 테이블 INSERT.
    파일명: {uuid}_{original_filename}
    sha256 해시 계산 후 drawings에 기록.
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
    if user["role"] != "buyer":
        raise HTTPException(status_code=403, detail="buyer만 도면을 업로드할 수 있습니다")

    # uploads/ 폴더 자동 생성
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # 파일 읽기
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="빈 파일입니다")

    # sha256 해시 계산
    file_sha256 = hashlib.sha256(content).hexdigest()

    # 파일 저장
    file_uuid = str(uuid.uuid4())
    original_filename = file.filename or "unknown"
    saved_name = f"{file_uuid}_{original_filename}"
    saved_path = UPLOAD_DIR / saved_name
    saved_path.write_bytes(content)

    file_uri = f"uploads/{saved_name}"

    # drawing_no: 파일명에서 확장자 제거하여 추출
    drawing_no = Path(original_filename).stem if original_filename != "unknown" else "unknown"

    with engine.begin() as conn:
        result = conn.execute(
            text(f"""
                INSERT INTO {SCHEMA}.drawings
                    (drawing_no, file_uri, file_sha256, original_filename, buyer_id)
                VALUES
                    (:drawing_no, :file_uri, :file_sha256, :original_filename,
                     CAST(:buyer_id AS uuid))
                RETURNING drawing_id
            """),
            {
                "drawing_no": drawing_no,
                "file_uri": file_uri,
                "file_sha256": file_sha256,
                "original_filename": original_filename,
                "buyer_id": user["id"],
            },
        )
        row = result.fetchone()
        drawing_id = str(row[0])

    return {
        "drawing_id": drawing_id,
        "file_uri": file_uri,
        "original_filename": original_filename,
        "file_sha256": file_sha256,
    }


# ---------------------------------------------------------------------------
# 도면 다운로드 — 접근 권한 제어
# ---------------------------------------------------------------------------


@router.get("/api/drawings/{drawing_id}/download")
def download_drawing(drawing_id: str,
                     user: dict = Depends(get_current_user)):
    """
    도면 접근 권한 제어 + 파일 다운로드.
    - buyer: 본인이 업로드한 도면이면 허용
    - supplier: 해당 도면에 연결된 orders에서 company_id가 본인이고
                status가 'contracting' 이상이면 허용. 아니면 403.
    - file_uri가 있으면 FileResponse, 없으면 vlm_result_jsonb만 반환
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        # drawing 조회
        drawing_row = conn.execute(
            text(f"""
                SELECT drawing_id, buyer_id, file_uri, original_filename,
                       vlm_result_jsonb
                FROM {SCHEMA}.drawings
                WHERE drawing_id = :did
            """),
            {"did": drawing_id},
        ).fetchone()

        if drawing_row is None:
            raise HTTPException(status_code=404, detail="도면을 찾을 수 없습니다")

        d_drawing_id = str(drawing_row[0])
        d_buyer_id = str(drawing_row[1]) if drawing_row[1] else None
        d_file_uri = drawing_row[2]
        d_original_filename = drawing_row[3]
        d_vlm_result = drawing_row[4]

        user_role = user["role"]
        user_id = user["id"]

        # 접근 권한 검증
        if user_role == "buyer":
            # buyer는 본인 도면만 허용 (소유자 미지정 도면은 차단)
            if d_buyer_id is None or user_id != d_buyer_id:
                raise HTTPException(
                    status_code=403,
                    detail="본인이 업로드한 도면만 다운로드할 수 있습니다",
                )

        elif user_role == "supplier":
            # supplier: 해당 도면에 연결된 orders에서 company_id 검증
            # drawings → rfqs (drawing_id) → orders 또는 직접 manufacturing_jobs (drawing_id)
            order_check = conn.execute(
                text(f"""
                    SELECT o.order_id
                    FROM {SCHEMA}.orders o
                    JOIN {SCHEMA}.rfqs r ON o.rfq_id = r.rfq_id
                    WHERE r.drawing_id = :did
                      AND o.company_id = :cid
                      AND o.status NOT IN ('cancelled')
                    LIMIT 1
                """),
                {"did": drawing_id, "cid": user_id},
            ).fetchone()

            if order_check is None:
                # drawing_id로 직접 manufacturing_jobs 연결 확인
                job_check = conn.execute(
                    text(f"""
                        SELECT mj.job_id
                        FROM {SCHEMA}.manufacturing_jobs mj
                        WHERE mj.drawing_id = :did
                          AND mj.company_id = :cid
                        LIMIT 1
                    """),
                    {"did": drawing_id, "cid": user_id},
                ).fetchone()

                if job_check is None:
                    raise HTTPException(
                        status_code=403,
                        detail="해당 도면에 대한 접근 권한이 없습니다. "
                               "발주가 확정된 도면만 다운로드할 수 있습니다.",
                    )
        else:
            # admin 등 다른 역할은 허용
            pass

    # 파일 반환
    if d_file_uri:
        file_path = UPLOAD_DIR.parent / d_file_uri
        if file_path.exists():
            return FileResponse(
                path=str(file_path),
                filename=d_original_filename or file_path.name,
                media_type="application/octet-stream",
            )
        else:
            # 파일이 디스크에 없지만 vlm_result는 있을 수 있음
            if d_vlm_result and d_vlm_result != {}:
                return {
                    "drawing_id": d_drawing_id,
                    "vlm_result_jsonb": d_vlm_result,
                    "note": "파일이 서버에 존재하지 않아 VLM 결과만 반환합니다",
                }
            raise HTTPException(
                status_code=404,
                detail="도면 파일이 서버에 존재하지 않습니다",
            )
    else:
        # file_uri가 없는 경우 vlm_result_jsonb만 반환
        if d_vlm_result and d_vlm_result != {}:
            return {
                "drawing_id": d_drawing_id,
                "vlm_result_jsonb": d_vlm_result,
            }
        raise HTTPException(
            status_code=404,
            detail="도면 파일과 VLM 결과 모두 존재하지 않습니다",
        )
