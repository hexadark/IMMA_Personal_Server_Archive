-- IMMA PostgreSQL schema v1
-- Purpose: manufacturer DB for hard-filter matching, RFQ/quote/order history, and Phase 2/3 extension.
-- PostgreSQL 15+ recommended. Uses pgcrypto for UUID and citext for case-insensitive email/code matching.

CREATE SCHEMA IF NOT EXISTS imma;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;

SET search_path TO imma, public;

-- -----------------------------------------------------------------------------
-- 1. Reference catalogs
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS process_catalog (
    process_code            text PRIMARY KEY,
    parent_process_code     text REFERENCES process_catalog(process_code),
    process_name_ko         text NOT NULL,
    process_name_en         text,
    process_group           text NOT NULL,
    din8580_group_code      text,
    description             text,
    is_active               boolean NOT NULL DEFAULT true,
    created_at              timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE process_catalog IS 'Canonical process codes used by VLM, PostgreSQL filters, and Neo4j ontology.';
COMMENT ON COLUMN process_catalog.process_code IS 'Examples: turning, milling, grinding, cylindrical_grinding, edm_wire, hobbing, heat_treatment, welding, sheet_metal.';

CREATE TABLE IF NOT EXISTS material_category_catalog (
    category_code           text PRIMARY KEY,
    category_name_ko        text NOT NULL,
    category_name_en        text,
    description             text,
    is_active               boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS materials (
    material_id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    material_code           citext NOT NULL UNIQUE,
    material_name_ko        text,
    material_name_en        text,
    category_code           text NOT NULL REFERENCES material_category_catalog(category_code),
    ks_code                 text,
    jis_code                text,
    aisi_sae_code           text,
    astm_or_uns_code        text,
    typical_usage           text,
    equivalence_confidence  text NOT NULL DEFAULT 'exact'
                            CHECK (equivalence_confidence IN ('exact','approximate','alias','unknown')),
    is_active               boolean NOT NULL DEFAULT true,
    created_at              timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE materials IS 'Canonical material master. Seed from IMMA material mapping lookup tables.';

CREATE TABLE IF NOT EXISTS material_aliases (
    alias_text              citext PRIMARY KEY,
    material_id             uuid NOT NULL REFERENCES materials(material_id) ON DELETE CASCADE,
    alias_type              text NOT NULL DEFAULT 'drawing_alias'
                            CHECK (alias_type IN ('drawing_alias','legacy_code','jis_alias','trade_name','user_input')),
    note                    text
);

CREATE TABLE IF NOT EXISTS equipment_category_catalog (
    equipment_category_code text PRIMARY KEY,
    category_name_ko        text NOT NULL,
    category_name_en        text,
    default_process_group   text,
    description             text,
    is_active               boolean NOT NULL DEFAULT true
);

COMMENT ON COLUMN equipment_category_catalog.equipment_category_code IS 'Examples: cnc_lathe, general_lathe, machining_center_3axis, machining_center_5axis, mill_turn, surface_grinder, cylindrical_grinder, internal_grinder, edm_sinker, edm_wire, hobbing_machine, drilling_machine, boring_machine, heat_treatment_furnace, welding_equipment, laser_cutting_machine, press_brake, press_machine, plasma_cutting_machine, waterjet_cutting_machine.';

CREATE TABLE IF NOT EXISTS equipment_model_catalog (
    model_id        text PRIMARY KEY,
    manufacturer    text NOT NULL,
    model_name      text NOT NULL,
    equipment_category_code text NOT NULL REFERENCES equipment_category_catalog(equipment_category_code),
    -- common specs
    max_workpiece_weight_kg numeric(12,3),
    -- 나머지 스펙은 jsonb로 저장 (장비 종류마다 필드가 다르므로)
    category_specs  jsonb NOT NULL DEFAULT '{}'::jsonb,
    process_capabilities jsonb NOT NULL DEFAULT '[]'::jsonb,
    non_machining_capabilities jsonb NOT NULL DEFAULT '{}'::jsonb,
    source          jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE equipment_model_catalog IS '장비 모델 카탈로그. 실제 장비 스펙 레퍼런스. equipment 테이블이 model_id로 참조.';

CREATE TABLE IF NOT EXISTS certification_catalog (
    certification_code      text PRIMARY KEY,
    certification_name      text NOT NULL,
    issuer_type             text,
    description             text,
    is_active               boolean NOT NULL DEFAULT true
);

-- -----------------------------------------------------------------------------
-- 2. Company master and onboarding information
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS companies (
    company_id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    neo4j_company_key       text UNIQUE,
    company_name            text NOT NULL,
    login_id                citext NOT NULL UNIQUE,
    business_registration_no text UNIQUE,
    representative_name     text,
    company_size            text NOT NULL DEFAULT 'unknown'
                            CHECK (company_size IN ('micro','small','medium','large','unknown')),
    employee_count          integer CHECK (employee_count IS NULL OR employee_count >= 0),
    established_year        integer CHECK (established_year IS NULL OR established_year BETWEEN 1900 AND 2100),
    main_phone              text,
    main_email              citext NOT NULL,
    password_hash           text NOT NULL,
    website_url             text,
    status                  text NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','active','paused','suspended','deleted')),
    onboarding_status       text NOT NULL DEFAULT 'draft'
                            CHECK (onboarding_status IN ('draft','submitted','verified','rejected')),
    accepting_orders        boolean NOT NULL DEFAULT true,
    notes                   text,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE companies IS 'Supplier/manufacturer master. company_id is also used as the join key to Neo4j.';

CREATE TABLE IF NOT EXISTS company_sites (
    site_id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id              uuid NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    site_name               text NOT NULL DEFAULT '본사/공장',
    is_primary              boolean NOT NULL DEFAULT false,
    country_code            char(2) NOT NULL DEFAULT 'KR',
    region                  text,
    city                    text,
    district                text,
    postal_code             text,
    address_line1           text,
    address_line2           text,
    latitude                numeric(9,6) CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
    longitude               numeric(9,6) CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180),
    created_at              timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, site_name)
);

CREATE TABLE IF NOT EXISTS company_contacts (
    contact_id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id              uuid NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    contact_name            text NOT NULL DEFAULT '대표',
    role_title              text,
    phone                   text,
    email                   citext,
    messenger               text,
    is_primary              boolean NOT NULL DEFAULT false,
    receives_rfq            boolean NOT NULL DEFAULT true,
    created_at              timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, contact_name)
);

-- -----------------------------------------------------------------------------
-- 3. Supplier capabilities: materials, process services, equipment
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS company_material_capabilities (
    company_material_capability_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id              uuid NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    scope_type              text NOT NULL CHECK (scope_type IN ('specific_material','material_category','free_text')),
    material_id             uuid REFERENCES materials(material_id),
    material_category_code  text REFERENCES material_category_catalog(category_code),
    raw_input_text          text,
    capability_level        text NOT NULL DEFAULT 'regular'
                            CHECK (capability_level IN ('regular','possible','consult','not_preferred')),
    max_hardness_hrc        numeric(5,2),
    min_order_quantity      integer CHECK (min_order_quantity IS NULL OR min_order_quantity >= 0),
    notes                   text,
    verification_level      text NOT NULL DEFAULT 'self_reported'
                            CHECK (verification_level IN ('self_reported','document_verified','job_history_verified','inspection_verified')),
    is_active               boolean NOT NULL DEFAULT true,
    created_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT material_capability_scope_chk CHECK (
        (scope_type = 'specific_material' AND material_id IS NOT NULL AND material_category_code IS NULL)
        OR (scope_type = 'material_category' AND material_id IS NULL AND material_category_code IS NOT NULL)
        OR (scope_type = 'free_text' AND material_id IS NULL AND raw_input_text IS NOT NULL)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_company_material_specific
    ON company_material_capabilities (company_id, material_id)
    WHERE scope_type = 'specific_material';
CREATE UNIQUE INDEX IF NOT EXISTS uq_company_material_category
    ON company_material_capabilities (company_id, material_category_code)
    WHERE scope_type = 'material_category';

CREATE TABLE IF NOT EXISTS company_process_capabilities (
    company_process_capability_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id              uuid NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    process_code            text NOT NULL REFERENCES process_catalog(process_code),
    service_mode            text NOT NULL DEFAULT 'in_house'
                            CHECK (service_mode IN ('in_house','outsourced','both')),
    capability_level        text NOT NULL DEFAULT 'primary'
                            CHECK (capability_level IN ('primary','secondary','consult','not_preferred')),
    best_achievable_it_grade smallint CHECK (best_achievable_it_grade IS NULL OR best_achievable_it_grade BETWEEN 1 AND 18),
    best_tolerance_mm       numeric(10,5) CHECK (best_tolerance_mm IS NULL OR best_tolerance_mm >= 0),
    best_ra_um              numeric(8,3) CHECK (best_ra_um IS NULL OR best_ra_um >= 0),
    max_x_mm                numeric(12,3) CHECK (max_x_mm IS NULL OR max_x_mm >= 0),
    max_y_mm                numeric(12,3) CHECK (max_y_mm IS NULL OR max_y_mm >= 0),
    max_z_mm                numeric(12,3) CHECK (max_z_mm IS NULL OR max_z_mm >= 0),
    max_turning_diameter_mm numeric(12,3) CHECK (max_turning_diameter_mm IS NULL OR max_turning_diameter_mm >= 0),
    max_turning_length_mm   numeric(12,3) CHECK (max_turning_length_mm IS NULL OR max_turning_length_mm >= 0),
    typical_lead_days       integer CHECK (typical_lead_days IS NULL OR typical_lead_days >= 0),
    notes                   text,
    verification_level      text NOT NULL DEFAULT 'self_reported'
                            CHECK (verification_level IN ('self_reported','document_verified','job_history_verified','inspection_verified')),
    is_active               boolean NOT NULL DEFAULT true,
    created_at              timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, process_code, service_mode)
);

COMMENT ON COLUMN company_process_capabilities.best_achievable_it_grade IS 'Lower number is tighter. Example: 6 means the company claims IT6 and looser grades are generally achievable for this process.';

CREATE TABLE IF NOT EXISTS equipment (
    equipment_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id              uuid NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    site_id                 uuid REFERENCES company_sites(site_id) ON DELETE SET NULL,
    equipment_category_code text NOT NULL REFERENCES equipment_category_catalog(equipment_category_code),
    model_id                text REFERENCES equipment_model_catalog(model_id),
    display_name            text NOT NULL,
    manufacturer            text,
    model_name              text,
    year_made               integer CHECK (year_made IS NULL OR year_made BETWEEN 1950 AND 2100),
    quantity                integer NOT NULL DEFAULT 1 CHECK (quantity >= 1),
    axis_count              smallint CHECK (axis_count IS NULL OR axis_count > 0),
    controller              text,

    -- Work envelope for prismatic processes such as milling/grinding/EDM.
    max_x_travel_mm         numeric(12,3) CHECK (max_x_travel_mm IS NULL OR max_x_travel_mm >= 0),
    max_y_travel_mm         numeric(12,3) CHECK (max_y_travel_mm IS NULL OR max_y_travel_mm >= 0),
    max_z_travel_mm         numeric(12,3) CHECK (max_z_travel_mm IS NULL OR max_z_travel_mm >= 0),
    table_size_x_mm         numeric(12,3) CHECK (table_size_x_mm IS NULL OR table_size_x_mm >= 0),
    table_size_y_mm         numeric(12,3) CHECK (table_size_y_mm IS NULL OR table_size_y_mm >= 0),
    max_workpiece_weight_kg numeric(12,3) CHECK (max_workpiece_weight_kg IS NULL OR max_workpiece_weight_kg >= 0),

    -- Work envelope for turning/rotary processes.
    max_turning_diameter_mm numeric(12,3) CHECK (max_turning_diameter_mm IS NULL OR max_turning_diameter_mm >= 0),
    max_turning_length_mm   numeric(12,3) CHECK (max_turning_length_mm IS NULL OR max_turning_length_mm >= 0),

    -- Claimed or verified capability.
    best_achievable_it_grade smallint CHECK (best_achievable_it_grade IS NULL OR best_achievable_it_grade BETWEEN 1 AND 18),
    best_tolerance_mm       numeric(10,5) CHECK (best_tolerance_mm IS NULL OR best_tolerance_mm >= 0),
    best_ra_um              numeric(8,3) CHECK (best_ra_um IS NULL OR best_ra_um >= 0),

    -- Spindle/spec fields. Nullable because many non-spindle machines do not have them.
    spindle_max_rpm         integer CHECK (spindle_max_rpm IS NULL OR spindle_max_rpm >= 0),
    spindle_power_kw        numeric(8,2) CHECK (spindle_power_kw IS NULL OR spindle_power_kw >= 0),
    tool_capacity           integer CHECK (tool_capacity IS NULL OR tool_capacity >= 0),

    status                  text NOT NULL DEFAULT 'running'
                            CHECK (status IN ('running','idle','maintenance','down','retired')),
    last_maintenance_date   date,
    next_maintenance_date   date,
    notes                   text,
    verification_level      text NOT NULL DEFAULT 'self_reported'
                            CHECK (verification_level IN ('self_reported','photo_verified','document_verified','job_history_verified','inspection_verified')),
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS equipment_process_capabilities (
    equipment_id            uuid NOT NULL REFERENCES equipment(equipment_id) ON DELETE CASCADE,
    process_code            text NOT NULL REFERENCES process_catalog(process_code),
    is_primary_process      boolean NOT NULL DEFAULT true,
    best_achievable_it_grade smallint CHECK (best_achievable_it_grade IS NULL OR best_achievable_it_grade BETWEEN 1 AND 18),
    best_tolerance_mm       numeric(10,5) CHECK (best_tolerance_mm IS NULL OR best_tolerance_mm >= 0),
    best_ra_um              numeric(8,3) CHECK (best_ra_um IS NULL OR best_ra_um >= 0),
    notes                   text,
    PRIMARY KEY (equipment_id, process_code)
);

-- -----------------------------------------------------------------------------
-- 4. Availability and scheduling
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS company_availability_snapshot (
    company_id              uuid PRIMARY KEY REFERENCES companies(company_id) ON DELETE CASCADE,
    overall_status          text NOT NULL DEFAULT 'unknown'
                            CHECK (overall_status IN ('available','limited','full','paused','unknown')),
    current_utilization_pct numeric(5,2) CHECK (current_utilization_pct IS NULL OR current_utilization_pct BETWEEN 0 AND 100),
    available_hours_per_week numeric(8,2) CHECK (available_hours_per_week IS NULL OR available_hours_per_week >= 0),
    next_available_date     date,
    min_lead_time_days      integer CHECK (min_lead_time_days IS NULL OR min_lead_time_days >= 0),
    max_parallel_jobs       integer CHECK (max_parallel_jobs IS NULL OR max_parallel_jobs >= 0),
    last_updated_at         timestamptz NOT NULL DEFAULT now(),
    notes                   text
);

CREATE TABLE IF NOT EXISTS company_capacity_calendar (
    capacity_id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id              uuid NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    equipment_id            uuid REFERENCES equipment(equipment_id) ON DELETE CASCADE,
    process_code            text REFERENCES process_catalog(process_code),
    week_start_date         date NOT NULL,
    status                  text NOT NULL DEFAULT 'available'
                            CHECK (status IN ('available','limited','full','maintenance','holiday','unknown')),
    planned_capacity_hours  numeric(8,2) NOT NULL DEFAULT 0 CHECK (planned_capacity_hours >= 0),
    booked_hours            numeric(8,2) NOT NULL DEFAULT 0 CHECK (booked_hours >= 0),
    available_hours         numeric(8,2) GENERATED ALWAYS AS (GREATEST(planned_capacity_hours - booked_hours, 0)) STORED,
    notes                   text,
    updated_at              timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, equipment_id, process_code, week_start_date)
);

-- -----------------------------------------------------------------------------
-- 5. Buyer/RFQ/quote/order/job history
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS buyers (
    buyer_id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    buyer_name              text NOT NULL,
    company_name            text,
    login_id                citext NOT NULL UNIQUE,
    contact_name            text,
    email                   citext NOT NULL,
    phone                   text,
    password_hash           text NOT NULL,
    region                  text,
    company_scale           text,
    created_at              timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS drawings (
    drawing_id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    buyer_id                uuid REFERENCES buyers(buyer_id) ON DELETE SET NULL,
    drawing_no              text,
    file_uri                text,
    file_sha256             text UNIQUE,
    original_filename       text,
    vlm_result_jsonb        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at              timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rfqs (
    rfq_id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    buyer_id                uuid REFERENCES buyers(buyer_id) ON DELETE SET NULL,
    drawing_id              uuid REFERENCES drawings(drawing_id) ON DELETE SET NULL,
    rfq_no                  text UNIQUE,
    status                  text NOT NULL DEFAULT 'open'
                            CHECK (status IN ('draft','open','quoted','closed','cancelled','ordered')),
    requested_delivery_date date,
    order_quantity          integer CHECK (order_quantity IS NULL OR order_quantity > 0),
    budget_amount           numeric(14,2) CHECK (budget_amount IS NULL OR budget_amount >= 0),
    budget_currency         char(3) DEFAULT 'KRW',
    general_notes_jsonb     jsonb NOT NULL DEFAULT '[]'::jsonb,
    referenced_standards_jsonb jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now()
);

-- rfq_no 자동 생성 트리거 (RFQ-YYYYMMDD-NNNN)
CREATE OR REPLACE FUNCTION generate_rfq_no()
RETURNS TRIGGER AS $$
DECLARE
    today_str text;
    next_seq int;
BEGIN
    IF NEW.rfq_no IS NULL THEN
        today_str := to_char(COALESCE(NEW.created_at, now()), 'YYYYMMDD');
        SELECT COALESCE(MAX(CAST(SUBSTRING(rfq_no FROM 'RFQ-' || today_str || '-(\d+)') AS int)), 0) + 1
            INTO next_seq
            FROM rfqs
            WHERE rfq_no LIKE 'RFQ-' || today_str || '-%';
        NEW.rfq_no := 'RFQ-' || today_str || '-' || lpad(next_seq::text, 4, '0');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_rfq_no_auto_gen ON rfqs;
CREATE TRIGGER trg_rfq_no_auto_gen
    BEFORE INSERT ON rfqs
    FOR EACH ROW
    EXECUTE FUNCTION generate_rfq_no();


CREATE TABLE IF NOT EXISTS rfq_parts (
    rfq_part_id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id                  uuid NOT NULL REFERENCES rfqs(rfq_id) ON DELETE CASCADE,
    part_name               text,
    material_raw_text       text,
    material_id             uuid REFERENCES materials(material_id),
    material_category_code  text REFERENCES material_category_catalog(category_code),
    quantity                integer NOT NULL DEFAULT 1 CHECK (quantity > 0),
    envelope_length_mm      numeric(12,3) CHECK (envelope_length_mm IS NULL OR envelope_length_mm >= 0),
    envelope_width_mm       numeric(12,3) CHECK (envelope_width_mm IS NULL OR envelope_width_mm >= 0),
    envelope_height_mm      numeric(12,3) CHECK (envelope_height_mm IS NULL OR envelope_height_mm >= 0),
    envelope_diameter_mm    numeric(12,3) CHECK (envelope_diameter_mm IS NULL OR envelope_diameter_mm >= 0),
    tightest_it_grade       smallint CHECK (tightest_it_grade IS NULL OR tightest_it_grade BETWEEN 1 AND 18),
    tightest_tolerance_mm   numeric(10,5) CHECK (tightest_tolerance_mm IS NULL OR tightest_tolerance_mm >= 0),
    finest_ra_um            numeric(8,3) CHECK (finest_ra_um IS NULL OR finest_ra_um >= 0),
    gdt_jsonb               jsonb NOT NULL DEFAULT '[]'::jsonb,
    tolerances_jsonb        jsonb NOT NULL DEFAULT '[]'::jsonb,
    surface_roughness_jsonb jsonb NOT NULL DEFAULT '[]'::jsonb,
    post_treatment_raw      text,
    vlm_part_jsonb          jsonb NOT NULL DEFAULT '{}'::jsonb,
    part_status             text NOT NULL DEFAULT 'pending'
                            CHECK (part_status IN ('pending','matched','rejected','supplemented')),
    created_at              timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rfq_part_processes (
    rfq_part_id             uuid NOT NULL REFERENCES rfq_parts(rfq_part_id) ON DELETE CASCADE,
    process_code            text NOT NULL REFERENCES process_catalog(process_code),
    sequence_order          integer CHECK (sequence_order IS NULL OR sequence_order >= 0),
    is_required             boolean NOT NULL DEFAULT true,
    raw_text                text,
    PRIMARY KEY (rfq_part_id, process_code)
);

CREATE TABLE IF NOT EXISTS quote_responses (
    quote_id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id                  uuid NOT NULL REFERENCES rfqs(rfq_id) ON DELETE CASCADE,
    company_id              uuid NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    status                  text NOT NULL DEFAULT 'draft'
                            CHECK (status IN ('draft','submitted','accepted','rejected','withdrawn','expired')),
    total_price             numeric(14,2) CHECK (total_price IS NULL OR total_price >= 0),
    currency_code           char(3) NOT NULL DEFAULT 'KRW',
    estimated_lead_days     integer CHECK (estimated_lead_days IS NULL OR estimated_lead_days >= 0),
    proposed_delivery_date  date,
    validity_until          date,
    assumptions             text,
    submitted_at            timestamptz,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    UNIQUE (rfq_id, company_id)
);

CREATE TABLE IF NOT EXISTS quote_line_items (
    quote_line_item_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    quote_id                uuid NOT NULL REFERENCES quote_responses(quote_id) ON DELETE CASCADE,
    rfq_part_id             uuid REFERENCES rfq_parts(rfq_part_id) ON DELETE SET NULL,
    process_code            text REFERENCES process_catalog(process_code),
    item_description        text,
    quantity                integer CHECK (quantity IS NULL OR quantity > 0),
    unit_price              numeric(14,2) CHECK (unit_price IS NULL OR unit_price >= 0),
    line_total              numeric(14,2) CHECK (line_total IS NULL OR line_total >= 0),
    notes                   text
);

CREATE TABLE IF NOT EXISTS orders (
    order_id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    quote_id                uuid UNIQUE REFERENCES quote_responses(quote_id) ON DELETE SET NULL,
    rfq_id                  uuid REFERENCES rfqs(rfq_id) ON DELETE SET NULL,
    buyer_id                uuid REFERENCES buyers(buyer_id) ON DELETE SET NULL,
    company_id              uuid NOT NULL REFERENCES companies(company_id) ON DELETE RESTRICT,
    status                  text NOT NULL DEFAULT 'contracting'
                            CHECK (status IN ('contracting','ordered','in_production','inspection','shipped','delivered','completed','cancelled','disputed')),
    nda_signed_at           timestamptz,
    contract_signed_at      timestamptz,
    production_start_date   date,
    promised_delivery_date  date,
    actual_delivery_date    date,
    total_price             numeric(14,2) CHECK (total_price IS NULL OR total_price >= 0),
    currency_code           char(3) NOT NULL DEFAULT 'KRW',
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS manufacturing_jobs (
    job_id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id                uuid REFERENCES orders(order_id) ON DELETE SET NULL,
    company_id              uuid NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    rfq_part_id             uuid REFERENCES rfq_parts(rfq_part_id) ON DELETE SET NULL,
    drawing_id              uuid REFERENCES drawings(drawing_id) ON DELETE SET NULL,

    part_name               text,
    material_id             uuid REFERENCES materials(material_id),
    material_category_code  text REFERENCES material_category_catalog(category_code),
    material_raw_text       text,
    quantity                integer CHECK (quantity IS NULL OR quantity > 0),

    envelope_length_mm      numeric(12,3),
    envelope_width_mm       numeric(12,3),
    envelope_height_mm      numeric(12,3),
    envelope_diameter_mm    numeric(12,3),
    tightest_it_grade       smallint CHECK (tightest_it_grade IS NULL OR tightest_it_grade BETWEEN 1 AND 18),
    tightest_tolerance_mm   numeric(10,5),
    finest_ra_um            numeric(8,3),
    gdt_jsonb               jsonb NOT NULL DEFAULT '[]'::jsonb,
    tolerances_jsonb        jsonb NOT NULL DEFAULT '[]'::jsonb,
    surface_roughness_jsonb jsonb NOT NULL DEFAULT '[]'::jsonb,
    drawing_summary_jsonb   jsonb NOT NULL DEFAULT '{}'::jsonb,

    quoted_price            numeric(14,2),
    actual_price            numeric(14,2),
    estimated_lead_days     integer,
    actual_lead_days        integer,
    promised_delivery_date  date,
    actual_delivery_date    date,

    job_status              text NOT NULL DEFAULT 'pending'
                            CHECK (job_status IN ('pending','material_received','in_progress','post_processing','inspection','packaging','completed','cancelled')),
    quality_status          text CHECK (quality_status IN ('pass','conditional_pass','fail','rework','unknown')),
    rework_count            integer NOT NULL DEFAULT 0 CHECK (rework_count >= 0),
    scrap_count             integer NOT NULL DEFAULT 0 CHECK (scrap_count >= 0),
    inspection_summary_jsonb jsonb NOT NULL DEFAULT '{}'::jsonb,
    client_rating_snapshot  numeric(3,2) CHECK (client_rating_snapshot IS NULL OR client_rating_snapshot BETWEEN 1 AND 5),

    qdrant_collection       text,
    qdrant_point_id         text,
    embedding_model         text,
    embedding_version       text,

    completed_at            timestamptz,
    created_at              timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE manufacturing_jobs IS 'Flattened historical job records for performance metrics and Qdrant similar-job search metadata.';

CREATE TABLE IF NOT EXISTS job_processes (
    job_id                  uuid NOT NULL REFERENCES manufacturing_jobs(job_id) ON DELETE CASCADE,
    process_code            text NOT NULL REFERENCES process_catalog(process_code),
    sequence_order          integer CHECK (sequence_order IS NULL OR sequence_order >= 0),
    was_outsourced          boolean NOT NULL DEFAULT false,
    PRIMARY KEY (job_id, process_code)
);

-- -----------------------------------------------------------------------------
-- 6. Reviews, certifications, and linked partners
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS reviews (
    review_id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id              uuid NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    buyer_id                uuid REFERENCES buyers(buyer_id) ON DELETE SET NULL,
    order_id                uuid REFERENCES orders(order_id) ON DELETE SET NULL,
    job_id                  uuid REFERENCES manufacturing_jobs(job_id) ON DELETE SET NULL,
    rating_overall          numeric(3,2) NOT NULL CHECK (rating_overall BETWEEN 1 AND 5),
    rating_quality          numeric(3,2) CHECK (rating_quality IS NULL OR rating_quality BETWEEN 1 AND 5),
    rating_delivery         numeric(3,2) CHECK (rating_delivery IS NULL OR rating_delivery BETWEEN 1 AND 5),
    rating_communication    numeric(3,2) CHECK (rating_communication IS NULL OR rating_communication BETWEEN 1 AND 5),
    rating_price            numeric(3,2) CHECK (rating_price IS NULL OR rating_price BETWEEN 1 AND 5),
    comment                 text,
    is_public               boolean NOT NULL DEFAULT true,
    created_at              timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS company_certifications (
    company_certification_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id              uuid NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    certification_code      text REFERENCES certification_catalog(certification_code),
    certification_name_raw  text,
    certificate_no          text,
    issuer                  text,
    issued_at               date,
    expires_at              date,
    document_uri            text,
    verification_status     text NOT NULL DEFAULT 'self_reported'
                            CHECK (verification_status IN ('self_reported','uploaded','verified','expired','rejected')),
    notes                   text,
    created_at              timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS company_partners (
    partner_id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id              uuid NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    partner_company_id      uuid REFERENCES companies(company_id) ON DELETE SET NULL,
    partner_name            text NOT NULL,
    partner_type            text NOT NULL DEFAULT 'subcontractor'
                            CHECK (partner_type IN ('subcontractor','group_company','certified_partner','informal_partner')),
    contact_phone           text,
    contact_email           citext,
    region                  text,
    nda_signed              boolean NOT NULL DEFAULT false,
    active                  boolean NOT NULL DEFAULT true,
    notes                   text,
    created_at              timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS company_partner_services (
    partner_service_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_id              uuid NOT NULL REFERENCES company_partners(partner_id) ON DELETE CASCADE,
    process_code            text REFERENCES process_catalog(process_code),
    material_category_code  text REFERENCES material_category_catalog(category_code),
    service_scope           text,
    typical_lead_days       integer CHECK (typical_lead_days IS NULL OR typical_lead_days >= 0),
    notes                   text
);

-- -----------------------------------------------------------------------------
-- 7. Optional Phase 2/3 audit tables for match runs and score explanation
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS match_runs (
    match_run_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id                  uuid REFERENCES rfqs(rfq_id) ON DELETE CASCADE,
    rfq_part_id             uuid REFERENCES rfq_parts(rfq_part_id) ON DELETE CASCADE,
    algorithm_version       text NOT NULL,
    mode                    text NOT NULL DEFAULT 'hard_filter'
                            CHECK (mode IN ('hard_filter','hybrid','agent')),
    input_summary_jsonb     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at              timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS match_candidates (
    match_run_id            uuid NOT NULL REFERENCES match_runs(match_run_id) ON DELETE CASCADE,
    company_id              uuid NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    rfq_part_id             uuid REFERENCES rfq_parts(rfq_part_id) ON DELETE CASCADE,
    hard_filter_pass        boolean NOT NULL DEFAULT false,
    technical_score         numeric(6,3),
    availability_score      numeric(6,3),
    quality_score           numeric(6,3),
    total_score             numeric(6,3),
    explanation_jsonb       jsonb NOT NULL DEFAULT '{}'::jsonb,
    rank_no                 integer,
    supplier_response       text NOT NULL DEFAULT 'pending'
                            CHECK (supplier_response IN ('pending','accepted','declined','expired')),
    responded_at            timestamptz,
    created_at              timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (match_run_id, company_id, rfq_part_id)
);

CREATE TABLE IF NOT EXISTS ontology_sync_refs (
    entity_type             text NOT NULL CHECK (entity_type IN ('company','equipment','process','material','job')),
    entity_id               uuid NOT NULL,
    neo4j_node_key          text NOT NULL,
    last_synced_at          timestamptz,
    sync_status             text NOT NULL DEFAULT 'pending'
                            CHECK (sync_status IN ('pending','synced','failed','disabled')),
    PRIMARY KEY (entity_type, entity_id)
);

-- -----------------------------------------------------------------------------
-- 7b. Notifications, admins, shipments, delivery_images
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS notifications (
    notification_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recipient_type      text NOT NULL CHECK (recipient_type IN ('buyer','supplier','admin')),
    recipient_id        uuid NOT NULL,
    event_type          text NOT NULL,
    title               text NOT NULL,
    message             text,
    reference_id        uuid,
    reference_type      text,
    is_read             boolean NOT NULL DEFAULT false,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS admins (
    admin_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    login_id            citext NOT NULL UNIQUE,
    name                text NOT NULL,
    email               citext NOT NULL,
    password_hash       text NOT NULL,
    role                text NOT NULL DEFAULT 'operator'
                        CHECK (role IN ('operator','manager','superadmin')),
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS shipments (
    shipment_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id            uuid NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    shipping_method     text,
    tracking_number     text,
    shipped_at          timestamptz,
    delivery_address    text,
    notes               text,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS delivery_images (
    image_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id              uuid NOT NULL REFERENCES manufacturing_jobs(job_id) ON DELETE CASCADE,
    image_type          text NOT NULL CHECK (image_type IN ('final_product','packaging','inspection_report')),
    file_uri            text NOT NULL,
    uploaded_at         timestamptz NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- 7c. Equipment daily schedule (일단위 장비 스케줄)
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS equipment_daily_schedule (
    schedule_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    equipment_id        uuid NOT NULL REFERENCES equipment(equipment_id) ON DELETE CASCADE,
    company_id          uuid NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    schedule_date       date NOT NULL,
    status              text NOT NULL DEFAULT 'available'
                        CHECK (status IN ('available','partially_booked','fully_booked','maintenance','holiday','off')),
    planned_hours       numeric(5,2) NOT NULL DEFAULT 8.0 CHECK (planned_hours >= 0),
    booked_hours        numeric(5,2) NOT NULL DEFAULT 0 CHECK (booked_hours >= 0),
    available_hours     numeric(5,2) GENERATED ALWAYS AS (GREATEST(planned_hours - booked_hours, 0)) STORED,
    order_id            uuid REFERENCES orders(order_id) ON DELETE SET NULL,
    job_id              uuid REFERENCES manufacturing_jobs(job_id) ON DELETE SET NULL,
    memo                text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (equipment_id, schedule_date)
);
CREATE INDEX IF NOT EXISTS idx_eds_company_date ON equipment_daily_schedule(company_id, schedule_date);
CREATE INDEX IF NOT EXISTS idx_eds_available ON equipment_daily_schedule(company_id, schedule_date, status)
    WHERE status IN ('available','partially_booked');

-- -----------------------------------------------------------------------------
-- 8. Materialized summary view for Phase 1 hard filters
-- -----------------------------------------------------------------------------

CREATE MATERIALIZED VIEW IF NOT EXISTS company_capability_summary AS
WITH mat AS (
    SELECT
        cmc.company_id,
        array_remove(array_agg(DISTINCT upper(m.material_code::text)), NULL) AS material_codes,
        array_remove(array_agg(DISTINCT cmc.material_category_code), NULL) AS explicit_material_category_codes,
        array_remove(array_agg(DISTINCT m.category_code), NULL) AS material_category_codes_from_materials
    FROM company_material_capabilities cmc
    LEFT JOIN materials m ON m.material_id = cmc.material_id
    WHERE cmc.is_active = true
      AND cmc.capability_level IN ('regular','possible','consult')
    GROUP BY cmc.company_id
), expanded_proc AS (
    SELECT cpc.company_id, cpc.process_code
    FROM company_process_capabilities cpc
    WHERE cpc.is_active = true AND cpc.capability_level != 'not_preferred'
    UNION
    SELECT cpc.company_id, pc.parent_process_code
    FROM company_process_capabilities cpc
    JOIN process_catalog pc ON pc.process_code = cpc.process_code
    WHERE cpc.is_active = true AND cpc.capability_level != 'not_preferred'
      AND pc.parent_process_code IS NOT NULL
), expanded_agg AS (
    SELECT company_id, array_agg(DISTINCT process_code) AS process_codes
    FROM expanded_proc
    GROUP BY company_id
), proc AS (
    SELECT
        cpc.company_id,
        array_agg(DISTINCT cpc.process_code) FILTER (WHERE cpc.is_active = true AND cpc.capability_level != 'not_preferred' AND cpc.service_mode IN ('in_house','both')) AS inhouse_process_codes,
        array_agg(DISTINCT cpc.process_code) FILTER (WHERE cpc.is_active = true AND cpc.capability_level != 'not_preferred' AND cpc.service_mode IN ('outsourced','both')) AS outsourced_process_codes,
        min(cpc.best_achievable_it_grade) FILTER (WHERE cpc.best_achievable_it_grade IS NOT NULL AND cpc.capability_level != 'not_preferred') AS best_company_it_grade,
        min(cpc.best_tolerance_mm) FILTER (WHERE cpc.best_tolerance_mm IS NOT NULL AND cpc.capability_level != 'not_preferred') AS best_company_tolerance_mm,
        min(cpc.best_ra_um) FILTER (WHERE cpc.best_ra_um IS NOT NULL AND cpc.capability_level != 'not_preferred') AS best_company_ra_um
    FROM company_process_capabilities cpc
    GROUP BY cpc.company_id
), eq AS (
    SELECT
        e.company_id,
        max(e.max_x_travel_mm) AS max_x_mm,
        max(e.max_y_travel_mm) AS max_y_mm,
        max(e.max_z_travel_mm) AS max_z_mm,
        max(e.max_turning_diameter_mm) AS max_turning_diameter_mm,
        max(e.max_turning_length_mm) AS max_turning_length_mm,
        max(e.max_workpiece_weight_kg) AS max_workpiece_weight_kg,
        min(e.best_achievable_it_grade) FILTER (WHERE e.best_achievable_it_grade IS NOT NULL) AS best_equipment_it_grade,
        min(e.best_tolerance_mm) FILTER (WHERE e.best_tolerance_mm IS NOT NULL) AS best_equipment_tolerance_mm,
        min(e.best_ra_um) FILTER (WHERE e.best_ra_um IS NOT NULL) AS best_equipment_ra_um,
        count(*) FILTER (WHERE e.status IN ('running','idle')) AS active_equipment_count
    FROM equipment e
    WHERE e.status IN ('running','idle')
    GROUP BY e.company_id
), rating AS (
    SELECT
        r.company_id,
        avg(r.rating_overall) AS avg_rating_overall,
        avg(r.rating_quality) AS avg_rating_quality,
        avg(r.rating_delivery) AS avg_rating_delivery,
        count(*) AS review_count
    FROM reviews r
    GROUP BY r.company_id
)
SELECT
    c.company_id,
    c.company_name,
    COALESCE(mat.material_codes, ARRAY[]::text[]) AS material_codes,
    COALESCE(
        (SELECT array_agg(DISTINCT x) FROM unnest(COALESCE(mat.explicit_material_category_codes, ARRAY[]::text[]) || COALESCE(mat.material_category_codes_from_materials, ARRAY[]::text[])) AS x),
        ARRAY[]::text[]
    ) AS material_category_codes,
    COALESCE(ea.process_codes, ARRAY[]::text[]) AS process_codes,
    COALESCE(proc.inhouse_process_codes, ARRAY[]::text[]) AS inhouse_process_codes,
    COALESCE(proc.outsourced_process_codes, ARRAY[]::text[]) AS outsourced_process_codes,
    eq.max_x_mm,
    eq.max_y_mm,
    eq.max_z_mm,
    eq.max_turning_diameter_mm,
    eq.max_turning_length_mm,
    eq.max_workpiece_weight_kg,
    LEAST(COALESCE(proc.best_company_it_grade, 99), COALESCE(eq.best_equipment_it_grade, 99)) AS best_it_grade,
    LEAST(COALESCE(proc.best_company_tolerance_mm, 999999), COALESCE(eq.best_equipment_tolerance_mm, 999999)) AS best_tolerance_mm,
    LEAST(COALESCE(proc.best_company_ra_um, 999999), COALESCE(eq.best_equipment_ra_um, 999999)) AS best_ra_um,
    eq.active_equipment_count,
    av.overall_status,
    av.next_available_date,
    av.available_hours_per_week,
    rating.avg_rating_overall,
    rating.avg_rating_quality,
    rating.avg_rating_delivery,
    COALESCE(rating.review_count, 0) AS review_count
FROM companies c
LEFT JOIN mat ON mat.company_id = c.company_id
LEFT JOIN expanded_agg ea ON ea.company_id = c.company_id
LEFT JOIN proc ON proc.company_id = c.company_id
LEFT JOIN eq ON eq.company_id = c.company_id
LEFT JOIN company_availability_snapshot av ON av.company_id = c.company_id
LEFT JOIN rating ON rating.company_id = c.company_id
WHERE c.status = 'active'
  AND c.onboarding_status = 'verified'
  AND c.accepting_orders = true;

-- -----------------------------------------------------------------------------
-- 9. Recommended indexes
-- -----------------------------------------------------------------------------

-- Company/search indexes
CREATE INDEX IF NOT EXISTS idx_companies_status ON companies(status, onboarding_status);
CREATE INDEX IF NOT EXISTS idx_companies_name_trgm ON companies USING gin (company_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_company_sites_company ON company_sites(company_id);
CREATE INDEX IF NOT EXISTS idx_company_contacts_company ON company_contacts(company_id);

-- Reference/master lookup indexes
CREATE INDEX IF NOT EXISTS idx_materials_category ON materials(category_code);
CREATE INDEX IF NOT EXISTS idx_material_aliases_material ON material_aliases(material_id);

-- Capability indexes
CREATE INDEX IF NOT EXISTS idx_cmc_company ON company_material_capabilities(company_id);
CREATE INDEX IF NOT EXISTS idx_cmc_material ON company_material_capabilities(material_id) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_cmc_category ON company_material_capabilities(material_category_code) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_cpc_company_process ON company_process_capabilities(company_id, process_code) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_cpc_process_it_ra ON company_process_capabilities(process_code, best_achievable_it_grade, best_ra_um) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_equipment_company_status ON equipment(company_id, status);
CREATE INDEX IF NOT EXISTS idx_equipment_category ON equipment(equipment_category_code);
CREATE INDEX IF NOT EXISTS idx_equipment_model_id ON equipment(model_id);
CREATE INDEX IF NOT EXISTS idx_emc_category ON equipment_model_catalog(equipment_category_code);
CREATE INDEX IF NOT EXISTS idx_equipment_envelope_prismatic ON equipment(max_x_travel_mm, max_y_travel_mm, max_z_travel_mm);
CREATE INDEX IF NOT EXISTS idx_equipment_envelope_turning ON equipment(max_turning_diameter_mm, max_turning_length_mm);
CREATE INDEX IF NOT EXISTS idx_epc_process ON equipment_process_capabilities(process_code, best_achievable_it_grade, best_ra_um);

-- Availability indexes
CREATE INDEX IF NOT EXISTS idx_availability_next_date ON company_availability_snapshot(overall_status, next_available_date);
CREATE INDEX IF NOT EXISTS idx_capacity_company_week ON company_capacity_calendar(company_id, week_start_date);
CREATE INDEX IF NOT EXISTS idx_capacity_process_week ON company_capacity_calendar(process_code, week_start_date);

-- RFQ/quote/order/history indexes
CREATE INDEX IF NOT EXISTS idx_drawings_vlm_jsonb ON drawings USING gin (vlm_result_jsonb jsonb_path_ops);
CREATE INDEX IF NOT EXISTS idx_rfq_parts_material ON rfq_parts(material_id, material_category_code);
CREATE INDEX IF NOT EXISTS idx_rfq_parts_requirements ON rfq_parts(tightest_it_grade, finest_ra_um);
CREATE INDEX IF NOT EXISTS idx_rfq_part_processes_process ON rfq_part_processes(process_code);
CREATE INDEX IF NOT EXISTS idx_quotes_company_status ON quote_responses(company_id, status);
CREATE INDEX IF NOT EXISTS idx_orders_company_status ON orders(company_id, status);
CREATE INDEX IF NOT EXISTS idx_jobs_company_completed ON manufacturing_jobs(company_id, completed_at);
CREATE INDEX IF NOT EXISTS idx_jobs_material_requirements ON manufacturing_jobs(material_id, material_category_code, tightest_it_grade, finest_ra_um);
CREATE INDEX IF NOT EXISTS idx_jobs_qdrant ON manufacturing_jobs(qdrant_collection, qdrant_point_id);
CREATE INDEX IF NOT EXISTS idx_job_processes_process ON job_processes(process_code);
CREATE INDEX IF NOT EXISTS idx_reviews_company ON reviews(company_id);
CREATE INDEX IF NOT EXISTS idx_certifications_company ON company_certifications(company_id);
CREATE INDEX IF NOT EXISTS idx_partner_services_process ON company_partner_services(process_code);

-- Materialized-view indexes. Create after first refresh if your PostgreSQL version requires it.
CREATE UNIQUE INDEX IF NOT EXISTS idx_company_capability_summary_pk ON company_capability_summary(company_id);
CREATE INDEX IF NOT EXISTS idx_ccs_material_codes_gin ON company_capability_summary USING gin (material_codes);
CREATE INDEX IF NOT EXISTS idx_ccs_material_categories_gin ON company_capability_summary USING gin (material_category_codes);
CREATE INDEX IF NOT EXISTS idx_ccs_process_codes_gin ON company_capability_summary USING gin (process_codes);
CREATE INDEX IF NOT EXISTS idx_ccs_inhouse_process_codes_gin ON company_capability_summary USING gin (inhouse_process_codes);
CREATE INDEX IF NOT EXISTS idx_ccs_envelope_milling ON company_capability_summary(max_x_mm, max_y_mm, max_z_mm);
CREATE INDEX IF NOT EXISTS idx_ccs_envelope_turning ON company_capability_summary(max_turning_diameter_mm, max_turning_length_mm);
CREATE INDEX IF NOT EXISTS idx_ccs_quality_availability ON company_capability_summary(best_it_grade, best_ra_um, next_available_date, avg_rating_overall);

-- -----------------------------------------------------------------------------
-- 10. Seed examples for canonical process/equipment categories
-- -----------------------------------------------------------------------------

INSERT INTO process_catalog(process_code, parent_process_code, process_name_ko, process_name_en, process_group) VALUES
('turning',              NULL,       '선삭',           'Turning',                    'cutting'),
('milling',              NULL,       '밀링',           'Milling',                    'cutting'),
('drilling',             NULL,       '드릴링',          'Drilling',                   'cutting'),
('boring',               NULL,       '보링',           'Boring',                     'cutting'),
('grinding',             NULL,       '연삭',           'Grinding',                   'cutting'),
('cylindrical_grinding', 'grinding', '원통연삭',         'Cylindrical grinding',       'cutting'),
('surface_grinding',     'grinding', '평면연삭',         'Surface grinding',           'cutting'),
('internal_grinding',    'grinding', '내면연삭',         'Internal grinding',          'cutting'),
('edm_sinker',           NULL,       '형조 방전가공',     'Sinker EDM',                 'special_cutting'),
('edm_wire',             NULL,       '와이어 방전가공',   'Wire EDM',                   'special_cutting'),
('hobbing',              NULL,       '호브절삭',         'Gear hobbing',               'gear_cutting'),
('broaching',            NULL,       '브로칭',          'Broaching',                  'cutting'),
('threading',            NULL,       '나사가공',         'Threading',                  'cutting'),
('keyway',               NULL,       '키홈가공',         'Keyway machining',           'cutting'),
('casting',              NULL,       '주조',           'Casting',                    'forming'),
('heat_treatment',       NULL,       '열처리',          'Heat treatment',             'post_process'),
('welding',              NULL,       '용접',           'Welding',                    'joining'),
('surface_treatment',    NULL,       '표면처리/후처리',   'Surface treatment/finishing', 'post_process'),
('sheet_metal',          NULL,       '판금',           'Sheet metal',                'sheet_metal'),
('laser_cutting',        'sheet_metal', '레이저 절단',   'Laser cutting',              'sheet_metal'),
('bending',              'sheet_metal', '절곡/벤딩',     'Bending',                    'sheet_metal'),
('plasma_cutting',       'sheet_metal', '플라즈마 절단', 'Plasma cutting',             'sheet_metal'),
('waterjet_cutting',     NULL,       '워터젯 절단',     'Waterjet cutting',           'special_cutting'),
('press_forming',        NULL,       '프레스 성형',     'Press forming',              'forming'),
('honing',               'grinding', '호닝',           'Honing',                     'cutting'),
('centerless_grinding',  'grinding', '센터리스 연삭',   'Centerless grinding',        'cutting')
ON CONFLICT (process_code) DO NOTHING;

-- 추가 공정 코드: reaming, lapping, gear_grinding, polishing + rough/finish 하위 공정
INSERT INTO process_catalog(process_code, parent_process_code, process_name_ko, process_name_en, process_group) VALUES
('reaming',              NULL,       '리밍',           'Reaming',                    'cutting'),
('lapping',              'grinding', '래핑',           'Lapping',                    'cutting'),
('gear_grinding',        'grinding', '기어 연삭',       'Gear grinding',              'gear_cutting'),
('polishing',            NULL,       '폴리싱',          'Polishing',                  'post_process'),
('turning_rough',        'turning',  '황삭 선삭',       'Rough turning',              'cutting'),
('turning_finish',       'turning',  '정삭 선삭',       'Finish turning',             'cutting'),
('milling_rough',        'milling',  '황삭 밀링',       'Rough milling',              'cutting'),
('milling_finish',       'milling',  '정삭 밀링',       'Finish milling',             'cutting')
ON CONFLICT (process_code) DO NOTHING;

INSERT INTO equipment_category_catalog(equipment_category_code, category_name_ko, category_name_en, default_process_group) VALUES
('cnc_lathe', 'CNC선반', 'CNC lathe', 'cutting'),
('general_lathe', '범용선반', 'Manual lathe', 'cutting'),
('machining_center_3axis', '3축 머시닝센터', '3-axis machining center', 'cutting'),
('machining_center_5axis', '5축 머시닝센터', '5-axis machining center', 'cutting'),
('surface_grinder', '평면연삭기', 'Surface grinder', 'cutting'),
('cylindrical_grinder', '원통연삭기', 'Cylindrical grinder', 'cutting'),
('internal_grinder', '내면연삭기', 'Internal grinder', 'cutting'),
('edm_sinker', '형조방전기', 'Sinker EDM machine', 'special_cutting'),
('edm_wire', '와이어컷 방전기', 'Wire EDM machine', 'special_cutting'),
('hobbing_machine', '호빙머신', 'Hobbing machine', 'gear_cutting'),
('drilling_machine', '드릴링머신', 'Drilling machine', 'cutting'),
('boring_machine', '보링머신', 'Boring machine', 'cutting'),
('heat_treatment_furnace', '열처리로', 'Heat treatment furnace', 'post_process'),
('welding_equipment', '용접장비', 'Welding equipment', 'joining'),
('laser_cutting_machine', '레이저 절단기', 'Laser cutting machine', 'sheet_metal'),
('press_brake', '절곡기', 'Press brake', 'sheet_metal'),
('mill_turn', '복합가공기', 'Mill-turn center', 'cutting'),
('press_machine', '프레스', 'Press machine', 'forming'),
('plasma_cutting_machine', '플라즈마 절단기', 'Plasma cutting machine', 'sheet_metal'),
('waterjet_cutting_machine', '워터젯 절단기', 'Waterjet cutting machine', 'special_cutting'),
('cnc_router', 'CNC 라우터', 'CNC router', 'cutting'),
('casting_foundry', '주조 설비', 'Casting foundry equipment', 'forming')
ON CONFLICT (equipment_category_code) DO NOTHING;

INSERT INTO material_category_catalog(category_code, category_name_ko, category_name_en) VALUES
('carbon_steel', '탄소강', 'Carbon steel'),
('alloy_steel', '합금강', 'Alloy steel'),
('stainless_steel', '스테인리스강', 'Stainless steel'),
('gray_cast_iron', '회주철', 'Gray cast iron'),
('cast_steel', '주강', 'Cast steel'),
('aluminum_alloy', '알루미늄 합금', 'Aluminum alloy'),
('copper_alloy', '구리합금', 'Copper alloy'),
('sheet_steel', '판금용 강판', 'Sheet steel'),
('tool_steel', '공구강', 'Tool steel'),
('engineering_plastic', '엔지니어링 플라스틱', 'Engineering Plastic'),
('free_cutting_steel', '쾌삭강', 'Free-Cutting Steel'),
('ductile_cast_iron', '구상흑연주철', 'Ductile Cast Iron'),
('composite_insulation', '복합재/절연재', 'Composite/Insulation'),
('other', '기타', 'Other'),
('stainless_cast_steel', '스테인리스 주강', 'Stainless cast steel')
ON CONFLICT (category_code) DO NOTHING;

-- ── company_material_process_capabilities: 재질-공정-업체 결합 테이블 ──
CREATE TABLE IF NOT EXISTS company_material_process_capabilities (
    company_material_process_capability_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id              uuid NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    material_id             uuid REFERENCES materials(material_id) ON DELETE CASCADE,
    material_category_code  text REFERENCES material_category_catalog(category_code),
    process_code            text NOT NULL REFERENCES process_catalog(process_code),
    service_mode            text NOT NULL DEFAULT 'in_house'
                            CHECK (service_mode IN ('in_house','outsourced','both')),
    capability_level        text NOT NULL DEFAULT 'regular'
                            CHECK (capability_level IN ('primary','regular','secondary','possible','consult','not_preferred')),
    equipment_id            uuid REFERENCES equipment(equipment_id) ON DELETE SET NULL,
    equipment_category_code text REFERENCES equipment_category_catalog(equipment_category_code),
    typical_it_grade        smallint CHECK (typical_it_grade IS NULL OR typical_it_grade BETWEEN 1 AND 18),
    best_achievable_it_grade smallint CHECK (best_achievable_it_grade IS NULL OR best_achievable_it_grade BETWEEN 1 AND 18),
    typical_tolerance_mm    numeric(10,5) CHECK (typical_tolerance_mm IS NULL OR typical_tolerance_mm >= 0),
    best_tolerance_mm       numeric(10,5) CHECK (best_tolerance_mm IS NULL OR best_tolerance_mm >= 0),
    typical_ra_um           numeric(8,3) CHECK (typical_ra_um IS NULL OR typical_ra_um >= 0),
    best_ra_um              numeric(8,3) CHECK (best_ra_um IS NULL OR best_ra_um >= 0),
    max_x_mm                numeric(12,3) CHECK (max_x_mm IS NULL OR max_x_mm >= 0),
    max_y_mm                numeric(12,3) CHECK (max_y_mm IS NULL OR max_y_mm >= 0),
    max_z_mm                numeric(12,3) CHECK (max_z_mm IS NULL OR max_z_mm >= 0),
    max_turning_diameter_mm numeric(12,3) CHECK (max_turning_diameter_mm IS NULL OR max_turning_diameter_mm >= 0),
    max_turning_length_mm   numeric(12,3) CHECK (max_turning_length_mm IS NULL OR max_turning_length_mm >= 0),
    max_sheet_thickness_mm  numeric(12,3) CHECK (max_sheet_thickness_mm IS NULL OR max_sheet_thickness_mm >= 0),
    lot_size_min            integer CHECK (lot_size_min IS NULL OR lot_size_min >= 0),
    lot_size_max            integer CHECK (lot_size_max IS NULL OR lot_size_max >= 0),
    special_requirements    jsonb NOT NULL DEFAULT '[]'::jsonb,
    safety_controls         jsonb NOT NULL DEFAULT '[]'::jsonb,
    notes                   text,
    capability_source       text NOT NULL DEFAULT 'self_reported'
                            CHECK (capability_source IN ('self_reported','manual_verified','document_verified','job_history_verified','inspection_verified','engineering_reference_estimate','spec_based_estimate','manufacturer_catalog','unknown')),
    verification_level      text NOT NULL DEFAULT 'self_reported'
                            CHECK (verification_level IN ('self_reported','document_verified','job_history_verified','inspection_verified')),
    is_active               boolean NOT NULL DEFAULT true,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cmpc_material_scope_chk CHECK (
        (material_id IS NOT NULL AND material_category_code IS NULL)
        OR (material_id IS NULL AND material_category_code IS NOT NULL)
    ),
    CONSTRAINT cmpc_equipment_scope_chk CHECK (
        NOT (equipment_id IS NOT NULL AND equipment_category_code IS NOT NULL)
    ),
    CONSTRAINT cmpc_lot_range_chk CHECK (lot_size_min IS NULL OR lot_size_max IS NULL OR lot_size_min <= lot_size_max)
);

CREATE INDEX IF NOT EXISTS idx_cmpc_company_process ON company_material_process_capabilities (company_id, process_code) WHERE is_active;
CREATE INDEX IF NOT EXISTS idx_cmpc_material_process ON company_material_process_capabilities (material_id, process_code) WHERE is_active AND material_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cmpc_category_process ON company_material_process_capabilities (material_category_code, process_code) WHERE is_active AND material_category_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cmpc_equipment_category ON company_material_process_capabilities (equipment_category_code, process_code) WHERE is_active AND equipment_category_code IS NOT NULL;

-- Refresh instruction:
-- REFRESH MATERIALIZED VIEW CONCURRENTLY imma.company_capability_summary;
