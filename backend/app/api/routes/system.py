"""
DOCINT - System profile route
GET /api/v1/system/profile  - what hardware was detected and which settings were derived (no secrets).
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.schemas.common import APIResponse

router = APIRouter(tags=["System"], prefix="/system")


@router.get("/profile", response_model=APIResponse[dict])
def system_profile(refresh: bool = Query(False, description="Re-detect hardware instead of using the cached profile")):
    from app.system.runtime import get_runtime
    hw, rp = get_runtime(refresh=refresh)
    hwd = hw.to_dict()
    hwd["gpus"] = [{k: v for k, v in g.items() if k != "uuid"} for g in hwd["gpus"]]  # GPU UUIDs are identifiers
    return APIResponse(data={"hardware": hwd, "runtime": rp.to_dict()})
