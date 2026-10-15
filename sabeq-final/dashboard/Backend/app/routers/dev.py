"""Dev-only endpoints (API_CONTRACT §4.5). Mounted only when DEV_MODE=true."""
from fastapi import APIRouter, Request

from app.models import Snapshot

router = APIRouter(prefix="/api/dev", tags=["dev"])


@router.post("/sim/reset", response_model=Snapshot)
async def reset_sim(request: Request):
    """Back to boot state: no mission, empty queue and history, sources reset.
    Every WS client receives a fresh `snapshot`."""
    state = request.app.state
    state.service.reset()
    state.sources.reset()
    snapshot = state.store.snapshot(state.service.now())
    state.broadcaster.publish("snapshot", snapshot)
    return snapshot
