"""REST routers (API_CONTRACT §4), mounted under /api. Handlers are async so
all state changes happen on the event loop."""
from fastapi import APIRouter

from . import fleet, incidents, missions, system

api_router = APIRouter(prefix="/api")
for _module in (system, fleet, incidents, missions):
    api_router.include_router(_module.router)
