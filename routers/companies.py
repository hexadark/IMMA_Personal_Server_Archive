"""
업체 관련 엔드포인트:
- GET  /companies                       (기존)
- GET  /companies/buyers                (기존)
- GET  /companies/suppliers             (기존)
- GET  /api/company/{company_id}        (B-2: 단건 조회)
- PUT  /api/company/profile             (기존)
- PUT  /api/company/availability        (기존)
- POST /api/equipment                   (기존)
- POST /api/material-capability         (기존)
- POST /api/process-capability          (기존)
- POST /api/company/capacity            (D-2: 주간 용량 등록)
- GET  /api/company/{id}/capacity       (D-2: 주간 용량 조회)
- PUT  /api/company/capacity/{id}       (D-2: 주간 용량 수정)
- POST /api/equipment/{equipment_id}/schedule   (일단위 스케줄 등록/수정)
- GET  /api/equipment/{equipment_id}/schedule   (장비별 스케줄 조회)
- GET  /api/company/{company_id}/daily-schedule (업체 전체 장비 스케줄 조회)
"""

from datetime import datetime, date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from routers.deps import engine, SCHEMA, get_current_user, _check_onboarding, _refresh_mv

router = APIRouter()


# ---------------------------------------------------------------------------
# 조회
# ---------------------------------------------------------------------------


@router.get("/companies")
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


@router.get("/companies/buyers")
def get_buyers(current_user: dict = Depends(get_current_user)):
    """buyer 목록 — admin 전용. 실명 데이터라 익명/일반 사용자 노출 금지."""
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="admin만 buyer 목록을 조회할 수 있습니다")

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


@router.get("/companies/suppliers")
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
# B-2: GET /api/company/{company_id} — 업체 단건 조회
# ---------------------------------------------------------------------------


def _bracket_employee_count(n: int | None) -> str | None:
    """임직원수를 한국 통계청 구간으로 마스킹: 1-9 / 10-49 / 50-99 / 100-299 / 300+."""
    if n is None:
        return None
    if n < 10:
        return "1-9"
    if n < 50:
        return "10-49"
    if n < 100:
        return "50-99"
    if n < 300:
        return "100-299"
    return "300+"


