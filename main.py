import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from routers import (
    auth, signup, companies, rfqs, matching, orders,
    drawings, quotes, reviews, notifications, admin, catalog, vlm, config
)

app = FastAPI()

# CORS: JWT는 Authorization 헤더 사용이므로 credentials 불필요. 명시 도메인 화이트리스트.
_DEFAULT_ALLOWED_ORIGINS = "http://localhost:8000,http://127.0.0.1:8000"
_ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("ALLOWED_ORIGINS", _DEFAULT_ALLOWED_ORIGINS).split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# Frontend UI
# =========================
BASE_DIR = Path(__file__).resolve().parent
UI_DIR = BASE_DIR / "machhub_ui"

if UI_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")


def serve_ui_file(filename: str):
    file_path = UI_DIR / filename
    if file_path.exists():
        return FileResponse(file_path)
    return {
        "message": "IMMA API server is running",
        "ui_error": f"machhub_ui/{filename} not found",
    }


@app.get("/", include_in_schema=False)
def serve_landing():
    return serve_ui_file("landing.html")


@app.get("/client-register", include_in_schema=False)
def serve_client_register():
    return serve_ui_file("client-register.html")


@app.get("/client", include_in_schema=False)
def serve_client():
    return serve_ui_file("client-dashboard.html")


@app.get("/supplier", include_in_schema=False)
def serve_supplier():
    return serve_ui_file("supplier-dashboard.html")


@app.get("/how-to-use", include_in_schema=False)
def serve_how_to_use():
    return serve_ui_file("how-to-use.html")


@app.get("/process-flow", include_in_schema=False)
def serve_process_flow():
    return serve_ui_file("process-flow.html")


@app.get("/search-suppliers", include_in_schema=False)
def serve_search_suppliers():
    return serve_ui_file("search-suppliers.html")


@app.get("/support", include_in_schema=False)
def serve_support():
    return serve_ui_file("support.html")


@app.get("/supplier-messages", include_in_schema=False)
def serve_supplier_messages():
    return serve_ui_file("supplier-messages.html")


@app.get("/supplier-register", include_in_schema=False)
def serve_supplier_register():
    return serve_ui_file("supplier-register.html")


@app.get("/supplier-rfq-detail", include_in_schema=False)
def serve_supplier_rfq_detail():
    return serve_ui_file("supplier-rfq-detail.html")


@app.get("/admin-ui", include_in_schema=False)
def serve_admin_ui():
    return serve_ui_file("admin-dashboard.html")


@app.get("/matching-ui", include_in_schema=False)
def serve_matching_ui():
    return serve_ui_file("matching.html")


@app.get("/quote-request", include_in_schema=False)
def serve_quote_request():
    return serve_ui_file("quote-request.html")


@app.get("/order-management", include_in_schema=False)
def serve_order_management():
    return serve_ui_file("order-management.html")


@app.get("/client-fulfillment", include_in_schema=False)
def serve_client_fulfillment():
    return serve_ui_file("client-fulfillment.html")


@app.get("/supplier-workbench", include_in_schema=False)
def serve_supplier_workbench():
    return serve_ui_file("supplier-workbench.html")


@app.get("/supplier-settings", include_in_schema=False)
def serve_supplier_settings():
    return serve_ui_file("supplier-settings.html")


@app.get("/admin-control-center", include_in_schema=False)
def serve_admin_control_center():
    return serve_ui_file("admin-control-center.html")


@app.get("/admin-operations", include_in_schema=False)
def serve_admin_operations():
    return serve_ui_file("admin-operations.html")


@app.get("/payment-success", include_in_schema=False)
def serve_payment_success():
    return serve_ui_file("payment-success.html")


# =========================
# API Routers
# =========================
app.include_router(signup.router)
app.include_router(auth.router)
app.include_router(matching.router)
app.include_router(companies.router)
app.include_router(rfqs.router)
app.include_router(orders.router)
app.include_router(drawings.router)
app.include_router(quotes.router)
app.include_router(reviews.router)
app.include_router(notifications.router)
app.include_router(admin.router)
app.include_router(catalog.router)
app.include_router(vlm.router)
app.include_router(config.router)
