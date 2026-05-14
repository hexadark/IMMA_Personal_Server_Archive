"""IMMA Phase 1 매칭 파이프라인 — 진입점.

JSON 파일을 받아서 parse -> DB 저장 -> resolve -> match -> response 체이닝을 수행한다.
"""

import argparse
import json
import logging
import sys
import hashlib
from pathlib import Path

import db

logger = logging.getLogger(__name__)
from parse import parse_vlm_json
from resolve import resolve_part
from match import run_hard_filter, run_equipment_verification, check_process_sequence
from response import assemble_response, to_json


def _ensure_buyer(conn, buyer_name: str = "CLI_테스트_바이어") -> str:
    """buyers 테이블에 mock buyer를 INSERT하거나 기존 buyer를 반환한다."""
    rows = db.execute_query_with_conn(
        conn,
        "SELECT buyer_id FROM imma.buyers WHERE buyer_name = %s",
        (buyer_name,),
    )
    if rows:
        return str(rows[0]["buyer_id"])
    row = db.execute_returning_with_conn(
        conn,
        """INSERT INTO imma.buyers (buyer_name, login_id, email, password_hash)
           VALUES (%s, %s, %s, %s) RETURNING buyer_id""",
        (buyer_name, "cli_pipeline", "cli_pipeline@imma.local",
         "cli_no_login:0000000000000000000000000000000000000000000000000000000000000000"),
    )
    return str(row[0])


def _insert_drawing(conn, buyer_id: str, file_uri: str, vlm_json: dict,
                    drawing_id: str | None = None) -> str:
    """drawings 테이블에 vlm_result_jsonb를 기록하고 drawing_id를 반환한다.

    drawing_id가 주어지면(API 흐름): 기존 row를 재사용. vlm_result_jsonb는 vlm.py가
    이미 순수 VLM output을 저장해둔 상태이므로 갱신 없이 존재 검증만 수행.
    pipeline 입력 dict는 _buyer_id 등 주입 키를 포함하므로 그대로 덮어쓰면 오염됨.

    drawing_id가 없으면(CLI/개발 흐름): JSON sha256으로 식별하여 재사용 또는 새 INSERT.
    """
    if drawing_id:
        rows = db.execute_query_with_conn(
            conn,
            "SELECT 1 FROM imma.drawings WHERE drawing_id = %s::uuid",
            (drawing_id,),
        )
        if not rows:
            raise ValueError(f"drawing_id not found: {drawing_id}")
        return drawing_id

    vlm_json_str = json.dumps(vlm_json, ensure_ascii=False)
    drawing_no = vlm_json.get("drawing_no", "")
    # CLI/개발: VLM JSON 기반 해시로 재사용 또는 새로 만든다
    json_bytes = vlm_json_str.encode("utf-8")
    file_sha256 = hashlib.sha256(json_bytes).hexdigest()

    rows = db.execute_query_with_conn(
        conn,
        "SELECT drawing_id FROM imma.drawings WHERE file_sha256 = %s",
        (file_sha256,),
    )
    if rows:
        db.execute_insert_with_conn(
            conn,
            "UPDATE imma.drawings SET vlm_result_jsonb = %s::jsonb WHERE drawing_id = %s",
            (vlm_json_str, str(rows[0]["drawing_id"])),
        )
        return str(rows[0]["drawing_id"])

    row = db.execute_returning_with_conn(
        conn,
        """INSERT INTO imma.drawings
           (buyer_id, drawing_no, file_uri, file_sha256, vlm_result_jsonb)
           VALUES (%s, %s, %s, %s, %s::jsonb)
           RETURNING drawing_id""",
        (buyer_id, drawing_no, file_uri, file_sha256, vlm_json_str),
    )
    return str(row[0])


