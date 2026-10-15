"""§4.3 incidents."""
from typing import get_args

from fastapi import APIRouter, Depends, Query

from app.errors import ApiError
from app.models import CreateIncident, Incident
from app.models.common import IncidentStatus
from app.service import MissionService

from .deps import get_service

router = APIRouter(tags=["incidents"])

_STATUSES = set(get_args(IncidentStatus))


@router.get("/incidents", response_model=list[Incident])
async def list_incidents(
    status: str | None = Query(None, description="Comma-separated, e.g. active,queued"),
    svc: MissionService = Depends(get_service),
):
    incidents = svc.store.all_incidents()
    if status is None:
        return incidents
    wanted = {s.strip() for s in status.split(",") if s.strip()}
    unknown = wanted - _STATUSES
    if unknown:
        raise ApiError(400, "bad_request", f"Unknown status: {', '.join(sorted(unknown))}",
                       {"allowed": sorted(_STATUSES)})
    return [i for i in incidents if i.status in wanted]


@router.get("/incidents/{incident_id}", response_model=Incident)
async def get_incident(incident_id: str, svc: MissionService = Depends(get_service)):
    return svc.get_incident(incident_id)


@router.post("/incidents", response_model=Incident, status_code=201)
async def create_incident(body: CreateIncident, svc: MissionService = Depends(get_service)):
    return svc.create_incident(body)