@router.get("/api/company/{company_id}")
def get_company_detail(company_id: str, current_user: dict = Depends(get_current_user)):
    """companies + sites + contacts + capabilities + equipment + reviews 종합 반환

    3-tier 정보 게이팅:
      - 기본 (모든 인증 사용자): 회사명/규모/주소/공정/재질/장비 카테고리/평점
      - 민감 (BRN/연락처/대표자명): admin / 본인 supplier / 관계 형성된 buyer
        관계 형성 = 매칭 supplier_response='accepted' 또는 orders 존재
      - 임직원수는 한국 통계청 구간으로 마스킹 표시
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    with engine.connect() as conn:
        # 회사 본체
        comp_row = conn.execute(
            text(f"""
                SELECT company_id, company_name, status, onboarding_status,
                       business_registration_no, representative_name,
                       company_size, employee_count, established_year,
                       main_phone, main_email, website_url, created_at
                FROM {SCHEMA}.companies
                WHERE company_id = :cid
            """),
            {"cid": company_id},
        ).fetchone()

        if comp_row is None:
            raise HTTPException(status_code=404, detail="업체를 찾을 수 없습니다")

        # 사이트 (본사/공장)
        site_row = conn.execute(
            text(f"""
                SELECT site_id, site_name, is_primary, region, city, district,
                       postal_code, address_line1, address_line2,
                       latitude, longitude
                FROM {SCHEMA}.company_sites
                WHERE company_id = :cid AND is_primary = true
                LIMIT 1
            """),
            {"cid": company_id},
        ).fetchone()

        site = None
        if site_row:
            site = {
                "site_id": str(site_row[0]),
                "site_name": site_row[1],
                "is_primary": site_row[2],
                "region": site_row[3],
                "city": site_row[4],
                "district": site_row[5],
                "postal_code": site_row[6],
                "address_line1": site_row[7],
                "address_line2": site_row[8],
                "latitude": float(site_row[9]) if site_row[9] is not None else None,
                "longitude": float(site_row[10]) if site_row[10] is not None else None,
            }

        # 담당자 목록
        contact_rows = conn.execute(
            text(f"""
                SELECT contact_id, contact_name, role_title, phone, email,
                       is_primary, receives_rfq
                FROM {SCHEMA}.company_contacts
                WHERE company_id = :cid
                ORDER BY is_primary DESC, contact_name
            """),
            {"cid": company_id},
        ).fetchall()

        contacts = []
        for cr in contact_rows:
            contacts.append({
                "contact_id": str(cr[0]),
                "contact_name": cr[1],
                "role_title": cr[2],
                "phone": cr[3],
                "email": cr[4],
                "is_primary": cr[5],
                "receives_rfq": cr[6],
            })

        # 소재 역량
        mat_rows = conn.execute(
            text(f"""
                SELECT cmc.company_material_capability_id, cmc.scope_type,
                       m.material_code, m.material_name_ko,
                       cmc.material_category_code, cmc.capability_level,
                       cmc.raw_input_text
                FROM {SCHEMA}.company_material_capabilities cmc
                LEFT JOIN {SCHEMA}.materials m ON cmc.material_id = m.material_id
                WHERE cmc.company_id = :cid AND cmc.is_active = true
                ORDER BY cmc.scope_type, m.material_code
            """),
            {"cid": company_id},
        ).fetchall()

        materials = []
        for mr in mat_rows:
            materials.append({
                "capability_id": str(mr[0]),
                "scope_type": mr[1],
                "material_code": mr[2],
                "material_name_ko": mr[3],
                "material_category_code": mr[4],
                "capability_level": mr[5],
                "raw_input_text": mr[6],
            })

        # 공정 역량
        proc_rows = conn.execute(
            text(f"""
                SELECT cpc.company_process_capability_id, cpc.process_code,
                       pc.process_name_ko,
                       cpc.service_mode, cpc.capability_level,
                       cpc.best_achievable_it_grade, cpc.best_tolerance_mm,
                       cpc.best_ra_um, cpc.typical_lead_days
                FROM {SCHEMA}.company_process_capabilities cpc
                LEFT JOIN {SCHEMA}.process_catalog pc
                    ON cpc.process_code = pc.process_code
                WHERE cpc.company_id = :cid AND cpc.is_active = true
                ORDER BY cpc.process_code
            """),
            {"cid": company_id},
        ).fetchall()

        processes = []
        for pr in proc_rows:
            processes.append({
                "capability_id": str(pr[0]),
                "process_code": pr[1],
                "process_name_ko": pr[2],
                "service_mode": pr[3],
                "capability_level": pr[4],
                "best_achievable_it_grade": pr[5],
                "best_tolerance_mm": float(pr[6]) if pr[6] is not None else None,
                "best_ra_um": float(pr[7]) if pr[7] is not None else None,
                "typical_lead_days": pr[8],
            })

        # 장비 목록
        equip_rows = conn.execute(
            text(f"""
                SELECT equipment_id, equipment_category_code, model_id,
                       display_name, manufacturer, model_name,
                       year_made, quantity, status,
                       max_turning_diameter_mm, max_turning_length_mm,
                       max_x_travel_mm, max_y_travel_mm, max_z_travel_mm,
                       best_achievable_it_grade, best_ra_um
                FROM {SCHEMA}.equipment
                WHERE company_id = :cid
                ORDER BY status, display_name
            """),
            {"cid": company_id},
        ).fetchall()

        equipment_list = []
        for er in equip_rows:
            equipment_list.append({
                "equipment_id": str(er[0]),
                "equipment_category_code": er[1],
                "model_id": er[2],
                "display_name": er[3],
                "manufacturer": er[4],
                "model_name": er[5],
                "year_made": er[6],
                "quantity": er[7],
                "status": er[8],
                "max_turning_diameter_mm": float(er[9]) if er[9] is not None else None,
                "max_turning_length_mm": float(er[10]) if er[10] is not None else None,
                "max_x_travel_mm": float(er[11]) if er[11] is not None else None,
                "max_y_travel_mm": float(er[12]) if er[12] is not None else None,
                "max_z_travel_mm": float(er[13]) if er[13] is not None else None,
                "best_achievable_it_grade": er[14],
                "best_ra_um": float(er[15]) if er[15] is not None else None,
            })

        # 리뷰 통계
        review_row = conn.execute(
            text(f"""
                SELECT
                    COALESCE(avg(rating_overall), 0) AS avg_rating,
                    count(*) AS review_count
                FROM {SCHEMA}.reviews
                WHERE company_id = :cid
            """),
            {"cid": company_id},
        ).fetchone()

        # 민감 정보 게이팅 판정
        user_role = current_user["role"]
        user_id = current_user["id"]
        is_full_access = False
        if user_role == "admin":
            is_full_access = True
        elif user_role == "supplier" and user_id == company_id:
            is_full_access = True
        elif user_role == "buyer":
            rel_row = conn.execute(
                text(f"""
                    SELECT
                      EXISTS (
                        SELECT 1 FROM {SCHEMA}.match_candidates mc
                        JOIN {SCHEMA}.match_runs mr ON mr.match_run_id = mc.match_run_id
                        JOIN {SCHEMA}.rfqs r ON r.rfq_id = mr.rfq_id
                        WHERE mc.company_id = CAST(:cid AS uuid)
                          AND r.buyer_id = CAST(:bid AS uuid)
                          AND mc.supplier_response = 'accepted'
                      )
                      OR EXISTS (
                        SELECT 1 FROM {SCHEMA}.orders
                        WHERE buyer_id = CAST(:bid AS uuid)
                          AND company_id = CAST(:cid AS uuid)
                      ) AS has_rel
                """),
                {"cid": company_id, "bid": user_id},
            ).fetchone()
            is_full_access = bool(rel_row[0]) if rel_row else False

    response = {
        "company_id": str(comp_row[0]),
        "company_name": comp_row[1],
        "status": comp_row[2],
        "onboarding_status": comp_row[3],
        "company_size": comp_row[6],
        "employee_count_bracket": _bracket_employee_count(comp_row[7]),
        "established_year": comp_row[8],
        "website_url": comp_row[11],
        "created_at": str(comp_row[12]),
        "site": site,
        "materials": materials,
        "processes": processes,
        "equipment": equipment_list,
        "avg_rating": float(review_row[0]) if review_row[0] is not None else 0,
        "review_count": review_row[1] if review_row else 0,
        "access_level": "full" if is_full_access else "basic",
    }
    if is_full_access:
        response["business_registration_no"] = comp_row[4]
        response["representative_name"] = comp_row[5]
        response["main_phone"] = comp_row[9]
        response["main_email"] = comp_row[10]
        response["contacts"] = contacts
    else:
        response["business_registration_no"] = None
        response["representative_name"] = None
        response["main_phone"] = None
        response["main_email"] = None
        response["contacts"] = []
    return response


# ---------------------------------------------------------------------------
# 프로필 수정
# ---------------------------------------------------------------------------


@router.put("/api/company/profile")
def update_company_profile(data: dict, current_user: dict = Depends(get_current_user)):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
    if current_user["role"] != "supplier":
        raise HTTPException(status_code=403, detail="supplier만 업체 정보를 수정할 수 있습니다")

    company_id = data.get("company_id")
    if not company_id:
        raise HTTPException(status_code=400, detail="company_id is required")

    if current_user["id"] != str(company_id):
        raise HTTPException(status_code=403, detail="본인 업체 정보만 수정할 수 있습니다")

    with engine.begin() as conn:
        fields = {}
        set_clauses = []
        for col in [
            "business_registration_no", "representative_name", "main_phone",
            "company_size", "employee_count", "established_year", "website_url",
        ]:
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


# ---------------------------------------------------------------------------
# 가용성 수정
# ---------------------------------------------------------------------------


@router.put("/api/company/availability")
def update_availability(data: dict, current_user: dict = Depends(get_current_user)):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
    if current_user["role"] != "supplier":
        raise HTTPException(status_code=403, detail="supplier만 가용성을 수정할 수 있습니다")

    company_id = data.get("company_id")
    if not company_id:
        raise HTTPException(status_code=400, detail="company_id is required")

    if current_user["id"] != str(company_id):
        raise HTTPException(status_code=403, detail="본인 업체 정보만 수정할 수 있습니다")

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


# ---------------------------------------------------------------------------
# 온보딩: 장비 등록
# ---------------------------------------------------------------------------


@router.post("/api/equipment")
def register_equipment(data: dict, current_user: dict = Depends(get_current_user)):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
    if current_user["role"] != "supplier":
        raise HTTPException(status_code=403, detail="supplier만 장비를 등록할 수 있습니다")

    company_id = data.get("company_id")
    model_id = data.get("model_id")
    display_name = data.get("display_name")
    category_code = data.get("equipment_category_code")

    if not company_id or not display_name or not category_code:
        raise HTTPException(
            status_code=400,
            detail="company_id, display_name, equipment_category_code are required",
        )

    if current_user["id"] != str(company_id):
        raise HTTPException(status_code=403, detail="본인 업체에만 장비를 등록할 수 있습니다")

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
                "td": cs.get("max_turning_diameter_mm"),
                "tl": cs.get("max_turning_length_mm") or cs.get("center_distance_mm"),
                "mx": cs.get("max_x_travel_mm"), "my": cs.get("max_y_travel_mm"),
                "mz": cs.get("max_z_travel_mm"),
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
                    {
                        "eid": equipment_id, "pc": pcode,
                        "it": pc.get("typical_it_grade"), "ra": pc.get("typical_ra_um"),
                    },
                )

                conn.execute(
                    text(f"""
                        INSERT INTO {SCHEMA}.company_process_capabilities
                            (company_id, process_code, best_achievable_it_grade, best_ra_um)
                        VALUES (:cid, :pc, :it, :ra)
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "cid": company_id, "pc": pcode,
                        "it": pc.get("typical_it_grade"), "ra": pc.get("typical_ra_um"),
                    },
                )

        ob_status = _check_onboarding(conn, company_id)

    return {
        "success": True,
        "equipment_id": str(equipment_id),
        "auto_generated_processes": [
            pc.get("process_code") for pc in catalog_procs if pc.get("process_code")
        ],
        "onboarding_status": ob_status,
    }


