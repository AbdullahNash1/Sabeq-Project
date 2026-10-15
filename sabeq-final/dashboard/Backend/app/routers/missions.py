"""§4.4 missions. /active and /history are declared before /{mission_id}."""
from fastapi import APIRouter, Depends

from app.models import (
    AbortRequest,
    CloseRequest,
    EmptyRequest,
    Mission,
    MissionSummary,
    PayloadRequest,
    PayloadState,
    SelectRouteRequest,
)
from app.service import MissionService

from .deps import get_service

router = APIRouter(prefix="/missions", tags=["missions"])


@router.get("/active", response_model=Mission | None)
async def get_active_mission(svc: MissionService = Depends(get_service)):
    return svc.store.active_mission  # 200 with null when idle


@router.get("/history", response_model=list[MissionSummary])
async def get_history(svc: MissionService = Depends(get_service)):
    return svc.store.recent_missions()


@router.get("/{mission_id}", response_model=Mission)
async def get_mission(mission_id: str, svc: MissionService = Depends(get_service)):
    return svc.get_mission(mission_id)


@router.post("/{mission_id}/launch", response_model=Mission)
async def launch(mission_id: str, body: EmptyRequest | None = None,
                 svc: MissionService = Depends(get_service)):
    return await svc.launch(mission_id)


@router.post("/{mission_id}/dismiss", response_model=Mission)
async def dismiss(mission_id: str, body: EmptyRequest | None = None,
                  svc: MissionService = Depends(get_service)):
    return svc.dismiss(mission_id)


@router.post("/{mission_id}/route", response_model=Mission)
async def select_route(mission_id: str, body: SelectRouteRequest,
                       svc: MissionService = Depends(get_service)):
    return await svc.select_route(mission_id, body.route)


@router.post("/{mission_id}/payload", response_model=PayloadState)
async def set_payload(mission_id: str, body: PayloadRequest,
                      svc: MissionService = Depends(get_service)):
    return svc.set_payload(mission_id, body)


@router.post("/{mission_id}/abort", response_model=Mission)
async def abort(mission_id: str, body: AbortRequest, svc: MissionService = Depends(get_service)):
    return await svc.abort(mission_id, body.reason)


@router.post("/{mission_id}/return", response_model=Mission)
async def start_return(mission_id: str, body: EmptyRequest | None = None,
                       svc: MissionService = Depends(get_service)):
    return await svc.start_return(mission_id)


@router.post("/{mission_id}/close", response_model=Mission)
async def close(mission_id: str, body: CloseRequest | None = None,
                svc: MissionService = Depends(get_service)):
    return svc.close(mission_id, body.outcome if body else None)
