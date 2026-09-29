"""
DOCINT — API Router
Assembles all sub-routers under /api/v1 prefix.
"""
from fastapi import APIRouter

from app.api.routes import dashboard, departments, documents, extract, health, processing, templates

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(health.router)
api_router.include_router(departments.router)
api_router.include_router(templates.router)
api_router.include_router(documents.router)
api_router.include_router(processing.router)
api_router.include_router(dashboard.router)
api_router.include_router(extract.router)  # Must be last — uses wildcard paths
