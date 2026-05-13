"""운영 설정 상태 점검. 민감정보 값은 반환하지 않는다."""

import os

from fastapi import APIRouter, Depends

from routers.deps import get_current_admin, JWT_SECRET

router = APIRouter()


@router.get("/api/config/health")
def config_health(admin: dict = Depends(get_current_admin)):
    return {
        "database_url_set": bool(os.getenv("DATABASE_URL")),
        "jwt_secret_set": bool(os.getenv("JWT_SECRET")),
        "jwt_secret_is_default": JWT_SECRET == "imma-dev-secret",
        "replicate_token_set": bool(os.getenv("REPLICATE_API_TOKEN")),
        "replicate_model_version_set": bool(os.getenv("REPLICATE_MODEL_VERSION")),
        "gemini_key_set": bool(os.getenv("GEMINI_API_KEY")),
        "neo4j_uri_set": bool(os.getenv("NEO4J_URI") or os.getenv("NEO4J_URL")),
    }