def _extract_delivery_date(vlm_json: dict) -> str | None:
    """VLM JSON에서 요청 납기일을 추출한다.

    general_notes_jsonb.delivery_date → 최상위 delivery_date 순으로 탐색.
    """
    gn = vlm_json.get("general_notes_jsonb")
    if isinstance(gn, dict):
        dd = gn.get("delivery_date") or gn.get("requested_delivery_date")
        if dd:
            return str(dd)
    cn = vlm_json.get("client_notes")
    if isinstance(cn, dict):
        dd = cn.get("delivery_date") or cn.get("requested_delivery_date")
        if dd:
            return str(dd)
    dd = vlm_json.get("delivery_date") or vlm_json.get("requested_delivery_date")
    return str(dd) if dd else None


def _insert_rfq(conn, buyer_id: str, drawing_id: str, vlm_json: dict) -> str:
    """rfqs 테이블에 INSERT하고 rfq_id를 반환한다."""
    standards = vlm_json.get("referenced_standards", [])
    general_notes = vlm_json.get("client_notes") or vlm_json.get("general_notes_jsonb") or {}
    delivery_date = _extract_delivery_date(vlm_json)
    order_quantity = vlm_json.get("order_quantity")
    budget_amount = vlm_json.get("budget_amount")
    budget_currency = vlm_json.get("budget_currency") or "KRW"
    row = db.execute_returning_with_conn(
        conn,
        """INSERT INTO imma.rfqs
           (buyer_id, drawing_id, referenced_standards_jsonb, general_notes_jsonb,
            requested_delivery_date, order_quantity, budget_amount, budget_currency)
           VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s)
           RETURNING rfq_id""",
        (buyer_id, drawing_id,
         json.dumps(standards, ensure_ascii=False),
         json.dumps(general_notes, ensure_ascii=False),
         delivery_date, order_quantity, budget_amount, budget_currency),
    )
    return str(row[0])


def _insert_rfq_part(conn, rfq_id: str, vlm_part, resolved) -> str:
    """rfq_parts 테이블에 INSERT하고 rfq_part_id를 반환한다."""
    row = db.execute_returning_with_conn(
        conn,
        """INSERT INTO imma.rfq_parts
           (rfq_id, part_name, material_raw_text, quantity,
            envelope_length_mm, envelope_width_mm, envelope_height_mm,
            envelope_diameter_mm, tightest_it_grade,
            finest_ra_um, gdt_jsonb, tolerances_jsonb,
            surface_roughness_jsonb, post_treatment_raw, vlm_part_jsonb)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                   %s::jsonb, %s::jsonb, %s::jsonb, %s, %s::jsonb)
           RETURNING rfq_part_id""",
        (rfq_id, vlm_part.part_name, vlm_part.material_raw_text,
         vlm_part.quantity,
         vlm_part.envelope_length, vlm_part.envelope_width,
         vlm_part.envelope_height, vlm_part.envelope_diameter,
         resolved.tightest_it, resolved.finest_ra,
         json.dumps(vlm_part.gdt, ensure_ascii=False),
         json.dumps(vlm_part.tolerances, ensure_ascii=False),
         json.dumps(vlm_part.surface_roughness, ensure_ascii=False),
         vlm_part.post_treatment,
         json.dumps({
             "part_no": vlm_part.part_no,
             "part_name": vlm_part.part_name,
             "material_raw_text": vlm_part.material_raw_text,
             "quantity": vlm_part.quantity,
             "required_processes": vlm_part.required_processes,
             "dimensions": vlm_part.dimensions,
             "tolerances": vlm_part.tolerances,
             "gdt": vlm_part.gdt,
             "surface_roughness": vlm_part.surface_roughness,
             "post_treatment": vlm_part.post_treatment,
             "unsupported": vlm_part.unsupported,
             "unsupported_reason": vlm_part.unsupported_reason,
         }, ensure_ascii=False)),
    )
    return str(row[0])


