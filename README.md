POST /api/match-v2 테스트 입력 예시:
tests/mock_data/sample_vlm_result.json



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
