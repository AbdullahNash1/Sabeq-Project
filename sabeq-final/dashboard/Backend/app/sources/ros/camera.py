"""SOURCE_CAMERA=ros: /camera/image -> MJPEG, /person_detections -> camera.detections.

- The latest /camera/image (rgb8 1280x960 from ros_gz_bridge, ros_samples.md §3) is kept; the
  MJPEG stream encodes it with Pillow in a worker thread, at most MJPEG_MAX_FPS, only while a
  client watches, and once per frame: every client shares the same JPEG.
- CAMERA_TRANSPORT=compressed/auto: JPEG frames from image_transport republish
  (/camera/image/compressed) are streamed as they are: no 3.7 MB raw frame ever reaches Python.
  auto subscribes to the raw topic only if no JPEG arrives within RAW_FALLBACK_S.
- /person_detections -> boxes (app/ros/convert.py), throttled to <= 10 Hz by the service.
- The detection rate is measured over the last 5 s for the Jetson FPS chip.
- While the drone part is not ROS, a fresh camera frame is the ROS heartbeat (API_CONTRACT §3.2).
"""
import asyncio
from collections import deque
import logging
from io import BytesIO
import time
from typing import AsyncIterator

from PIL import Image

from app.models import ImageSize
from app.ros import topics as T
from app.ros.convert import detections_sim_time, detections_to_boxes
from app.ros.wire import BEST_EFFORT_1, BEST_EFFORT_5
from app.sources.base import CameraSource

JPEG_QUALITY = 62
JPEG_MAX_SIZE = (640, 480)
RATE_WINDOW_S = 5.0
RAW_FALLBACK_S = 4.0
FPS_LOG_S = 10.0
LOW_FPS = 8.0

log = logging.getLogger(__name__)
_RAW_MODES = {"rgb8": ("RGB", "RGB", 3), "bgr8": ("RGB", "BGR", 3), "mono8": ("L", "L", 1)}


