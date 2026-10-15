"""§4.2 drones, camera, carts — exactly one of each in v1."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.errors import ApiError
from app.models import CameraInfo, Cart, Drone
from app.state.store import Store

from .deps import get_store

router = APIRouter(tags=["fleet"])


def _drone(store: Store, drone_id: str) -> Drone:
    if drone_id != store.drone.id:
        raise ApiError(404, "not_found", f"Drone '{drone_id}' not found")
    return store.drone


@router.get("/drones", response_model=list[Drone])
async def list_drones(store: Store = Depends(get_store)):
    return [store.drone]


@router.get("/drones/{drone_id}", response_model=Drone)
async def get_drone(drone_id: str, store: Store = Depends(get_store)):
    return _drone(store, drone_id)


@router.get("/drones/{drone_id}/camera", response_model=CameraInfo)
async def get_camera(drone_id: str, store: Store = Depends(get_store)):
    _drone(store, drone_id)
    return store.camera


BOUNDARY = "frame"


async def mjpeg(frames):
    """multipart/x-mixed-replace body from an async iterator of JPEG frames."""
    async for jpeg in frames:
        yield (
            f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n".encode()
            + jpeg
            + b"\r\n"
        )


@router.get("/drones/{drone_id}/camera/stream")
async def get_camera_stream(drone_id: str, request: Request, store: Store = Depends(get_store)):
    _drone(store, drone_id)
    if not store.camera.available:
        raise ApiError(503, "ros_unavailable", "Camera stream unavailable: no camera source")
    return StreamingResponse(
        mjpeg(request.app.state.sources.camera.frames()),
        media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/carts", response_model=list[Cart])
async def list_carts(store: Store = Depends(get_store)):
    return [store.cart]


@router.get("/carts/{cart_id}", response_model=Cart)
async def get_cart(cart_id: str, store: Store = Depends(get_store)):
    if cart_id != store.cart.id:
        raise ApiError(404, "not_found", f"Cart '{cart_id}' not found")
    return store.cart