def _insert_rfq_part_processes(conn, rfq_part_id: str, processes: list[str]) -> None:
    """rfq_part_processes 테이블에 각 공정을 INSERT한다."""
    for seq, proc in enumerate(processes):
        db.execute_insert_with_conn(
            conn,
            """INSERT INTO imma.rfq_part_processes
               (rfq_part_id, process_code, sequence_order)
               VALUES (%s, %s, %s)
               ON CONFLICT (rfq_part_id, process_code) DO NOTHING""",
            (rfq_part_id, proc, seq),
        )


def _update_rfq_part_material(conn, rfq_part_id: str, resolved) -> None:
    """resolve 후 rfq_parts의 material_id, material_category_code를 UPDATE한다."""
    db.execute_insert_with_conn(
        conn,
        """UPDATE imma.rfq_parts
           SET material_id = %s::uuid,
               material_category_code = %s
           WHERE rfq_part_id = %s::uuid""",
        (resolved.material_id, resolved.category_code, rfq_part_id),
    )


def run_pipeline_from_dict(raw: dict, file_uri: str = "api://direct") -> dict:
    """VLM 출력 JSON dict를 직접 받아 전체 파이프라인을 실행한다.

    run_pipeline과 동일한 로직이지만, 파일 경로 대신 dict를 입력으로 받는다.
    API 서버에서 호출하기 위한 진입점.
    """
    return _run_pipeline_core(raw, file_uri)