# ---------------------------------------------------------------------------
# 온보딩: 소재 역량 등록
# ---------------------------------------------------------------------------


@router.post("/api/material-capability")
def register_material_capability(data: dict, current_user: dict = Depends(get_current_user)):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
    if current_user["role"] != "supplier":
        raise HTTPException(status_code=403, detail="supplier만 재질 역량을 등록할 수 있습니다")

    company_id = data.get("company_id")
    materials = data.get("materials", [])
    categories = data.get("categories", [])

    if not company_id or (not materials and not categories):
        raise HTTPException(
            status_code=400,
            detail="company_id and at least one material or category required",
        )

    if current_user["id"] != str(company_id):
        raise HTTPException(status_code=403, detail="본인 업체에만 소재 역량을 등록할 수 있습니다")

    with engine.begin() as conn:
        # specific_material — 기존 비활성 row 재활성화 우선, 없으면 INSERT
        for mat_code in materials:
            mat_row = conn.execute(
                text(f"SELECT material_id FROM {SCHEMA}.materials WHERE material_code = :mc"),
                {"mc": mat_code},
            ).fetchone()
            if not mat_row:
                continue
            updated = conn.execute(
                text(f"""
                    UPDATE {SCHEMA}.company_material_capabilities
                    SET is_active = true, capability_level = 'regular'
                    WHERE company_id = :cid
                      AND scope_type = 'specific_material'
                      AND material_id = :mid
                """),
                {"cid": company_id, "mid": mat_row[0]},
            )
            if updated.rowcount == 0:
                conn.execute(
                    text(f"""
                        INSERT INTO {SCHEMA}.company_material_capabilities
                            (company_id, scope_type, material_id, capability_level, is_active)
                        VALUES (:cid, 'specific_material', :mid, 'regular', true)
                        ON CONFLICT DO NOTHING
                    """),
                    {"cid": company_id, "mid": mat_row[0]},
                )

        # material_category — 동일 패턴 (재활성화 우선)
        # + 카테고리 선택 시 그 카테고리의 모든 specific 강종 자동 INSERT (시연 정책)
        # — 카탈로그에 장비별 specific 매핑 부재 + supplier 가 카테고리 단위로 가공 가능 선언했으므로
        # 자식 specific 전체 등록하여 매칭 hard filter 의 specific 우선 정책에 정합 보장.
        for cat_code in categories:
            updated = conn.execute(
                text(f"""
                    UPDATE {SCHEMA}.company_material_capabilities
                    SET is_active = true, capability_level = 'regular'
                    WHERE company_id = :cid
                      AND scope_type = 'material_category'
                      AND material_category_code = :cc
                """),
                {"cid": company_id, "cc": cat_code},
            )
            if updated.rowcount == 0:
                conn.execute(
                    text(f"""
                        INSERT INTO {SCHEMA}.company_material_capabilities
                            (company_id, scope_type, material_category_code, capability_level, is_active)
                        VALUES (:cid, 'material_category', :cc, 'regular', true)
                        ON CONFLICT DO NOTHING
                    """),
                    {"cid": company_id, "cc": cat_code},
                )

            # 그 카테고리의 모든 specific 강종 자동 등록
            spec_rows = conn.execute(
                text(f"""
                    SELECT material_id FROM {SCHEMA}.materials
                    WHERE category_code = :cc AND is_active = true
                """),
                {"cc": cat_code},
            ).fetchall()
            for (mid,) in spec_rows:
                spec_updated = conn.execute(
                    text(f"""
                        UPDATE {SCHEMA}.company_material_capabilities
                        SET is_active = true, capability_level = 'regular'
                        WHERE company_id = :cid
                          AND scope_type = 'specific_material'
                          AND material_id = :mid
                    """),
                    {"cid": company_id, "mid": mid},
                )
                if spec_updated.rowcount == 0:
                    conn.execute(
                        text(f"""
                            INSERT INTO {SCHEMA}.company_material_capabilities
                                (company_id, scope_type, material_id, capability_level, is_active)
                            VALUES (:cid, 'specific_material', :mid, 'regular', true)
                            ON CONFLICT DO NOTHING
                        """),
                        {"cid": company_id, "mid": mid},
                    )

        ob_status = _check_onboarding(conn, company_id)

    return {"success": True, "onboarding_status": ob_status}


