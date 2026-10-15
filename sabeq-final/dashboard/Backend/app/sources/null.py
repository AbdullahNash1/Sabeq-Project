"""Null sources: accept commands and do nothing. Used by API tests that drive
the mission by calling the service hooks themselves."""
from typing import AsyncIterator

from app.sources.base import CameraSource, CartSource, DroneSource, RouteSource, SourceSet


class NullCameraSource(CameraSource):
    async def frames(self) -> AsyncIterator[bytes]:
        return
        yield  # pragma: no cover  (makes this an async generator)


class NullRouteSource(RouteSource):
    pass


class NullDroneSource(DroneSource):
    pass


class NullCartSource(CartSource):
    pass


def null_sources(settings, service, store) -> SourceSet:
    args = (service, store, settings)
    return SourceSet(NullCameraSource(*args), NullRouteSource(*args), NullDroneSource(*args), NullCartSource(*args))
