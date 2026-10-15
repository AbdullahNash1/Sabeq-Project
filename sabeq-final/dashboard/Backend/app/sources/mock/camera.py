"""Mock camera: generated 4:3 JPEG frames (1280x960) + fake person detections.

Frames show the clock, the mission state and the camera mode; boxes are NOT
drawn in (the frontend draws them from camera.detections, as with the real feed).
Frames are rendered only while a stream client is connected, off the event loop.
"""
import asyncio
from io import BytesIO
import math
import random
from datetime import datetime
from typing import AsyncIterator

from PIL import Image, ImageDraw, ImageFont

from app.models import Box, ImageSize
from app.sources.base import CameraSource
from app.sources.mock.sim import SCAN_YAW_TOL_DEG, Ticker
from app.state.machine import MissionState as S

WIDTH, HEIGHT = 1280, 960
DETECTIONS_INTERVAL_S = 0.2  # sim seconds: 5 Hz at MOCK_SPEED=1 (the service caps the WS rate)
SCAN_BOXES = 40  # box positions available; the route being scanned shows its live count
JPEG_QUALITY = 70


def render_frame(lines: list, heading: float, mode: str, now: datetime) -> bytes:
    """One JPEG frame. Pure: safe to run in a worker thread."""
    img = Image.new("RGB", (WIDTH, HEIGHT), (24, 32, 40))
    draw = ImageDraw.Draw(img)
    horizon = int(HEIGHT * 0.42)
    draw.rectangle([0, horizon, WIDTH, HEIGHT], fill=(44, 52, 44))
    # Perspective ground grid that slides with the heading, so motion is visible.
    vanish = (WIDTH // 2, horizon)
    shift = (heading % 30) / 30 * (WIDTH / 8)
    for i in range(-8, 17):
        x = i * WIDTH / 8 + shift
        draw.line([vanish, (x * 3 - WIDTH, HEIGHT)], fill=(64, 74, 64), width=2)
    for k in range(1, 9):
        y = horizon + (HEIGHT - horizon) * (k / 8) ** 2
        draw.line([(0, y), (WIDTH, y)], fill=(64, 74, 64), width=2)
    draw.line([(0, horizon), (WIDTH, horizon)], fill=(120, 140, 150), width=3)

    big = ImageFont.load_default(size=44)
    small = ImageFont.load_default(size=30)
    # Caption band below the top ~12 %, where the dashboard overlays its own badges
    band = 130
    draw.rectangle([0, band, WIDTH, band + 120], fill=(0, 0, 0))
    draw.text((32, band + 18), "SBQ-01 · MOCK CAMERA", font=big, fill=(240, 240, 240))
    draw.text((32, band + 72), " · ".join(lines), font=small, fill=(180, 200, 210))
    draw.text((WIDTH - 260, band + 18), now.strftime("%H:%M:%S"), font=big, fill=(240, 240, 240))
    draw.text((WIDTH - 260, band + 72), f"{mode} · {heading:5.1f}°", font=small, fill=(180, 200, 210))
    # Crosshair
    cx, cy = WIDTH // 2, HEIGHT // 2
    draw.line([(cx - 30, cy), (cx + 30, cy)], fill=(255, 255, 255), width=2)
    draw.line([(cx, cy - 30), (cx, cy + 30)], fill=(255, 255, 255), width=2)

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


class MockCameraSource(CameraSource):
    def __init__(self, service, store, settings):
        super().__init__(service, store, settings)
        self._ticker = Ticker(settings, "camera")
        self.reset()

    def reset(self) -> None:
        self._announce()
        self._since = 0.0
        self._sim_t = 0.0
        self._rng = random.Random(0)
        self._scan_boxes: list = []
        self._mission_id = None

    async def start(self) -> None:
        self._announce()
        self._ticker.start(self._step)

    def _announce(self) -> None:
        """The camera is up; called at start and after a dev reset rebuilt the store."""
        self.service.camera_status(available=True, resolution=ImageSize(w=WIDTH, h=HEIGHT))

    async def stop(self) -> None:
        await self._ticker.stop()

    # --- MJPEG frames -----------------------------------------------------------

    async def frames(self) -> AsyncIterator[bytes]:
        interval = 1.0 / self.settings.mjpeg_max_fps
        loop = asyncio.get_running_loop()
        while True:
            started = loop.time()
            mission = self.store.active_mission
            lines = [mission.state.value if mission else "idle"]
            if mission:
                lines.insert(0, mission.id)
            drone = self.store.drone
            lines.append(f"ALT {drone.altitude_m:.0f} m")
            yield await asyncio.to_thread(
                render_frame, lines, drone.heading_deg, self.store.camera.mode, datetime.now()
            )
            await asyncio.sleep(max(0.0, interval - (loop.time() - started)))

    # --- detections ---------------------------------------------------------------

    def _step(self, dt: float) -> None:
        self._sim_t += dt
        self._since += dt
        if self._since < DETECTIONS_INTERVAL_S:
            return
        self._since = 0.0
        mission = self.store.active_mission
        if mission is None:
            return
        if mission.id != self._mission_id:
            self._mission_id = mission.id
            rng = random.Random(mission.id)
            self._scan_boxes = [(rng.uniform(2, 95), rng.uniform(45, 92)) for _ in range(SCAN_BOXES)]
        if mission.state is S.SCANNING and mission.scan.phase in ("rotate", "done"):
            boxes = self._scan(mission)
        elif mission.state in (S.ESCORTING, S.RETURNING, S.ARRIVED):
            boxes = self._forward()
        else:
            return
        self.service.camera_detections(boxes, ImageSize(w=WIDTH, h=HEIGHT), sim_time_s=round(self._sim_t, 3))

    def _scan(self, mission) -> list:
        """As many boxes as the live count of the route the drone faces (none while turning)."""
        facing = self.service.world.route_facing(self.store.drone.heading_deg, SCAN_YAW_TOL_DEG)
        count = next((r.people_count for r in mission.routes if r.id == facing), 0)
        visible = self._scan_boxes[: min(count, len(self._scan_boxes))]
        return [self._box(x, y, i) for i, (x, y) in enumerate(visible)]

    def _forward(self) -> list:
        n = 3 + int(1.5 + 1.5 * math.sin(self._sim_t / 7))
        boxes = []
        for i in range(n):
            x = (12 + i * 19 + 6 * math.sin(self._sim_t / 3 + i)) % 92
            y = 50 + (self._sim_t * 4 + i * 11) % 38  # drift down as the drone moves forward
            boxes.append(self._box(x, y, i))
        return boxes

    def _box(self, x: float, y: float, i: int) -> Box:
        depth = (y - 40) / 55  # nearer (lower in frame) = bigger
        w, h = 1.2 + 2.5 * depth, 4.0 + 8.0 * depth
        return Box(
            x=round(min(x, 100 - w), 2), y=round(min(y, 100 - h), 2), w=round(w, 2), h=round(h, 2),
            conf=round(0.62 + ((i * 37) % 36) / 100, 3), cls="person",
        )