# ---------------------------------------------------------------------------
# 온보딩: 공정 역량 등록
# ---------------------------------------------------------------------------


@router.post("/api/process-capability")
def register_process_capability(data: dict, current_user: dict = Depends(get_current_user)):
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
    if current_user["role"] != "supplier":
        raise HTTPException(status_code=403, detail="supplier만 공정 역량을 등록할 수 있습니다")

    company_id = data.get("company_id")
    processes = data.get("processes", [])

    if not company_id or not processes:
        raise HTTPException(status_code=400, detail="company_id and processes required")

    if current_user["id"] != str(company_id):
        raise HTTPException(status_code=403, detail="본인 업체에만 공정 역량을 등록할 수 있습니다")

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

        ob_status = _check_onboarding(conn, company_id)

    return {"success": True, "onboarding_status": ob_status}


# ---------------------------------------------------------------------------
# D-2: 주간 용량 캘린더
# ---------------------------------------------------------------------------


def _parse_week_param(week: str):
    """
    week 파라미터 파싱.
    - "2026-W20" → 해당 주의 월요일 날짜
    - "2026-05-11" → 그대로 date 객체
    반환: datetime.date 또는 None (파싱 실패 시 HTTPException)
    """
    if not week:
        return None
    week = week.strip()

    # ISO week 형식: "2026-W20"
    if "W" in week.upper():
        try:
            # %G-W%V → 해당 ISO 주의 월요일 (%u=1)
            dt = datetime.strptime(week.upper() + "-1", "%G-W%V-%u")
            return dt.date()
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"week 형식이 올바르지 않습니다: '{week}'. "
                       f"'2026-W20' 또는 '2026-05-11' 형식을 사용하세요.",
            )

    # 날짜 형식: "2026-05-11"
    try:
        return datetime.strptime(week, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"week 형식이 올바르지 않습니다: '{week}'. "
                   f"'2026-W20' 또는 '2026-05-11' 형식을 사용하세요.",
        )


