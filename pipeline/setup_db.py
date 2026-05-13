"""IMMA Phase 1 매칭 파이프라인 — DDL 실행, seed 데이터, mock 업체 데이터 로딩."""

import hashlib
import json
import logging
import os
from pathlib import Path

import config
import db
from lookup import get_table

logger = logging.getLogger(__name__)

DDL_PATH = Path(__file__).resolve().parent.parent / "lookup_tables" / "schema.sql"

# ---------------------------------------------------------------------------
# 비밀번호 해싱 (routers/deps.py의 _hash_password와 동일 로직)
# ---------------------------------------------------------------------------

def _hash_password(password: str) -> str:
    """PBKDF2-SHA256 해싱. 반환값: 'salt_hex:hash_hex'"""
    salt = os.urandom(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return salt.hex() + ":" + hashed.hex()


# ---------------------------------------------------------------------------
# mock 업체명 → login_id 영문 slug 매핑
# ---------------------------------------------------------------------------

_COMPANY_LOGIN_SLUG: dict[str, str] = {
    "A정밀": "a_precision",
    "B공업": "b_industry",
    "C테크": "c_tech",
    "D가공": "d_machining",
    "E제작": "e_fabrication",
    "F정밀": "f_precision",
    "G주철": "g_castiron",
    "H정공": "h_precision_eng",
    "I에어로": "i_aero",
    "J복합": "j_composite",
    "K방전": "k_edm",
    "L범용": "l_general",
    "M대형": "m_large",
    "N밸브": "n_valve",
    "O워터젯": "o_waterjet",
    "P프레스": "p_press",
    "Q범용선반": "q_lathe",
    "R수지가공": "r_plastic",
    "S스텐주조": "s_stainless_casting",
}

_MOCK_PASSWORD_HASH = _hash_password("test1234")

# 룩업 JSON의 category 한글명 → DB material_category_catalog.category_code 매핑
_CATEGORY_TEXT_TO_CODE: dict[str, str] = {
    "구조용 강재": "carbon_steel",
    "기계구조용 탄소강": "carbon_steel",
    "크롬몰리브덴강": "alloy_steel",
    "합금강/크롬강": "alloy_steel",
    "합금강/니켈크롬몰리브덴강": "alloy_steel",
    "스프링강": "alloy_steel",
    "스테인리스": "stainless_steel",
    "회주철": "gray_cast_iron",
    "탄소주강": "cast_steel",
    "알루미늄": "aluminum_alloy",
    "알루미늄 다이캐스팅": "aluminum_alloy",
    "구리합금/황동": "copper_alloy",
    "구리합금/베릴륨동": "copper_alloy",
    "구리합금/인청동": "copper_alloy",
    "구리/전기동 계열": "copper_alloy",
    "판금용 강판/냉간압연 강판": "sheet_steel",
    "판금용 강판/열간압연 연강판": "sheet_steel",
    "판금용 강판/냉간압연 드로잉 강판": "sheet_steel",
    "판금용 강판/용융아연도금강판": "sheet_steel",
    "판금용 강판/냉간압연 강판 legacy": "sheet_steel",
    "공구강/냉간금형용강": "tool_steel",
    "공구강/열간공구강": "tool_steel",
    "공구강/냉간공구강": "tool_steel",
    "엔지니어링 플라스틱/폴리아세탈": "engineering_plastic",
    "엔지니어링 플라스틱/주조나일론": "engineering_plastic",
    "엔지니어링 플라스틱/나일론": "engineering_plastic",
    "엔지니어링 플라스틱/불소수지": "engineering_plastic",
    "엔지니어링 플라스틱/고내열수지": "engineering_plastic",
    "엔지니어링 플라스틱/폴리카보네이트": "engineering_plastic",
    "엔지니어링 플라스틱/ABS수지": "engineering_plastic",
    "쾌삭강/납쾌삭강": "free_cutting_steel",
    "쾌삭강/황쾌삭강": "free_cutting_steel",
    "쾌삭강/중탄소쾌삭강": "free_cutting_steel",
    "구상흑연주철": "ductile_cast_iron",
    "복합재/유리섬유 에폭시": "composite_insulation",
    "복합재/유리섬유 에폭시 적층재": "composite_insulation",
    "복합재/페놀수지": "composite_insulation",
    # 스테인리스 주강
    "스테인리스 주강": "stainless_cast_steel",
    "스테인리스주강": "stainless_cast_steel",
    "SCS계 주강": "stainless_cast_steel",
}


def create_schema() -> None:
    """imma 스키마와 필요한 테이블들의 DDL을 실행한다.
    DDL 파일에 seed INSERT문(process_catalog 34종, equipment_category_catalog 22종,
    material_category_catalog 15종)이 이미 포함돼 있다.
    """
    ddl_sql = DDL_PATH.read_text(encoding="utf-8")
    db.execute_script(ddl_sql)
    logger.info("DDL + seed 실행 완료")


def seed_reference_data() -> None:
    """룩업 JSON에서 재질 마스터 데이터를 읽어 materials + material_aliases에 삽입한다."""

    # ── materials: MATERIAL_CODE_MAPPING_KS_JIS_AISI_SAE ──
    mat_rows = get_table("MATERIAL_CODE_MAPPING_KS_JIS_AISI_SAE")
    mat_count = 0
    for row in mat_rows:
        ks_code = row.get("ks_code")
        if not ks_code:
            continue
        category_text = row.get("category", "")
        category_code = _CATEGORY_TEXT_TO_CODE.get(category_text, "other")
        ks_standard = row.get("ks_standard")
        jis_code = row.get("jis_code")
        aisi_sae = row.get("aisi_sae")
        astm_or_uns = row.get("astm_or_uns")
        representative_use = row.get("representative_use")
        equivalence_confidence = row.get("equivalence_confidence")
        material_name = f"{ks_code} {category_text}" if category_text else ks_code

        ks_full = f"{ks_standard} {ks_code}" if ks_standard and "legacy" not in ks_standard.lower() else None

        # equivalence_confidence 값을 스키마 CHECK 제약에 맞게 변환
        eq_conf_map = {
            "close": "exact",
            "approximate": "approximate",
            "legacy_or_approximate": "approximate",
        }
        eq_conf_db = eq_conf_map.get(equivalence_confidence, "unknown") if equivalence_confidence else "unknown"

        db.execute_insert(
            """INSERT INTO imma.materials
               (material_code, material_name_ko, category_code, ks_code,
                jis_code, aisi_sae_code, astm_or_uns_code, typical_usage, equivalence_confidence)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (material_code) DO NOTHING""",
            (ks_code, material_name, category_code, ks_full,
             jis_code, aisi_sae, astm_or_uns, representative_use, eq_conf_db),
        )
        mat_count += 1

        # MATERIAL_CODE_MAPPING 내의 aliases도 material_aliases로 등록
        for alias_text in row.get("aliases", []):
            clean_alias = alias_text.split("(")[0].strip()
            if not clean_alias or clean_alias == ks_code:
                continue
            db.execute_insert(
                """INSERT INTO imma.material_aliases
                   (alias_text, material_id, alias_type, note)
                   VALUES (%s,
                           (SELECT material_id FROM imma.materials WHERE material_code = %s),
                           'jis_alias',
                           %s)
                   ON CONFLICT (alias_text) DO NOTHING""",
                (clean_alias, ks_code, f"from MATERIAL_CODE_MAPPING aliases: {alias_text}"),
            )

    logger.info("materials %d행 INSERT (from MATERIAL_CODE_MAPPING)", mat_count)

    # ── material_aliases: LEGACY_KS_CODE_MAPPING ──
    legacy_rows = get_table("LEGACY_KS_CODE_MAPPING")
    alias_count = 0
    for row in legacy_rows:
        legacy_code = row.get("legacy_code")
        if not legacy_code:
            continue
        current_code = row.get("current_code")
        mapping_type = row.get("mapping_type", "legacy_code")
        description = row.get("description", "")

        # alias_type 변환: mapping_type에서 유형 추출
        if "jis_alias" in mapping_type:
            alias_type = "jis_alias"
        elif "legacy" in mapping_type or "old" in mapping_type:
            alias_type = "legacy_code"
        elif "same_code" in mapping_type:
            alias_type = "legacy_code"
        else:
            alias_type = "legacy_code"

        if current_code:
            # current_code가 materials에 존재하는 경우에만 삽입 (material_id NOT NULL 제약)
            db.execute_insert(
                """INSERT INTO imma.material_aliases
                   (alias_text, material_id, alias_type, note)
                   SELECT %s, material_id, %s, %s
                   FROM imma.materials WHERE material_code = %s
                   ON CONFLICT (alias_text) DO NOTHING""",
                (legacy_code, alias_type, description, current_code),
            )
        # current_code가 없거나 materials에 없으면 건너뜀 (material_id NOT NULL 제약)
        alias_count += 1

    logger.info("material_aliases %d행 INSERT (from LEGACY_KS_CODE_MAPPING)", alias_count)

    # ── material_aliases: MATERIAL_PROPERTIES의 common_aliases에서 별칭 등록 ──
    prop_rows = get_table("MATERIAL_PROPERTIES")
    new_alias_count = 0
    for prop in prop_rows:
        mat_code = prop.get("code")
        if not mat_code:
            continue
        for alias_text in prop.get("common_aliases", []):
            db.execute_insert(
                """INSERT INTO imma.material_aliases (alias_text, material_id)
                   SELECT %s, material_id FROM imma.materials WHERE material_code = %s
                   ON CONFLICT (alias_text) DO NOTHING""",
                (alias_text, mat_code),
            )
            new_alias_count += 1
    logger.info("material_aliases %d행 INSERT (MATERIAL_PROPERTIES common_aliases)", new_alias_count)


def _extract_value(field: dict | None) -> object:
    """equipment_catalog.json 필드에서 value만 추출한다. dict가 아니면 그대로 반환."""
    if isinstance(field, dict) and "value" in field:
        return field["value"]
    return field


def _flatten_specs(specs_dict: dict) -> dict:
    """category_specs 등의 중첩 구조에서 {key: value} 평탄 dict를 만든다."""
    result = {}
    for k, v in specs_dict.items():
        result[k] = _extract_value(v)
    return result


def _flatten_source(source_dict: dict) -> dict:
    """source 필드의 중첩 구조를 평탄화한다."""
    result = {}
    for k, v in source_dict.items():
        result[k] = _extract_value(v)
    return result


def load_equipment_catalog() -> None:
    """equipment_catalog.json의 59개 모델을 equipment_model_catalog 테이블에 INSERT한다."""

    with open(config.EQUIPMENT_CATALOG_PATH, encoding="utf-8") as f:
        catalog = json.load(f)

    templates = catalog["templates"]
    count = 0

    for tpl in templates:
        common = tpl["common"]
        model_id = _extract_value(common["model_id"])
        manufacturer = _extract_value(common["manufacturer"])
        model_name = _extract_value(common["model_name"])
        ecc = _extract_value(common["equipment_category_code"])
        max_weight = _extract_value(common.get("max_workpiece_weight_kg"))

        category_specs = json.dumps(_flatten_specs(tpl.get("category_specs", {})))

        proc_caps = []
        for pc in tpl.get("process_capabilities", []):
            proc_caps.append({
                "process_code": _extract_value(pc.get("process_code")),
                "typical_it_grade": _extract_value(pc.get("typical_it_grade")),
                "typical_ra_um": _extract_value(pc.get("typical_ra_um")),
                "capability_source": _extract_value(pc.get("capability_source")),
                "uncertain": _extract_value(pc.get("uncertain")),
            })
        proc_caps_json = json.dumps(proc_caps)

        non_mach = []
        for nm in tpl.get("non_machining_capabilities", []):
            if isinstance(nm, dict):
                non_mach.append({k: _extract_value(v) for k, v in nm.items()})
        non_mach_json = json.dumps(non_mach)

        source_json = json.dumps(_flatten_source(common.get("source", {})))

        db.execute_insert(
            """INSERT INTO imma.equipment_model_catalog
               (model_id, manufacturer, model_name, equipment_category_code,
                max_workpiece_weight_kg, category_specs, process_capabilities,
                non_machining_capabilities, source)
               VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)
               ON CONFLICT (model_id) DO UPDATE SET
                   manufacturer = EXCLUDED.manufacturer,
                   model_name = EXCLUDED.model_name,
                   equipment_category_code = EXCLUDED.equipment_category_code,
                   max_workpiece_weight_kg = EXCLUDED.max_workpiece_weight_kg,
                   category_specs = EXCLUDED.category_specs,
                   process_capabilities = EXCLUDED.process_capabilities,
                   non_machining_capabilities = EXCLUDED.non_machining_capabilities,
                   source = EXCLUDED.source""",
            (model_id, manufacturer, model_name, ecc,
             max_weight, category_specs, proc_caps_json,
             non_mach_json, source_json),
        )
        count += 1

    logger.info("equipment_model_catalog %d행 INSERT", count)


# ── 카탈로그 model_id → equipment INSERT에 필요한 스펙 추출 ──

_CATALOG_CACHE: dict[str, dict] = {}


def _get_catalog_model(model_id: str) -> dict:
    """카탈로그 JSON에서 model_id에 해당하는 모델 정보를 캐시하여 반환한다."""
    if not _CATALOG_CACHE:
        with open(config.EQUIPMENT_CATALOG_PATH, encoding="utf-8") as f:
            catalog = json.load(f)
        for tpl in catalog["templates"]:
            mid = _extract_value(tpl["common"]["model_id"])
            _CATALOG_CACHE[mid] = tpl
    return _CATALOG_CACHE.get(model_id, {})


def _catalog_equipment_row(model_id: str) -> dict:
    """카탈로그에서 equipment 테이블 컬럼에 매핑할 값들을 dict로 반환한다."""
    tpl = _get_catalog_model(model_id)
    if not tpl:
        return {}

    common = tpl["common"]
    cs = _flatten_specs(tpl.get("category_specs", {}))
    ecc = _extract_value(common["equipment_category_code"])

    row = {
        "model_id": model_id,
        "equipment_category_code": ecc,
        "manufacturer": _extract_value(common["manufacturer"]),
        "model_name": _extract_value(common["model_name"]),
        "max_workpiece_weight_kg": _extract_value(common.get("max_workpiece_weight_kg")),
    }

    # 선반류: max_turning_diameter_mm, max_turning_length_mm
    row["max_turning_diameter_mm"] = cs.get("max_turning_diameter_mm")
    row["max_turning_length_mm"] = cs.get("max_turning_length_mm") or cs.get("center_distance_mm")

    # 연삭기: grinding 스펙 → turning 컬럼에 매핑 (원통연삭 직경/길이)
    if "max_grinding_diameter_mm" in cs:
        row["max_turning_diameter_mm"] = cs.get("max_grinding_diameter_mm")
    if "max_grinding_length_mm" in cs:
        row["max_turning_length_mm"] = cs.get("max_grinding_length_mm")

    # 내면연삭기: bore 스펙
    if "max_workpiece_bore_mm" in cs:
        row["max_turning_diameter_mm"] = cs.get("max_workpiece_bore_mm")
    if "max_bore_depth_mm" in cs:
        row["max_turning_length_mm"] = cs.get("max_bore_depth_mm")

    # 밀링/MC: travel
    row["max_x_travel_mm"] = cs.get("max_x_travel_mm")
    row["max_y_travel_mm"] = cs.get("max_y_travel_mm")
    row["max_z_travel_mm"] = cs.get("max_z_travel_mm")
    row["table_size_x_mm"] = cs.get("table_size_x_mm")
    row["table_size_y_mm"] = cs.get("table_size_y_mm")

    # 스핀들
    row["spindle_max_rpm"] = cs.get("spindle_max_rpm")
    row["spindle_power_kw"] = cs.get("spindle_power_kw")
    row["axis_count"] = cs.get("axis_count")

    # 레이저/플라즈마/워터젯: sheet/bed size → travel 컬럼 매핑
    for xkey in ("max_sheet_size_x_mm", "bed_size_x_mm", "max_cut_length_mm"):
        if cs.get(xkey) and not row.get("max_x_travel_mm"):
            row["max_x_travel_mm"] = cs[xkey]
    for ykey in ("max_sheet_size_y_mm", "bed_size_y_mm", "max_cut_width_mm"):
        if cs.get(ykey) and not row.get("max_y_travel_mm"):
            row["max_y_travel_mm"] = cs[ykey]

    # 절곡기: bending length → x travel
    if cs.get("bending_length_mm") and not row.get("max_x_travel_mm"):
        row["max_x_travel_mm"] = cs["bending_length_mm"]

    return row


def _catalog_process_capabilities(model_id: str) -> list[tuple]:
    """카탈로그에서 (process_code, it_grade, ra_um) 리스트를 반환한다."""
    tpl = _get_catalog_model(model_id)
    if not tpl:
        return []
    result = []
    for pc in tpl.get("process_capabilities", []):
        pcode = _extract_value(pc.get("process_code"))
        it = _extract_value(pc.get("typical_it_grade"))
        ra = _extract_value(pc.get("typical_ra_um"))
        if pcode:
            result.append((pcode, it, ra))
    return result


def _best_from_catalog_procs(procs: list[tuple]) -> tuple:
    """process_capabilities 리스트에서 best IT grade와 best Ra를 구한다."""
    best_it = None
    best_ra = None
    for _, it, ra in procs:
        if it is not None and (best_it is None or it < best_it):
            best_it = it
        if ra is not None and (best_ra is None or ra < best_ra):
            best_ra = ra
    return best_it, best_ra


def load_mock_companies() -> None:
    """테스트용 mock 업체 데이터 19개를 삽입한다."""

    # ── A정밀 (선삭+연삭+열처리 전문) ──
    _insert_company(
        name="A정밀",
        catalog_equipment=[
            ("smec_sl_2000b", "CNC선반 A-1 (SMEC SL 2000B)"),
            ("jainnher_jhu_2706cnc", "원통연삭기 A-1 (Jainnher JHU-2706CNC)"),
            ("nabertherm_n_41_h", "열처리로 A-1 (Nabertherm N 41/H)"),
        ],
        material_specifics=["SCM415", "SCM440", "STS304", "STS316"],
        material_categories=[],
        processes=[
            ("turning", 5, 0.4, None, None, None, 80, 500),
            ("cylindrical_grinding", 4, 0.2, None, None, None, 60, 400),
            ("heat_treatment", None, None, None, None, None, None, None),
        ],
        availability=("available", "2026-05-05"),
        ratings=[(4.3, 4.5, 4.2, 4.0, 4.5)] * 4,
    )

    # ── B공업 (종합 가공) ──
    _insert_company(
        name="B공업",
        catalog_equipment=[
            ("smec_mcv_6700", "3축MC B-1 (SMEC MCV 6700)"),
            ("dmg_mori_cmx_1100_v", "3축MC B-2 (DMG MORI CMX 1100V)"),
            ("dn_solutions_lynx_2100lya", "CNC선반 B-1 (DN Solutions Lynx 2100LYA)"),
            ("jainnher_jhu_2706cnc", "원통연삭기 B-1 (Jainnher JHU-2706CNC)"),
            ("nabertherm_n_41_h", "열처리로 B-1 (Nabertherm N 41/H)"),
        ],
        material_specifics=["SCM415", "SCM440", "SM45C", "S45C"],
        material_categories=[],
        processes=[
            ("turning", 4, 0.2, None, None, None, 120, 800),
            ("milling", 5, 0.4, 300, 200, 150, None, None),
            ("cylindrical_grinding", 5, 0.4, None, None, None, 100, 600),
            ("heat_treatment", None, None, None, None, None, None, None),
        ],
        availability=("available", "2026-05-10"),
        ratings=[(4.6, 4.7, 4.5, 4.6, 4.5)] * 8,
    )

    # ── C테크 (밀링 전문) ──
    _insert_company(
        name="C테크",
        catalog_equipment=[
            ("haas_vf_2", "3축MC C-1 (Haas VF-2)"),
            ("mazak_vc_ez_20", "3축MC C-2 (Mazak VC-Ez 20)"),
        ],
        material_specifics=["A6061", "A5052", "STS304"],
        material_categories=[],
        processes=[
            ("milling", 7, 0.8, 400, 300, 200, None, None),
        ],
        availability=("available", "2026-05-03"),
        ratings=[(4.0, 4.0, 4.0, 4.0, 4.0)] * 2,
    )

    # ── D가공 (소형 선삭) ──
    _insert_company(
        name="D가공",
        catalog_equipment=[
            ("smec_sl_2000b", "CNC선반 D-1 (SMEC SL 2000B)"),
        ],
        material_specifics=["SM45C", "S45C"],
        material_categories=[],
        processes=[
            ("turning", 8, 1.6, None, None, None, 60, 300),
        ],
        availability=("limited", "2026-05-20"),
        ratings=[(3.8, 3.5, 4.0, 4.0, 3.8)] * 3,
    )

    # ── E제작 (판금/용접 종합) ──
    _insert_company(
        name="E제작",
        catalog_equipment=[
            ("trumpf_trulaser_3030_fiber_trufiber_6001", "레이저절단기 E-1 (TRUMPF TruLaser 3030)"),
            ("amada_ensis_3015rie_6kw", "레이저절단기 E-2 (AMADA ENSIS-3015RIe)"),
            ("amada_hrb_1003", "절곡기 E-1 (AMADA HRB-1003)"),
            ("dener_xl_30100", "절곡기 E-2 (Dener XL 30100)"),
            ("hyundai_welding_beta500ap", "용접장비 E-1 (Hyundai BETA500AP)"),
            ("hyundai_welding_hi400i", "용접장비 E-2 (Hyundai Hi400i)"),
            ("tecna_4647n", "스폿용접기 E-1 (TECNA 4647N)"),
        ],
        material_specifics=["SPHC", "SS400"],
        material_categories=["sheet_steel"],
        processes=[
            ("laser_cutting", None, None, 3070, 1550, None, None, None),
            ("bending", None, None, 3020, None, None, None, None),
            ("welding", None, None, None, None, None, None, None),
        ],
        availability=("available", "2026-05-01"),
        ratings=[(4.1, 4.0, 4.2, 4.0, 4.2)] * 5,
    )

    # ── F정밀 (쾌삭강 전문, 소형 정밀 선삭) ──
    _insert_company(
        name="F정밀",
        catalog_equipment=[
            ("hwacheon_cutex_160a", "CNC선반 F-1 (Hwacheon CUTEX-160A)"),
            ("jainnher_jhu_2706cnc", "원통연삭기 F-1 (Jainnher JHU-2706CNC)"),
        ],
        material_specifics=["SM45C", "SUM24L", "SUM22", "SUM23", "SUM43"],
        material_categories=["carbon_steel", "free_cutting_steel"],
        processes=[
            ("turning", 4, 0.2, None, None, None, 60, 300),
            ("drilling", 7, None, None, None, None, 60, 300),
            ("cylindrical_grinding", 4, 0.1, None, None, None, 60, 300),
            ("threading", 6, None, None, None, None, 60, 300),
        ],
        availability=("available", "2026-05-08"),
        ratings=[(4.5, 4.6, 4.3, 4.4, 4.5)] * 6,
    )

    # ── G주철 (회주철 가공 전문, 주물+밀링+선삭) ──
    _insert_company(
        name="G주철",
        catalog_equipment=[
            ("hwacheon_hl_580_2000", "CNC선반 G-1 (Hwacheon HL-580/2000)"),
            ("dn_solutions_dnm_5700", "3축MC G-1 (DN Solutions DNM 5700)"),
            ("inductotherm_heavy_steel_shell_furnace_350kg_100t", "유도 용해로 G-1 (Inductotherm Heavy Steel Shell Furnace)"),
        ],
        material_specifics=["GC200", "GCD400", "GCD500", "GCD450", "GCD600", "GCD700"],
        material_categories=["gray_cast_iron", "cast_steel", "ductile_cast_iron"],
        processes=[
            ("turning", 6, 0.8, None, None, None, 200, 800),
            ("milling", 6, 0.8, 570, 400, 460, None, None),
            ("drilling", 8, None, 570, 400, 460, None, None),
            ("boring", 6, 0.3, 570, 400, 460, None, None),
            ("threading", 7, None, None, None, None, 200, 800),
            ("casting", None, None, None, None, None, None, None),
        ],
        availability=("available", "2026-05-12"),
        ratings=[(4.2, 4.3, 4.1, 4.0, 4.2)] * 5,
    )

    # ── H정공 (호닝/내면연삭 정밀 가공 전문) ──
    # 보유 장비: 내면연삭기(JHI-150CNC, OIG-200)만 보유, 원통연삭기 미보유
    # → cylindrical_grinding 제거, internal_grinding만 유지
    _insert_company(
        name="H정공",
        catalog_equipment=[
            ("jainnher_jhi_150cnc", "내면연삭기 H-1 (Jainnher JHI-150CNC)"),
            ("palmary_oig_200", "내면연삭기 H-2 (Palmary OIG-200)"),
            ("nidec_ge15hs", "기어호브 H-1 (Nidec GE15HS)"),
            ("nidec_ge25hs", "기어호브 H-2 (Nidec GE25HS)"),
        ],
        material_specifics=["SCM415", "SCM440"],
        material_categories=["alloy_steel", "carbon_steel"],
        processes=[
            ("internal_grinding", 3, 0.1, None, None, None, 150, 200),
            ("hobbing", 5, 0.8, None, None, None, 150, None),
        ],
        availability=("available", "2026-05-06"),
        ratings=[(4.7, 4.8, 4.5, 4.6, 4.4)] * 7,
    )

    # ── I에어로 (5축 가공 전문) ──
    _insert_company(
        name="I에어로",
        catalog_equipment=[
            ("haas_umc_750", "5축MC I-1 (Haas UMC-750)"),
            ("dmg_mori_dmu_50_3rd_generation", "5축MC I-2 (DMG MORI DMU 50)"),
            ("mazak_variaxis_i_600", "5축MC I-3 (Mazak VARIAXIS i-600)"),
            ("haas_umc_500", "5축MC I-4 (Haas UMC-500)"),
        ],
        material_specifics=["A6061", "A5052", "STS304", "STS316", "SCM440"],
        material_categories=["aluminum_alloy", "stainless_steel"],
        processes=[
            ("milling", 4, 0.2, 762, 508, 508, None, None),
            ("drilling", 6, None, 762, 508, 508, None, None),
        ],
        availability=("available", "2026-05-15"),
        ratings=[(4.8, 4.9, 4.7, 4.6, 4.5)] * 4,
    )

    # ── J복합 (복합가공기 mill-turn 전문) ──
    # boring: 복합가공기(INTEGREX, SMX3100ST, NTX 1000)의 밀링 스핀들로 보링 수행.
    #         장비 카탈로그에 별도 boring capability row는 없으나, 밀링 스핀들의
    #         보링바 장착으로 보링 공정 수행이 가능하므로 공정 목록에 유지.
    _insert_company(
        name="J복합",
        catalog_equipment=[
            ("mazak_integrex_i_250h_s", "복합가공기 J-1 (Mazak INTEGREX i-250H S)"),
            ("dn_solutions_smx3100st", "복합가공기 J-2 (DN Solutions SMX3100ST)"),
            ("dmg_mori_ntx_1000", "복합가공기 J-3 (DMG MORI NTX 1000)"),
        ],
        material_specifics=["SCM415", "SCM440", "STS304", "SM45C", "FR4", "G10", "Bakelite"],
        material_categories=["alloy_steel", "composite_insulation"],
        processes=[
            ("turning", 4, 0.2, None, None, None, 150, 800),
            ("milling", 5, 0.4, 400, 300, 300, None, None),
            ("drilling", 6, None, 400, 300, 300, None, None),
            ("threading", 6, None, None, None, None, 150, 800),
            ("boring", 5, 0.4, 400, 300, 300, None, None),  # 복합가공기 밀링 스핀들로 보링 수행
            ("polishing", None, None, None, None, None, None, None),
        ],
        availability=("limited", "2026-05-18"),
        ratings=[(4.6, 4.7, 4.5, 4.5, 4.3)] * 6,
    )

    # ── K방전 (EDM 전문 — 와이어+형조) ──
    _insert_company(
        name="K방전",
        catalog_equipment=[
            ("sodick_ag40l", "형조방전 K-1 (Sodick AG40L)"),
            ("sodick_ad35l", "형조방전 K-2 (Sodick AD35L)"),
            ("sodick_vl400q", "와이어방전 K-1 (Sodick VL400Q)"),
            ("mitsubishi_electric_mv1200s", "와이어방전 K-2 (Mitsubishi MV1200S)"),
        ],
        material_specifics=["SCM440", "SKD11", "SKD61"],
        material_categories=["alloy_steel", "tool_steel"],
        processes=[
            ("edm_sinker", 5, 0.2, 400, 300, 250, None, None),
            ("edm_wire", 5, 0.4, 400, 300, 250, None, None),
        ],
        availability=("available", "2026-05-07"),
        ratings=[(4.4, 4.5, 4.3, 4.2, 4.4)] * 5,
    )

    # ── L범용 (넓은 재질/공정 커버, 중간 정밀도) ──
    _insert_company(
        name="L범용",
        catalog_equipment=[
            ("smec_mcv_6700", "3축MC L-1 (SMEC MCV 6700)"),
            ("hyundai_wia_kf5600ii_15k_opt1", "3축MC L-2 (Hyundai WIA KF5600II)"),
            ("hwacheon_hl_460_1500", "CNC선반 L-1 (Hwacheon HL-460/1500)"),
            ("chevalier_fsg_618m", "평면연삭기 L-1 (Chevalier FSG-618M)"),
            ("nabertherm_n_41_h", "열처리로 L-1 (Nabertherm N 41/H)"),
            ("ipsen_titan_h2", "진공열처리로 L-1 (Ipsen TITAN H2)"),
        ],
        material_specifics=["SM45C", "S45C", "SCM415", "SCM440", "STS304", "A6061", "GC200", "C3604", "C2600"],
        material_categories=["carbon_steel", "alloy_steel", "stainless_steel", "aluminum_alloy", "gray_cast_iron", "copper_alloy"],
        processes=[
            ("turning", 6, 0.8, None, None, None, 150, 800),
            ("milling", 7, 0.8, 600, 400, 400, None, None),
            ("drilling", 8, None, 600, 400, 400, None, None),
            ("surface_grinding", 5, 0.4, 450, 150, None, None, None),
            ("heat_treatment", None, None, None, None, None, None, None),
        ],
        availability=("available", "2026-05-04"),
        ratings=[(3.9, 3.8, 4.0, 4.1, 4.2)] * 8,
    )

    # ── M대형 (대형 선삭 전문) ──
    _insert_company(
        name="M대형",
        catalog_equipment=[
            ("hwacheon_hl_720_2000", "CNC선반 M-1 (Hwacheon HL-720/2000)"),
            ("dmg_mori_nlx_4000_1500", "CNC선반 M-2 (DMG MORI NLX 4000/1500)"),
            ("palmary_gu32x60s", "원통연삭기 M-1 (Palmary GU32x60S)"),
        ],
        material_specifics=["SCM415", "SCM440", "SM45C"],
        material_categories=["alloy_steel", "carbon_steel"],
        processes=[
            ("turning", 5, 0.4, None, None, None, 400, 2000),
            ("cylindrical_grinding", 4, 0.2, None, None, None, 320, 600),
            ("threading", 6, None, None, None, None, 400, 2000),
        ],
        availability=("available", "2026-05-10"),
        ratings=[(4.3, 4.4, 4.2, 4.1, 4.0)] * 5,
    )

    # ── N밸브 (소형 밸브/슬리브 정밀 선삭 + 연삭, mill-turn) ──
    _insert_company(
        name="N밸브",
        catalog_equipment=[
            ("mazak_quick_turn_250msy", "CNC선반 N-1 (Mazak QT 250MSY)"),
            ("mazak_quick_turn_200my", "복합가공기 N-2 (Mazak QT 200MY)"),
            ("nakamura_tome_as_200l", "복합가공기 N-1 (Nakamura-Tome AS-200L)"),
            ("kent_cgs_618m", "평면연삭기 N-1 (Kent CGS-618M)"),
        ],
        material_specifics=["SCM415", "SCM440", "C3604", "C5191"],
        material_categories=["alloy_steel", "carbon_steel"],
        processes=[
            ("turning", 4, 0.2, None, None, None, 100, 500),
            ("milling", 6, 0.4, 200, 150, 150, None, None),
            ("drilling", 6, None, 200, 150, 150, None, None),
            ("surface_grinding", 5, 0.4, 450, 150, None, None, None),
            ("threading", 6, None, None, None, None, 100, 500),
        ],
        availability=("limited", "2026-05-22"),
        ratings=[(4.5, 4.6, 4.4, 4.3, 4.5)] * 9,
    )

    # ── O워터젯 (워터젯+플라즈마+용접 — 판금/특수재 절단) ──
    _insert_company(
        name="O워터젯",
        catalog_equipment=[
            ("omax_55100", "워터젯 O-1 (OMAX 55100)"),
            ("flow_mach_200_3020", "워터젯 O-2 (Flow Mach 200 3020)"),
            ("messer_metalmaster_2_0_xpr300", "플라즈마 O-1 (Messer MetalMaster 2.0)"),
            ("koike_shopproxhd_katana_xpr300", "플라즈마 O-2 (Koike ShopProXHD)"),
            ("fronius_tps_400i", "용접장비 O-1 (Fronius TPS 400i)"),
            ("fronius_iwave_230i_acdc", "TIG용접기 O-1 (Fronius iWave 230i AC/DC)"),
        ],
        material_specifics=["STS304", "STS316", "A6061", "SS400"],
        material_categories=["stainless_steel", "aluminum_alloy", "sheet_steel", "copper_alloy", "composite_insulation"],
        processes=[
            ("waterjet_cutting", None, None, 1524, 3048, None, None, None),
            ("plasma_cutting", None, None, 3000, 6000, None, None, None),
            ("welding", None, None, None, None, None, None, None),
        ],
        availability=("available", "2026-05-09"),
        ratings=[(4.0, 3.9, 4.1, 4.0, 4.2)] * 4,
    )

    # ── P프레스 (프레스 성형 + 용접) ──
    _insert_company(
        name="P프레스",
        catalog_equipment=[
            ("aida_nc1_1100_2e", "프레스 P-1 (AIDA NC1-1100)"),
            ("simpac_cx_110_ls", "프레스 P-2 (SIMPAC CX-110 LS)"),
            ("miller_millermatic_255", "용접장비 P-1 (Miller Millermatic 255)"),
        ],
        material_specifics=["SPHC", "SS400", "STS304"],
        material_categories=["sheet_steel", "carbon_steel"],
        processes=[
            ("press_forming", 13, 6.3, 1150, 700, None, None, None),
            ("welding", None, None, None, None, None, None, None),
        ],
        availability=("available", "2026-05-11"),
        ratings=[(3.9, 3.8, 4.0, 4.0, 4.1)] * 4,
    )

    # ── Q범용선반 (소규모 범용 선삭) ──
    _insert_company(
        name="Q범용선반",
        catalog_equipment=[
            ("hwacheon_hl_380_750", "범용선반 Q-1 (Hwacheon HL-380x750)"),
            ("haas_st_20y", "CNC선반 Q-1 (Haas ST-20Y)"),
        ],
        material_specifics=["SM45C", "S45C", "SS400"],
        material_categories=["carbon_steel"],
        processes=[
            ("turning", 7, 1.0, None, None, None, 298, 572),
            ("drilling", 9, 3.2, None, None, None, 298, 572),
            ("threading", 8, 1.6, None, None, None, 298, 572),
        ],
        availability=("available", "2026-05-06"),
        ratings=[(3.5, 3.4, 3.6, 3.8, 3.8)] * 3,
    )

    # ── R수지가공 (엔지니어링 플라스틱 전문 CNC) ──
    _insert_company(
        name="R수지가공",
        catalog_equipment=[
            ("hwacheon_cutex_160a", "CNC선반 R-1 (Hwacheon CUTEX-160A)"),
            ("haas_vf_2", "3축MC R-1 (Haas VF-2)"),
        ],
        material_specifics=["POM", "MC_Nylon", "PEEK", "PC", "ABS", "PA6", "PTFE"],
        material_categories=["engineering_plastic"],
        processes=[
            ("turning", 7, 0.8, None, None, None, None, None),
            ("milling", 7, 0.8, None, None, None, None, None),
            ("drilling", 8, None, None, None, None, None, None),
            ("threading", 8, None, None, None, None, None, None),
            ("polishing", None, None, None, None, None, None, None),
        ],
        availability=("available", "2026-05-15"),
        ratings=[(4.3, 4.4, 4.2, 4.3, 4.4)] * 4,
    )

    # ── S스텐주조 (스테인리스 주강 전문, 주조 + 후가공) ──
    _insert_company(
        name="S스텐주조",
        catalog_equipment=[
            ("pillar_steel_frame_furnace_45_1361kg", "스테인리스 용해로 S-1 (Pillar Steel Frame Furnace, 45–1361 kg steel capacity)"),
            ("dn_solutions_dnm_5700", "후가공 MC S-1 (DN Solutions DNM 5700)"),
            ("hwacheon_hl_580_2000", "후가공 선반 S-1 (Hwacheon HL-580 x 2000)"),
            ("ipsen_titan_h2", "고용화/열처리 협력 기준 모델 (Ipsen TITAN H2)"),
        ],
        material_specifics=["SCS13", "SCS14", "SCS16"],
        material_categories=["stainless_cast_steel"],
        processes=[
            ("casting", None, None, None, None, None, None, None),
            ("heat_treatment", None, None, None, None, None, None, None),
            ("milling", 8, 1.6, None, None, None, None, None),
            ("turning", 8, 1.6, None, None, None, None, None),
            ("boring", 8, 1.6, None, None, None, None, None),
        ],
        availability=("available", "2026-05-20"),
        ratings=[(4.2, 4.3, 4.1, 4.0, 4.2)] * 5,
    )

    # MV 갱신
    db.execute_script("REFRESH MATERIALIZED VIEW imma.company_capability_summary;")
    logger.info("mock 업체 로딩 + MV REFRESH 완료")


def _insert_company(
    name: str,
    catalog_equipment: list[tuple[str, str]],
    material_specifics: list[str],
    material_categories: list[str],
    processes: list[tuple],
    availability: tuple[str, str],
    ratings: list[tuple],
) -> None:
    """단일 mock 업체의 모든 관련 테이블을 INSERT한다.

    catalog_equipment: [(model_id, display_name), ...]
        카탈로그에서 스펙을 가져와 equipment + equipment_process_capabilities를 채운다.
    """

    # companies — 이미 존재하면 스킵
    existing = db.execute_query(
        "SELECT company_id FROM imma.companies WHERE company_name = %s",
        (name,),
    )
    if existing:
        logger.info("업체 '%s' 이미 존재 — 스킵", name)
        return

    login_slug = _COMPANY_LOGIN_SLUG.get(name, name.lower().replace(" ", "_"))
    login_email = f"{login_slug}@imma-mock.local"

    row = db.execute_returning(
        """INSERT INTO imma.companies
           (company_name, login_id, main_email, password_hash,
            status, onboarding_status)
           VALUES (%s, %s, %s, %s, 'active', 'verified')
           RETURNING company_id""",
        (name, login_slug, login_email, _MOCK_PASSWORD_HASH),
    )
    cid = str(row[0])

    # company_material_capabilities — specific materials
    for mat_code in material_specifics:
        db.execute_insert(
            """INSERT INTO imma.company_material_capabilities
               (company_id, scope_type, material_id, capability_level)
               VALUES (%s, 'specific_material',
                       COALESCE(
                           (SELECT material_id FROM imma.materials WHERE material_code = %s),
                           (SELECT material_id FROM imma.material_aliases WHERE alias_text = %s)
                       ),
                       'regular')
               ON CONFLICT DO NOTHING""",
            (cid, mat_code, mat_code),
        )

    # company_material_capabilities — material categories
    for cat_code in material_categories:
        db.execute_insert(
            """INSERT INTO imma.company_material_capabilities
               (company_id, scope_type, material_category_code, capability_level)
               VALUES (%s, 'material_category', %s, 'regular')
               ON CONFLICT DO NOTHING""",
            (cid, cat_code),
        )

    # company_process_capabilities — 수동 목록 먼저 삽입
    inserted_proc_codes: set[str] = set()
    for proc in processes:
        (pc, it, ra, mx, my, mz, td, tl) = proc
        db.execute_insert(
            """INSERT INTO imma.company_process_capabilities
               (company_id, process_code, best_achievable_it_grade, best_ra_um,
                max_x_mm, max_y_mm, max_z_mm,
                max_turning_diameter_mm, max_turning_length_mm)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT DO NOTHING""",
            (cid, pc, it, ra, mx, my, mz, td, tl),
        )
        inserted_proc_codes.add(pc)

    # 카탈로그 장비의 공정을 수집하여 company_process_capabilities에 자동 병합
    catalog_proc_codes: set[str] = set()
    for model_id, _ in catalog_equipment:
        for pcode, it_grade, ra_um in _catalog_process_capabilities(model_id):
            catalog_proc_codes.add(pcode)

    for pcode in catalog_proc_codes - inserted_proc_codes:
        db.execute_insert(
            """INSERT INTO imma.company_process_capabilities
               (company_id, process_code, best_achievable_it_grade, best_ra_um)
               VALUES (%s, %s, NULL, NULL)
               ON CONFLICT DO NOTHING""",
            (cid, pcode),
        )
        inserted_proc_codes.add(pcode)

    # equipment + equipment_process_capabilities — 카탈로그 기반
    for model_id, display_name in catalog_equipment:
        eq_info = _catalog_equipment_row(model_id)
        cat_procs = _catalog_process_capabilities(model_id)
        best_it, best_ra = _best_from_catalog_procs(cat_procs)

        eq_row = db.execute_returning(
            """INSERT INTO imma.equipment
               (company_id, equipment_category_code, model_id, display_name,
                manufacturer, model_name,
                max_turning_diameter_mm, max_turning_length_mm,
                max_x_travel_mm, max_y_travel_mm, max_z_travel_mm,
                table_size_x_mm, table_size_y_mm,
                max_workpiece_weight_kg,
                best_achievable_it_grade, best_ra_um,
                spindle_max_rpm, spindle_power_kw,
                axis_count, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'running')
               RETURNING equipment_id""",
            (cid,
             eq_info.get("equipment_category_code"),
             model_id,
             display_name,
             eq_info.get("manufacturer"),
             eq_info.get("model_name"),
             eq_info.get("max_turning_diameter_mm"),
             eq_info.get("max_turning_length_mm"),
             eq_info.get("max_x_travel_mm"),
             eq_info.get("max_y_travel_mm"),
             eq_info.get("max_z_travel_mm"),
             eq_info.get("table_size_x_mm"),
             eq_info.get("table_size_y_mm"),
             eq_info.get("max_workpiece_weight_kg"),
             best_it,
             best_ra,
             eq_info.get("spindle_max_rpm"),
             eq_info.get("spindle_power_kw"),
             eq_info.get("axis_count")),
        )
        eid = str(eq_row[0])

        # equipment_process_capabilities — 카탈로그의 process_capabilities에서 생성
        if cat_procs:
            for pcode, it_grade, ra_um in cat_procs:
                db.execute_insert(
                    """INSERT INTO imma.equipment_process_capabilities
                       (equipment_id, process_code, best_achievable_it_grade, best_ra_um)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (equipment_id, process_code) DO NOTHING""",
                    (eid, pcode, it_grade, ra_um),
                )
        else:
            # 카탈로그에 process_capabilities가 없는 장비 (열처리로, 용접장비 등)
            # equipment_category_code → default process_code 매핑
            _FALLBACK_PROC = {
                "heat_treatment_furnace": "heat_treatment",
                "welding_equipment": "welding",
                "casting_foundry": "casting",
            }
            ecc = eq_info.get("equipment_category_code", "")
            fallback_proc = _FALLBACK_PROC.get(ecc)
            if fallback_proc:
                db.execute_insert(
                    """INSERT INTO imma.equipment_process_capabilities
                       (equipment_id, process_code, best_achievable_it_grade, best_ra_um)
                       VALUES (%s, %s, NULL, NULL)
                       ON CONFLICT (equipment_id, process_code) DO NOTHING""",
                    (eid, fallback_proc),
                )

    # company_material_process_capabilities — 재질×공정 결합 테이블 seed
    # material_specifics × processes 조합에서 비가공 공정(heat_treatment, casting 등)을 제외하고 생성
    _NON_MACHINING_FOR_CMPC = {
        "heat_treatment", "welding", "surface_treatment", "casting",
        "sheet_metal", "polishing", "cleaning", "final_inspection", "deburring",
    }
    for mat_code in material_specifics:
        for proc in processes:
            (pc, it, ra, mx, my, mz, td, tl) = proc
            if pc in _NON_MACHINING_FOR_CMPC:
                continue
            db.execute_insert(
                """INSERT INTO imma.company_material_process_capabilities
                   (company_id, material_id, process_code,
                    typical_it_grade, best_achievable_it_grade,
                    typical_ra_um, best_ra_um,
                    max_x_mm, max_y_mm, max_z_mm,
                    max_turning_diameter_mm, max_turning_length_mm,
                    capability_source)
                   SELECT %s,
                          (SELECT material_id FROM imma.materials WHERE material_code = %s),
                          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                          'spec_based_estimate'
                   WHERE EXISTS (SELECT 1 FROM imma.materials WHERE material_code = %s)
                   ON CONFLICT DO NOTHING""",
                (cid, mat_code, pc, it, it, ra, ra, mx, my, mz, td, tl, mat_code),
            )
    # material_categories × processes 조합 (카테고리 수준)
    for cat_code in material_categories:
        for proc in processes:
            (pc, it, ra, mx, my, mz, td, tl) = proc
            if pc in _NON_MACHINING_FOR_CMPC:
                continue
            db.execute_insert(
                """INSERT INTO imma.company_material_process_capabilities
                   (company_id, material_category_code, process_code,
                    typical_it_grade, best_achievable_it_grade,
                    typical_ra_um, best_ra_um,
                    max_x_mm, max_y_mm, max_z_mm,
                    max_turning_diameter_mm, max_turning_length_mm,
                    capability_source)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           'spec_based_estimate')
                   ON CONFLICT DO NOTHING""",
                (cid, cat_code, pc, it, it, ra, ra, mx, my, mz, td, tl),
            )

    # company_availability_snapshot
    db.execute_insert(
        """INSERT INTO imma.company_availability_snapshot
           (company_id, overall_status, next_available_date)
           VALUES (%s, %s, %s)""",
        (cid, availability[0], availability[1]),
    )

    # reviews (mock buyer 필요)
    buyer_name = f"테스트바이어_{name}"
    buyer_login_id = f"buyer_{login_slug}"
    existing_buyer = db.execute_query(
        "SELECT buyer_id FROM imma.buyers WHERE buyer_name = %s",
        (buyer_name,),
    )
    if existing_buyer:
        bid = str(existing_buyer[0]["buyer_id"])
    else:
        buyer_row = db.execute_returning(
            """INSERT INTO imma.buyers (buyer_name, login_id, email, password_hash)
               VALUES (%s, %s, %s, %s) RETURNING buyer_id""",
            (buyer_name, buyer_login_id, f"test_{name}@example.com",
             _MOCK_PASSWORD_HASH),
        )
        bid = str(buyer_row[0])
    for r in ratings:
        (ro, rq, rd, rc, rp) = r
        db.execute_insert(
            """INSERT INTO imma.reviews
               (company_id, buyer_id, rating_overall, rating_quality,
                rating_delivery, rating_communication, rating_price)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (cid, bid, ro, rq, rd, rc, rp),
        )


def _seed_admin() -> None:
    """관리자 계정 1개를 admins 테이블에 seed한다. 테이블 미존재 시 안전하게 넘어간다."""
    try:
        existing = db.execute_query(
            "SELECT admin_id FROM imma.admins WHERE login_id = %s",
            ("admin",),
        )
        if existing:
            logger.info("admin 계정 이미 존재 — 스킵")
            return
        db.execute_insert(
            """INSERT INTO imma.admins (login_id, name, email, password_hash, role)
               VALUES (%s, %s, %s, %s, %s)""",
            ("admin", "관리자", "admin@imma.local", _MOCK_PASSWORD_HASH, "superadmin"),
        )
        logger.info("admin seed 완료")
    except Exception:
        logger.warning("admins 테이블 seed 실패 (테이블 미존재 가능)", exc_info=True)


def _seed_demo_buyers() -> None:
    """데모용 발주자 계정을 seed한다."""
    demo_pw = _hash_password("demo1234")
    buyers = [
        ("kim_cheolsu", "김철수", "세진테크 반도체장비사업부", "cheolsu@sejin-demo.com", "010-1234-5678"),
        ("park_jiyoung", "박지영", "대한모터스 소형차설계팀", "jiyoung@daehan-demo.com", "010-2345-6789"),
        ("lee_minho", "이민호", "한성에너지 터빈사업부", "minho@hansung-demo.com", "010-3456-7890"),
        ("choi_soojin", "최수진", "미래항공 정밀기계팀", "soojin@mirae-demo.com", "010-4567-8901"),
        ("jung_taewoo", "정태우", "광진이노텍 광학솔루션팀", "taewoo@gwangjin-demo.com", "010-5678-9012"),
    ]
    for login_id, name, company, email, phone in buyers:
        db.execute_insert(
            """INSERT INTO imma.buyers (login_id, buyer_name, company_name, email, phone, password_hash)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (login_id) DO NOTHING""",
            (login_id, name, company, email, phone, demo_pw),
        )
    logger.info("데모 발주자 %d명 seed", len(buyers))


def _seed_equipment_schedule() -> None:
    """장비별 90일 스케줄 + 업체 가용성 snapshot을 seed한다."""
    from datetime import date, timedelta
    import random
    random.seed(42)

    base_date = date(2026, 5, 18)

    company_profiles = {
        "A정밀": "busy", "B공업": "moderate", "C테크": "moderate", "D가공": "free",
        "E제작": "moderate", "F정밀": "busy", "G주철": "free", "H정공": "busy",
        "I에어로": "busy", "J복합": "moderate", "K방전": "moderate", "L범용": "free",
        "M대형": "free", "N밸브": "busy", "O워터젯": "moderate", "P프레스": "moderate",
        "Q범용선반": "free", "R수지가공": "free", "S스텐주조": "free",
    }

    patterns = {
        "busy": {"available": 0.15, "partially_booked": 0.35, "fully_booked": 0.40, "maintenance": 0.10},
        "moderate": {"available": 0.30, "partially_booked": 0.40, "fully_booked": 0.20, "maintenance": 0.10},
        "free": {"available": 0.50, "partially_booked": 0.30, "fully_booked": 0.10, "maintenance": 0.10},
    }

    equipment = db.execute_query(
        """SELECT c.company_id, c.company_name, e.equipment_id
           FROM imma.companies c JOIN imma.equipment e ON c.company_id = e.company_id
           WHERE e.status = 'running'"""
    )

    inserted = 0
    for eq in equipment:
        company = eq["company_name"]
        eid = str(eq["equipment_id"])
        cid = str(eq["company_id"])
        profile = company_profiles.get(company, "moderate")
        probs = patterns[profile]

        for day_offset in range(90):
            d = base_date + timedelta(days=day_offset)
            if d.weekday() >= 5:
                status = "off" if random.random() > 0.2 else "available"
                booked = 0.0 if status == "off" else 2.0
            else:
                r = random.random()
                cumul = 0
                status = "available"
                for s, p in probs.items():
                    cumul += p
                    if r <= cumul:
                        status = s
                        break
                if status == "available":
                    booked = round(random.uniform(0.0, 2.0), 1)
                elif status == "partially_booked":
                    booked = round(random.uniform(3.0, 6.0), 1)
                elif status == "fully_booked":
                    booked = round(random.uniform(7.0, 8.0), 1)
                elif status == "maintenance":
                    booked = 0.0

            db.execute_insert(
                """INSERT INTO imma.equipment_daily_schedule
                   (equipment_id, company_id, schedule_date, status, planned_hours, booked_hours)
                   VALUES (%s::uuid, %s::uuid, %s, %s, 8.0, %s)
                   ON CONFLICT (equipment_id, schedule_date) DO UPDATE SET
                       status = EXCLUDED.status, booked_hours = EXCLUDED.booked_hours""",
                (eid, cid, d.isoformat(), status, booked),
            )
            inserted += 1

    # snapshot 보정
    for cname, profile in company_profiles.items():
        nad = {
            "busy": date(2026, 5, 22),
            "moderate": date(2026, 5, 20),
            "free": date(2026, 5, 18),
        }[profile]
        snap_status = "limited" if profile == "busy" else "available"
        db.execute_insert(
            """UPDATE imma.company_availability_snapshot cas
               SET overall_status = %s, next_available_date = %s
               FROM imma.companies c
               WHERE cas.company_id = c.company_id AND c.company_name = %s""",
            (snap_status, nad.isoformat(), cname),
        )

    logger.info("장비 스케줄 %d행 + snapshot 보정 완료", inserted)


def setup_all() -> None:
    """create_schema -> seed_reference_data -> load_equipment_catalog -> load_mock_companies -> admin/buyer/schedule seed 순차 실행."""
    create_schema()
    seed_reference_data()
    load_equipment_catalog()
    load_mock_companies()
    _seed_admin()
    _seed_demo_buyers()
    _seed_equipment_schedule()
    logger.info("전체 셋업 완료")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    setup_all()