def run_pipeline(vlm_json_path: str) -> dict:
    """VLM 출력 JSON 파일 경로를 받아 전체 파이프라인을 실행한다.

    1. JSON 파일 로드
    2. buyers / drawings / rfqs INSERT (트랜잭션)
    3. parse_vlm_json -> VlmPart 리스트
    4. 각 part마다 (트랜잭션 내):
       a. rfq_parts INSERT
       b. rfq_part_processes INSERT
       c. resolve_part -> ResolvedPart
       d. rfq_parts UPDATE (material_id, material_category_code)
    5. run_hard_filter + run_equipment_verification -> 후보 업체
    6. assemble_response -> to_json -> 최종 dict 반환
    """
    path = Path(vlm_json_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    return _run_pipeline_core(raw, str(path.resolve()))


def _run_pipeline_core(raw: dict, file_uri: str) -> dict:
    """파이프라인 코어 로직. run_pipeline / run_pipeline_from_dict 공통."""

    # [3] VLM JSON 파싱
    vlm_parts = parse_vlm_json(raw)

    # 도면 수준 메타데이터
    drawing_no = raw.get("drawing_no", "")
    standards = raw.get("referenced_standards", [])
    # client_notes: general_notes_jsonb.client_notes 계층을 우선, 없으면 최상위 client_notes
    _gn = raw.get("general_notes_jsonb")
    client_notes = None
    if isinstance(_gn, dict) and _gn.get("client_notes"):
        client_notes = _gn["client_notes"]
    if client_notes is None:
        client_notes = raw.get("client_notes") or None

    # client_notes.material fallback: VLM에서 재질 미추출 시 client_notes 값으로 보충
    client_material = None
    if isinstance(client_notes, dict):
        client_material = client_notes.get("material")
    if client_material:
        for vp in vlm_parts:
            if not vp.material_raw_text:
                vp.material_raw_text = client_material
                vp.material_type = "client_input"
                vp.material_source = "client_input"

    # ── client_quantity fallback: form 입력 order_quantity 를 VLM quantity 부재 시 사용 ──
    # 단부품 RFQ 의 경우에 한해 사용자 입력 우선. 다부품은 VLM quantity 보존.
    # parse.py 의 'quantity or 1' 폴백으로 quantity == 1 인 단부품 영역만 덮어쓴다.
    client_quantity = raw.get("order_quantity")
    if client_quantity:
        try:
            client_quantity_int = int(client_quantity)
        except (TypeError, ValueError):
            client_quantity_int = None
        if client_quantity_int and client_quantity_int >= 1:
            if len(vlm_parts) == 1:
                vp = vlm_parts[0]
                if vp.quantity == 1:
                    vp.quantity = client_quantity_int

    # [1~4] DB 저장: 하나의 트랜잭션으로 묶기
    # 실패 시 자동 rollback
    resolved_list = []
    rfq_part_id_list = []
    with db.transaction() as conn:
        # API 호출 시 로그인 사용자의 buyer_id를 우선 사용, CLI 실행 시 기존 fallback
        _api_buyer_id = raw.get("_buyer_id")
        if _api_buyer_id:
            buyer_id = _api_buyer_id
        else:
            buyer_id = _ensure_buyer(conn)
        _api_drawing_id = raw.get("_drawing_id")
        drawing_id = _insert_drawing(conn, buyer_id, file_uri, raw, drawing_id=_api_drawing_id)
        rfq_id = _insert_rfq(conn, buyer_id, drawing_id, raw)

        for vp in vlm_parts:
            # [4] 재질 해소 + 필드 추출 (rfq_parts INSERT 전에 resolve해서 tightest_it 확보)
            resolved = resolve_part(vp, standards)

            # [4a] rfq_parts INSERT
            rfq_part_id = _insert_rfq_part(conn, rfq_id, vp, resolved)
            rfq_part_id_list.append(rfq_part_id)

            # [4b] rfq_part_processes INSERT
            _insert_rfq_part_processes(conn, rfq_part_id, vp.required_processes)

            # [4c] resolve 후 material_id, material_category_code UPDATE
            if resolved.material_id or resolved.category_code:
                _update_rfq_part_material(conn, rfq_part_id, resolved)

            resolved_list.append(resolved)

    # [5] 매칭 (DB 읽기 전용이므로 트랜잭션 밖에서)
    parts_with_candidates = []
    for i, (resolved, vp) in enumerate(zip(resolved_list, vlm_parts)):
        rpid = rfq_part_id_list[i]
        if vp.unsupported:
            resolved.is_valid = False
            resolved.ontology_warnings.append(
                f"[unsupported] {vp.unsupported_reason or '지원 범위 외 부품'}"
            )
            parts_with_candidates.append((resolved, [], rpid))
            continue
        if not resolved.is_valid:
            parts_with_candidates.append((resolved, [], rpid))
            continue

        # 하드필터 매칭
        candidates = run_hard_filter(resolved)

        # 장비 검증
        candidates = run_equipment_verification(candidates, resolved)

        # ★ 공정 순서 검증
        seq_warnings = check_process_sequence(resolved)
        resolved.ontology_warnings.extend(seq_warnings)

        parts_with_candidates.append((resolved, candidates, rpid))

    # delivery_date를 rfq에서 추출한 값으로
    delivery_date = _extract_delivery_date(raw)

    # [6] 응답 조립
    response = assemble_response(
        rfq_id=rfq_id,
        drawing_no=drawing_no,
        delivery_date=delivery_date,
        parts_with_candidates=[
            (resolved, cands)
            for resolved, cands, _ in parts_with_candidates
        ],
        client_notes=client_notes,
    )

    result = to_json(response)

    # rfq_part_id 반영
    for i, (_, _, rpid) in enumerate(parts_with_candidates):
        if i < len(result["parts"]):
            result["parts"][i]["rfq_part_id"] = rpid

    return result


def main() -> None:
    """CLI 진입점. python main.py <vlm_json_path> 형태로 실행한다."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="IMMA Phase 1 매칭 파이프라인")
    parser.add_argument("vlm_json_path", help="VLM 출력 JSON 파일 경로")
    args = parser.parse_args()

    logger.info("파이프라인 시작: %s", args.vlm_json_path)
    try:
        result = run_pipeline(args.vlm_json_path)
    except Exception:
        logger.error("파이프라인 실행 실패", exc_info=True)
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