@router.post("/api/company/capacity")
def register_capacity(data: dict,
                      user: dict = Depends(get_current_user)):
    """
    주간 용량 등록 (UPSERT).
    available_hours는 GENERATED 컬럼이므로 INSERT 대상에서 제외.
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    # 소유권: JWT의 company_id 사용
    if user["role"] != "supplier":
        raise HTTPException(status_code=403, detail="업체(supplier)만 용량을 등록할 수 있습니다")
    company_id = user["id"]

    week_start_date = data.get("week_start_date")
    planned_capacity_hours = data.get("planned_capacity_hours")
    booked_hours = data.get("booked_hours")

    if week_start_date is None or planned_capacity_hours is None or booked_hours is None:
        raise HTTPException(
            status_code=400,
            detail="week_start_date, planned_capacity_hours, booked_hours는 필수입니다",
        )

    equipment_id = data.get("equipment_id")
    process_code = data.get("process_code")

    with engine.begin() as conn:
        # UPSERT: (company_id, equipment_id, process_code, week_start_date) UNIQUE 제약 활용
        conn.execute(
            text(f"""
                INSERT INTO {SCHEMA}.company_capacity_calendar
                    (company_id, equipment_id, process_code, week_start_date,
                     planned_capacity_hours, booked_hours)
                VALUES (:cid, :eid, :pc, :wsd, :pch, :bh)
                ON CONFLICT (company_id, equipment_id, process_code, week_start_date)
                DO UPDATE SET
                    planned_capacity_hours = EXCLUDED.planned_capacity_hours,
                    booked_hours = EXCLUDED.booked_hours,
                    updated_at = now()
            """),
            {
                "cid": company_id,
                "eid": equipment_id,
                "pc": process_code,
                "wsd": week_start_date,
                "pch": planned_capacity_hours,
                "bh": booked_hours,
            },
        )

        # INSERT 후 별도 SELECT로 available_hours 읽어옴 (GENERATED 컬럼)
        row = conn.execute(
            text(f"""
                SELECT capacity_id, week_start_date,
                       planned_capacity_hours, booked_hours, available_hours
                FROM {SCHEMA}.company_capacity_calendar
                WHERE company_id = :cid
                  AND equipment_id IS NOT DISTINCT FROM :eid
                  AND process_code IS NOT DISTINCT FROM :pc
                  AND week_start_date = :wsd
            """),
            {
                "cid": company_id,
                "eid": equipment_id,
                "pc": process_code,
                "wsd": week_start_date,
            },
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=500, detail="용량 등록 후 조회에 실패했습니다")

    return {
        "capacity_id": str(row[0]),
        "week_start_date": str(row[1]),
        "planned_capacity_hours": float(row[2]),
        "booked_hours": float(row[3]),
        "available_hours": float(row[4]),
    }


@router.get("/api/company/{company_id}/capacity")
def get_company_capacity(company_id: str,
                         week: Optional[str] = Query(None),
                         user: dict = Depends(get_current_user)):
    """주간 가용 시간 조회. 본인 supplier + admin만 접근 가능 (경쟁사 가동률 정찰 차단).

    week 파라미터: "2026-W20" 또는 "2026-05-11" 형식. 없으면 최근 12주.
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
    if user["role"] == "admin":
        pass
    elif user["role"] == "supplier" and user["id"] == company_id:
        pass
    else:
        raise HTTPException(status_code=403, detail="본인 업체 가용 시간만 조회할 수 있습니다")

    week_date = _parse_week_param(week)

    with engine.connect() as conn:
        if week_date is not None:
            # 해당 주만 조회
            rows = conn.execute(
                text(f"""
                    SELECT capacity_id, equipment_id, process_code,
                           week_start_date, planned_capacity_hours,
                           booked_hours, available_hours, status
                    FROM {SCHEMA}.company_capacity_calendar
                    WHERE company_id = :cid
                      AND week_start_date = :wsd
                    ORDER BY equipment_id, process_code
                """),
                {"cid": company_id, "wsd": week_date},
            ).fetchall()
        else:
            # 최근 12주 조회
            cutoff = datetime.now().date() - timedelta(weeks=12)
            rows = conn.execute(
                text(f"""
                    SELECT capacity_id, equipment_id, process_code,
                           week_start_date, planned_capacity_hours,
                           booked_hours, available_hours, status
                    FROM {SCHEMA}.company_capacity_calendar
                    WHERE company_id = :cid
                      AND week_start_date >= :cutoff
                    ORDER BY week_start_date DESC, equipment_id, process_code
                """),
                {"cid": company_id, "cutoff": cutoff},
            ).fetchall()

    result = []
    for r in rows:
        result.append({
            "capacity_id": str(r[0]),
            "equipment_id": str(r[1]) if r[1] else None,
            "process_code": r[2],
            "week_start_date": str(r[3]),
            "planned_capacity_hours": float(r[4]),
            "booked_hours": float(r[5]),
            "available_hours": float(r[6]),
            "status": r[7],
        })

    return result


