# IMMA FastAPI Server

## 2026-05-02 기준 운영 통합 상태

현재 서버는 기존 v1 매칭 API와 신규 v2 RAG/DB 매칭 API를 함께 유지합니다.

### 완료된 항목

- Railway PostgreSQL 연결 성공
- IMMA 스키마/seed 구성 완료
- lookup_data.json / equipment_catalog.json 로드
- RAG pipeline CLI 실행 성공
- RAG pipeline FastAPI 실행 성공
- GitHub 운영 repo에 pipeline/lookup_tables 추가
- 기존 main.py 유지
- 신규 POST /api/match-v2 추가
- Railway 운영 서버 배포 성공
- Swagger에서 /api/match-v2 200 응답 확인
- equipment_verified true/false 반환 확인

### API 구분

#### v1 매칭

GET /match/{rfq_id}

기존 DB의 RFQ 데이터를 기반으로 material/process 조건에 맞는 supplier를 조회하는 간단 매칭 API입니다.

#### v2 매칭

POST /api/match-v2

VLM이 도면에서 추출한 JSON을 입력으로 받아 RAG/DB 기반 제조사 매칭 파이프라인을 실행합니다.

테스트 입력 예시:
sample_vlm_result.json

정상 응답 예시:
sample_match_v2_response.json







## 향후 확장용 ERD 초안

주의: 아래 ERD는 현재 운영 DB의 실제 테이블과 완전히 동일한 구현본이 아니라,
IMMA 서비스 확장을 위한 목표 구조 초안입니다.

# fas
erDiagram

    users {
        int user_id PK
        string name
        string email
        string phone
        string signup_channel
        string role
    }

    companies {
        int company_id PK
        string company_name
        string company_type
        string region
        string main_products
        string verified_status
    }

    company_members {
        int company_member_id PK
        int company_id FK
        int user_id FK
        string position_name
        bool is_owner
    }

    supplier_profiles {
        int supplier_profile_id PK
        int company_id FK
        int min_order_qty
        int max_order_qty
        int lead_time_min_days
        int lead_time_max_days
        string precision_level
        string capacity_status
    }

    supplier_company_machines {
        int id PK
        int company_id FK
        int machine_id FK
    }

    supplier_company_processes {
        int id PK
        int company_id FK
        int process_id FK
    }

    supplier_company_materials {
        int id PK
        int company_id FK
        int material_id FK
    }

    machines {
        int machine_id PK
        string machine_name
        string machine_type
    }

    processes {
        int process_id PK
        string process_name
        string process_group
    }

    materials {
        int material_id PK
        string material_name
        string material_group
    }

    quote_requests {
        int request_id PK
        int requester_user_id FK
        int requester_company_id FK
        string project_name
        string manufacturing_type
        int material_id FK
        int quantity
        date delivery_due_date
        string product_usage
        string detail_request_text
        string status
    }

    quote_request_required_processes {
        int required_process_id PK
        int request_id FK
        int process_id FK
        string source_type
        float confidence_score
    }

    quote_request_metadata {
        int metadata_id PK
        int request_id FK
        json raw_extracted_json
        json normalized_json
        string extraction_model
    }

    quote_request_validation_results {
        int validation_id PK
        int request_id FK
        string check_type
        string severity
        string message
        bool is_resolved
    }

    supplier_matches {
        int match_id PK
        int request_id FK
        int supplier_company_id FK
        float match_score
        float machine_score
        float process_score
        float material_score
        float delivery_score
        int rank_order
        string match_reason
    }

    quotations {
        int quotation_id PK
        int request_id FK
        int supplier_company_id FK
        int quoted_price
        string currency
        int lead_time_days
        string quotation_text
        string status
    }

    orders {
        int order_id PK
        int request_id FK
        int quotation_id FK
        int requester_company_id FK
        int supplier_company_id FK
        string order_status
        string payment_method
    }

    reviews {
        int review_id PK
        int order_id FK
        int reviewer_user_id FK
        int supplier_company_id FK
        int rating
        string review_text
    }

    %% 관계 정의
    users ||--o{ company_members : "소속"
    companies ||--o{ company_members : "구성"
    companies ||--o| supplier_profiles : "공급자 프로필"
    companies ||--o{ supplier_company_machines : "보유장비"
    companies ||--o{ supplier_company_processes : "가공공정"
    companies ||--o{ supplier_company_materials : "취급소재"
    machines ||--o{ supplier_company_machines : ""
    processes ||--o{ supplier_company_processes : ""
    materials ||--o{ supplier_company_materials : ""

    users ||--o{ quote_requests : "요청자"
    companies ||--o{ quote_requests : "요청사"
    materials ||--o{ quote_requests : "소재"
    quote_requests ||--o{ quote_request_required_processes : "필요공정"
    quote_requests ||--o| quote_request_metadata : "VLM추출"
    quote_requests ||--o{ quote_request_validation_results : "검증결과"
    quote_requests ||--o{ supplier_matches : "매칭결과"
    quote_requests ||--o{ quotations : "견적"
    processes ||--o{ quote_request_required_processes : ""

    quotations ||--o| orders : "발주"
    orders ||--o| reviews : "리뷰"
    users ||--o{ reviews : "작성자"
    companies ||--o{ reviews : "대상업체"
    companies ||--o{ supplier_matches : "매칭업체"
    companies ||--o{ quotations : "견적업체"
