"""FastAPI app factory: REST under /api (§4), WS /ws (§5), data sources per part."""
import asyncio
import contextlib
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.broadcaster import Broadcaster
from app.config import Settings, get_settings
from app.errors import install_error_handlers
from app.geo import Projection
from app.routers import api_router
from app.routers.dev import router as dev_router
from app.service import MissionService
from app.sources.bus import CommandBus
from app.sources.factory import build_sources
from app.sources.recording import RecordingSink
from app.state.store import create_default_store
from app.world import load_world
from app.ws.endpoint import router as ws_router


async def _system_status_loop(app: FastAPI) -> None:
    """§5.2: system.status every 2 s."""
    while True:
        await asyncio.sleep(app.state.settings.system_status_interval_s)
        app.state.broadcaster.publish("system.status", app.state.service.system_status())


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.broadcaster.bind_loop(asyncio.get_running_loop())
    await app.state.sources.start()
    task = asyncio.create_task(_system_status_loop(app))
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await app.state.sources.stop()


def create_app(
    settings: "Settings | None" = None,
    sources_factory: "Callable | None" = None,
    record_commands: bool = False,
) -> FastAPI:
    """sources_factory(settings, service, store) -> SourceSet; defaults to SOURCE_* config.
    record_commands adds a RecordingSink (app.state.recorder) to the command bus.

    Raises app.world.WorldConfigError when WORLD_META and ROUTES_YAML disagree."""
    settings = settings or get_settings()
    world = load_world(settings)  # fail fast: route names/waypoints must match (docs/WORLD.md)
    app = FastAPI(title="Sabeq Backend", version="1.4.0", lifespan=lifespan)

    store = create_default_store(settings, world)
    broadcaster = Broadcaster(queue_size=settings.ws_queue_size)
    bus = CommandBus()
    service = MissionService(store, settings, broadcaster, bus, Projection(settings), world)
    sources = (sources_factory or build_sources)(settings, service, store)
    for source in sources.all():
        bus.add(source)
    recorder = RecordingSink() if record_commands else None
    if recorder:
        bus.add(recorder)

    app.state.settings = settings
    app.state.world = world
    app.state.store = store
    app.state.broadcaster = broadcaster
    app.state.service = service
    app.state.sources = sources
    app.state.recorder = recorder

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)
    app.include_router(api_router)
    if settings.dev_mode:
        app.include_router(dev_router)
    app.include_router(ws_router)
    return app


app = create_app()