@router.put("/api/company/capacity/{capacity_id}")
def update_capacity(capacity_id: str, data: dict,
                    user: dict = Depends(get_current_user)):
    """
    용량 수정. available_hours는 GENERATED 컬럼이므로 UPDATE 대상에서 제외.
    JWT의 company_id와 해당 capacity의 company_id 일치 확인.
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    if user["role"] != "supplier":
        raise HTTPException(status_code=403, detail="업체(supplier)만 용량을 수정할 수 있습니다")

    with engine.begin() as conn:
        # 소유권 확인
        cap_row = conn.execute(
            text(f"""
                SELECT company_id FROM {SCHEMA}.company_capacity_calendar
                WHERE capacity_id = :cid
            """),
            {"cid": capacity_id},
        ).fetchone()

        if cap_row is None:
            raise HTTPException(status_code=404, detail="용량 레코드를 찾을 수 없습니다")

        if str(cap_row[0]) != user["id"]:
            raise HTTPException(status_code=403, detail="본인 업체의 용량만 수정할 수 있습니다")

        # 업데이트할 필드 동적 구성 (available_hours 제외)
        updatable = ["planned_capacity_hours", "booked_hours", "status", "notes"]
        set_clauses = []
        params = {"cid": capacity_id}

        for col in updatable:
            if col in data:
                set_clauses.append(f"{col} = :{col}")
                params[col] = data[col]

        if not set_clauses:
            raise HTTPException(status_code=400, detail="수정할 필드가 하나 이상 필요합니다")

        set_clauses.append("updated_at = now()")

        conn.execute(
            text(f"""
                UPDATE {SCHEMA}.company_capacity_calendar
                SET {', '.join(set_clauses)}
                WHERE capacity_id = :cid
            """),
            params,
        )

        # 수정 후 SELECT로 available_hours 포함 조회
        row = conn.execute(
            text(f"""
                SELECT capacity_id, planned_capacity_hours,
                       booked_hours, available_hours, status
                FROM {SCHEMA}.company_capacity_calendar
                WHERE capacity_id = :cid
            """),
            {"cid": capacity_id},
        ).fetchone()

    return {
        "capacity_id": str(row[0]),
        "planned_capacity_hours": float(row[1]),
        "booked_hours": float(row[2]),
        "available_hours": float(row[3]),
        "status": row[4],
    }


# ---------------------------------------------------------------------------
# 일단위 장비 스케줄
# ---------------------------------------------------------------------------


@router.post("/api/equipment/{equipment_id}/schedule")
def upsert_equipment_schedule(equipment_id: str, data: dict,
                              user: dict = Depends(get_current_user)):
    """
    장비 일단위 스케줄 등록/수정 (UPSERT).
    단일 날짜: {"schedule_date": "2026-05-20", "planned_hours": 8, "status": "available", "memo": ""}
    범위 입력: {"from_date": "2026-05-20", "to_date": "2026-05-25", "planned_hours": 8, "status": "available"}
    available_hours는 GENERATED 컬럼이므로 INSERT/UPDATE에서 제외.
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    if user["role"] != "supplier":
        raise HTTPException(status_code=403, detail="업체(supplier)만 스케줄을 등록할 수 있습니다")

    with engine.begin() as conn:
        # 장비 소유권 확인
        eq_row = conn.execute(
            text(f"""
                SELECT company_id FROM {SCHEMA}.equipment
                WHERE equipment_id = :eid
            """),
            {"eid": equipment_id},
        ).fetchone()

        if eq_row is None:
            raise HTTPException(status_code=404, detail="장비를 찾을 수 없습니다")

        company_id = str(eq_row[0])
        if user["id"] != company_id:
            raise HTTPException(status_code=403, detail="본인 업체의 장비만 스케줄을 등록할 수 있습니다")

        # 날짜 목록 생성
        schedule_date_str = data.get("schedule_date")
        from_date_str = data.get("from_date")
        to_date_str = data.get("to_date")

        dates = []
        if schedule_date_str:
            dates.append(schedule_date_str)
        elif from_date_str and to_date_str:
            try:
                d_from = date.fromisoformat(from_date_str)
                d_to = date.fromisoformat(to_date_str)
            except ValueError:
                raise HTTPException(status_code=400, detail="날짜 형식이 올바르지 않습니다 (YYYY-MM-DD)")
            if d_to < d_from:
                raise HTTPException(status_code=400, detail="to_date는 from_date 이후여야 합니다")
            current = d_from
            while current <= d_to:
                dates.append(current.isoformat())
                current += timedelta(days=1)
        else:
            raise HTTPException(
                status_code=400,
                detail="schedule_date 또는 from_date + to_date가 필요합니다",
            )

        planned_hours = data.get("planned_hours", 8.0)
        sched_status = data.get("status", "available")
        memo = data.get("memo")

        for d in dates:
            conn.execute(
                text(f"""
                    INSERT INTO {SCHEMA}.equipment_daily_schedule
                        (equipment_id, company_id, schedule_date,
                         status, planned_hours, memo)
                    VALUES (:eid, :cid, :sd, :st, :ph, :memo)
                    ON CONFLICT (equipment_id, schedule_date) DO UPDATE SET
                        status = EXCLUDED.status,
                        planned_hours = EXCLUDED.planned_hours,
                        memo = EXCLUDED.memo,
                        updated_at = now()
                """),
                {
                    "eid": equipment_id,
                    "cid": company_id,
                    "sd": d,
                    "st": sched_status,
                    "ph": planned_hours,
                    "memo": memo,
                },
            )

    return {
        "success": True,
        "count": len(dates),
        "dates": dates,
    }


