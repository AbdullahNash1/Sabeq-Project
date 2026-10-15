from fastapi import Request

from app.service import MissionService
from app.state.store import Store


def get_service(request: Request) -> MissionService:
    return request.app.state.service


def get_store(request: Request) -> Store:
    return request.app.state.store