def encode_jpeg(frame) -> bytes:
    """ImageFrame -> JPEG bytes (rgb8 natively; bgr8 / mono8 too). Pure; worker thread."""
    if frame.encoding == "jpeg":
        return frame.data
    mode, raw, bpp = _RAW_MODES.get(frame.encoding, ("RGB", "RGB", 3))
    img = Image.frombuffer(mode, (frame.width, frame.height), frame.data, "raw", raw,
                           frame.step or frame.width * bpp, 1)
    # Dashboard-only downscale.  YOLO still receives the original /camera/image.
    # An integer box reduce (1280x960 -> 640x480) is several times cheaper than thumbnail().
    factor = max(-(-img.width // JPEG_MAX_SIZE[0]), -(-img.height // JPEG_MAX_SIZE[1]))
    if factor > 1:
        img = img.reduce(factor)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=False)
    return buf.getvalue()


class RosCameraSource(CameraSource):

    def __init__(self, service, store, settings, ctx):
        super().__init__(service, store, settings)
        self.ctx = ctx
        ctx.register_part(self.part)
        self.frame = None
        self.frame_wall = 0.0
        self._jpeg = (None, b"")         # (frame seq, JPEG) shared by all stream clients
        self._encode_lock = asyncio.Lock()
        self._new_frame = asyncio.Event()  # replaced on every frame; stream clients await it
        self._det_times: deque = deque()
        self._task = None
        self._raw_subscribed = False
        self._jpeg_wall = 0.0            # last compressed frame (raw frames yield to it)
        self._frames_in = 0              # frames received since the last FPS log

    async def start(self) -> None:
        await self.ctx.start()
        mode = self.settings.camera_transport
        if mode in ("compressed", "auto"):
            self.ctx.subscribe(T.CAMERA_COMPRESSED, "jpeg", BEST_EFFORT_1, self._on_image)
        if mode == "raw":
            self._subscribe_raw()
        self.ctx.subscribe(T.PERSON_DETECTIONS, "detections", BEST_EFFORT_5, self._on_detections)
        self._task = asyncio.get_running_loop().create_task(self._watch())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self.ctx.stop()

    def reset(self) -> None:
        if self.frame is not None:     # the store was rebuilt: announce the camera again
            self.service.camera_status(True, ImageSize(w=self.frame.width, h=self.frame.height))

    def _subscribe_raw(self) -> None:
        self._raw_subscribed = True
        self.ctx.subscribe(T.CAMERA_IMAGE, "image", BEST_EFFORT_1, self._on_image)

    # --- ROS callbacks (asyncio thread) ------------------------------------------------------
    def _on_image(self, frame) -> None:
        now = time.monotonic()
        if frame.encoding == "jpeg":
            self._jpeg_wall = now
        elif now - self._jpeg_wall < 1.0:
            return                     # compressed frames are flowing: drop the raw duplicate
        self._frames_in += 1
        first = self.frame is None or (self.frame.width, self.frame.height) != (frame.width,
                                                                                  frame.height)
        self.frame, self.frame_wall = frame, time.monotonic()
        self._new_frame.set()
        self._new_frame = asyncio.Event()
        if first or not self.store.camera.available:
            self.service.camera_status(True, ImageSize(w=frame.width, h=frame.height))

    def _on_detections(self, msg) -> None:
        self._det_times.append(time.monotonic())
        w, h = (self.frame.width, self.frame.height) if self.frame else (None, None)
        boxes = detections_to_boxes(msg, w, h)
        size = ImageSize(w=w or self.settings.camera_fallback_width,
                         h=h or self.settings.camera_fallback_height)
        self.service.camera_detections(boxes, size, sim_time_s=detections_sim_time(msg))

    async def _watch(self) -> None:
        """Every second: detection rate; camera heartbeat while the drone part isn't ROS."""
        started = last_log = time.monotonic()
        while True:
            await asyncio.sleep(1.0)
            now = time.monotonic()
            if (self.settings.camera_transport == "auto" and not self._raw_subscribed
                    and self._jpeg_wall == 0.0 and now - started > RAW_FALLBACK_S):
                log.warning("no frames on %s; falling back to raw %s (slower). Is the "
                            "image_transport republish node running?",
                            T.CAMERA_COMPRESSED, T.CAMERA_IMAGE)
                self._subscribe_raw()
            if now - last_log >= FPS_LOG_S:
                fps = self._frames_in / (now - last_log)
                if self.frame is not None and fps < LOW_FPS:
                    kind = "jpeg" if now - self._jpeg_wall < 2.0 else "raw"
                    log.warning("camera input only %.1f fps (%s): frames arrive slowly from "
                                "Gazebo / ROS, not from the dashboard", fps, kind)
                self._frames_in, last_log = 0, now
            while self._det_times and now - self._det_times[0] > RATE_WINDOW_S:
                self._det_times.popleft()
            span = min(RATE_WINDOW_S, now - self._det_times[0]) if self._det_times else 0.0
            fps = (len(self._det_times) - 1) / span if len(self._det_times) > 1 and span else 0.0
            self.service.detector_fps(fps)
            if "drone" not in self.ctx.ros_parts:
                fresh = self.frame is not None and now - self.frame_wall < \
                    self.settings.heartbeat_timeout_s
                self.service.ros_status(fresh, "OK" if fresh else "—")

    # --- MJPEG --------------------------------------------------------------------------------
    async def latest_jpeg(self, frame=None) -> "bytes | None":
        frame = frame or self.frame
        if frame is None:
            return None
        async with self._encode_lock:
            if self._jpeg[0] != frame.seq:
                self._jpeg = (frame.seq, await asyncio.to_thread(encode_jpeg, frame))
            return self._jpeg[1]

    async def frames(self) -> AsyncIterator[bytes]:
        """Send each new frame as soon as it arrives, at most MJPEG_MAX_FPS.

        Waiting on the frame event (instead of polling on a fixed tick) avoids the judder of
        a poll that just misses a frame and then sleeps a whole extra interval.
        """
        interval = 1.0 / self.settings.mjpeg_max_fps
        loop = asyncio.get_running_loop()
        last_seq = None
        next_at = 0.0
        while True:
            frame = self.frame
            if frame is None or frame.seq == last_seq:
                try:
                    await asyncio.wait_for(self._new_frame.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
                continue
            delay = next_at - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
                frame = self.frame
            next_at = loop.time() + interval
            jpeg = await self.latest_jpeg(frame)
            if jpeg:
                last_seq = frame.seq
                yield jpeg