@router.get("/api/equipment/{equipment_id}/schedule")
def get_equipment_schedule(
    equipment_id: str,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    user: dict = Depends(get_current_user),
):
    """장비 일단위 스케줄 조회. 본인 supplier + admin만 (경쟁사 정찰 차단).

    query params: from, to (YYYY-MM-DD). 기본: 오늘 ~ 4주 후.
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    # 소유권 검증: 장비의 company_id가 본인 supplier와 일치해야 함
    with engine.connect() as conn:
        owner_row = conn.execute(
            text(f"SELECT company_id FROM {SCHEMA}.equipment WHERE equipment_id = CAST(:eid AS uuid)"),
            {"eid": equipment_id},
        ).fetchone()
    if owner_row is None:
        raise HTTPException(status_code=404, detail="장비를 찾을 수 없습니다")
    owner_company_id = str(owner_row[0])
    if user["role"] == "admin":
        pass
    elif user["role"] == "supplier" and user["id"] == owner_company_id:
        pass
    else:
        raise HTTPException(status_code=403, detail="본인 업체 장비 스케줄만 조회할 수 있습니다")

    today = date.today()
    try:
        d_from = date.fromisoformat(from_date) if from_date else today
        d_to = date.fromisoformat(to_date) if to_date else today + timedelta(weeks=4)
    except ValueError:
        raise HTTPException(status_code=400, detail="날짜 형식이 올바르지 않습니다 (YYYY-MM-DD)")

    with engine.connect() as conn:
        try:
            rows = conn.execute(
                text(f"""
                    SELECT schedule_date, status, planned_hours,
                           booked_hours, available_hours, order_id, memo
                    FROM {SCHEMA}.equipment_daily_schedule
                    WHERE equipment_id = :eid
                      AND schedule_date BETWEEN :d_from AND :d_to
                    ORDER BY schedule_date
                """),
                {"eid": equipment_id, "d_from": d_from, "d_to": d_to},
            ).fetchall()
        except Exception:
            return []

    return [
        {
            "schedule_date": str(r[0]),
            "status": r[1],
            "planned_hours": float(r[2]),
            "booked_hours": float(r[3]),
            "available_hours": float(r[4]),
            "order_id": str(r[5]) if r[5] else None,
            "memo": r[6],
        }
        for r in rows
    ]


@router.get("/api/company/{company_id}/daily-schedule")
def get_company_daily_schedule(
    company_id: str,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    user: dict = Depends(get_current_user),
):
    """업체 전체 장비 일단위 스케줄 조회. 본인 supplier + admin만.

    query params: from, to (YYYY-MM-DD). 기본: 오늘 ~ 4주 후.
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
    if user["role"] == "admin":
        pass
    elif user["role"] == "supplier" and user["id"] == company_id:
        pass
    else:
        raise HTTPException(status_code=403, detail="본인 업체 스케줄만 조회할 수 있습니다")

    today = date.today()
    try:
        d_from = date.fromisoformat(from_date) if from_date else today
        d_to = date.fromisoformat(to_date) if to_date else today + timedelta(weeks=4)
    except ValueError:
        raise HTTPException(status_code=400, detail="날짜 형식이 올바르지 않습니다 (YYYY-MM-DD)")

    with engine.connect() as conn:
        try:
            rows = conn.execute(
                text(f"""
                    SELECT eds.equipment_id, e.display_name,
                           eds.schedule_date, eds.status,
                           eds.planned_hours, eds.booked_hours,
                           eds.available_hours
                    FROM {SCHEMA}.equipment_daily_schedule eds
                    JOIN {SCHEMA}.equipment e ON eds.equipment_id = e.equipment_id
                    WHERE eds.company_id = :cid
                      AND eds.schedule_date BETWEEN :d_from AND :d_to
                    ORDER BY e.display_name, eds.schedule_date
                """),
                {"cid": company_id, "d_from": d_from, "d_to": d_to},
            ).fetchall()
        except Exception:
            return []

    return [
        {
            "equipment_id": str(r[0]),
            "display_name": r[1],
            "schedule_date": str(r[2]),
            "status": r[3],
            "planned_hours": float(r[4]),
            "booked_hours": float(r[5]),
            "available_hours": float(r[6]),
        }
        for r in rows
    ]
