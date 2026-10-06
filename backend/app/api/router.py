"""
DOCINT — API Router
Assembles all sub-routers under /api/v1 prefix.
"""
from fastapi import APIRouter

from app.api.routes import dashboard, department_urls, departments, documents, events, evidence, extract, health, pipelines, processing, templates

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(health.router)
api_router.include_router(departments.router)
api_router.include_router(templates.router)
api_router.include_router(documents.router)
api_router.include_router(processing.router)
api_router.include_router(dashboard.router)
api_router.include_router(pipelines.router)  # Phase 13: /pipelines listing for the OCR/Orchestration pages
api_router.include_router(evidence.router)  # Phase 16: field evidence (crop/page image) API
api_router.include_router(events.router)  # Phase 14: processing-events REST feed (job timeline + recent activity)
api_router.include_router(department_urls.router)  # Generate URL page: shareable department/template pre-filled links
api_router.include_router(extract.router)  # Must be last — uses wildcard paths
