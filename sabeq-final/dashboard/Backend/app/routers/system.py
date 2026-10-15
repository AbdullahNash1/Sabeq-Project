"""§4.1 bootstrap and §4.2 site/system."""
from fastapi import APIRouter, Depends

from app.models import Site, Snapshot, SystemStatus
from app.service import MissionService

from .deps import get_service

router = APIRouter(tags=["system"])


@router.get("/snapshot", response_model=Snapshot)
async def get_snapshot(svc: MissionService = Depends(get_service)):
    svc.system_status()  # refresh uptime
    return svc.store.snapshot(svc.now())


@router.get("/site", response_model=Site)
async def get_site(svc: MissionService = Depends(get_service)):
    return svc.store.site


@router.get("/system/status", response_model=SystemStatus)
async def get_system_status(svc: MissionService = Depends(get_service)):
    return svc.system_status()
