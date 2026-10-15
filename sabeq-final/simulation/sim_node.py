#!/usr/bin/env python3
"""
sim_node.py - the Gazebo side of the demo. Runs INSIDE the venv.

  source ~/emergency_drone/venv/bin/activate && python ~/emergency_drone/sim_node.py

Options:
  --model PATH   YOLO weights to use (default yolov8s.pt), e.g. --model ~/emergency_drone/yolo26s.pt
  --cls NAME     class to count (default "person"); looked up by name in the model
  --imgsz N      YOLO resolution while scanning (default 960; 640 = faster, 1280 = more accurate)
  --threads N    YOLO CPU threads (default 4)
  --device D     cpu | 0 (NVIDIA GPU) | intel:gpu | intel:cpu  (intel:* needs an OpenVINO model)
  --idle-hz N    YOLO runs per second BEFORE the scan (default 0 = off until the scan starts)
  --boxes MODE   synced (default): while YOLO runs, the window shows the exact frame YOLO
                 analysed, so every box sits on its person even when the camera turns.
                 live: smooth live video, boxes only drawn while they are fresh.
  --follow WHO   none (default - the Gazebo view is yours to move) | cart | drone | escort
  --view NAME    camera_view.py preset for --follow (chase, side, high, top, ...)
                 Change the Gazebo view any time with camera_view.py (see README).

OpenVINO (much faster on Intel laptops): export once, then pass the folder as --model:
  yolo export model=yolo26s.pt format=openvino imgsz=960
  python sim_node.py --model ~/emergency_drone/yolo26s_openvino_model --device intel:gpu

What it does:
  - Camera window: shows every camera frame at full speed.
  - YOLO (background thread): detects people while the drone scans the routes, at full
    speed / high resolution. While it runs, the window shows the frame YOLO actually
    analysed with its boxes, so the boxes never trail behind a moving camera. It is
    OFF before the scan (unless --idle-hz) and once the best route is chosen.
    Density = share of the centre band (+-CENTER_BAND_DEG) covered by person boxes,
    so the neighbouring routes are not counted and overlapping people still count.
  - Medics: when the scan starts, two medics run from the First Aid Center to the
    golf cart carrying a stretcher and ride on its rear bed.
  - Golf cart: when mission.py sends "cart_start", moves the cart along the chosen
    route to the incident pad.
  - Crowd: people stand on the avenues as obstacles; people on the chosen route hurry
    to the roadside once the drone is overhead. The circle round the injured visitor
    opens when the drone arrives over it. Only moving people get pose updates.
  - Rescue: at the incident the medics put the stretcher next to the injured visitor,
    load them, carry them to the cart; the cart turns and drives back along the
    route to the First Aid Center; the medics carry the patient inside.
  - Siren + speaker: siren and spoken alerts (English + Arabic) on the laptop
    speakers, plus a flashing red/blue banner in the camera window.

Talks to mission.py over UDP on this laptop:
  sends status  -> 127.0.0.1:5601  {"count", "count_center", "coverage", "det_t", "cart_s",
                                    "rescue", "ret_phase", "ret_s", "ret_done", ...}
  receives cmds <- 127.0.0.1:5602  {"cmd": "reset" | "scanning" | "snapshot" | "scan_result" | "cart_start" | "siren"}

Leave this running between missions: each mission.py run sends "reset" first.

Press q in the camera window to quit.
"""
import argparse
import faulthandler
import json
import math
import os

# Point OpenCV's Qt window at the system fonts (silences the QFontDatabase warning)
os.environ.setdefault("QT_QPA_FONTDIR", "/usr/share/fonts/truetype/dejavu")
faulthandler.enable()   # if this script crashes, print where it crashed

import shutil
import socket
import subprocess
import sys
from pathlib import Path
import threading
import time
import wave

import cv2
import numpy as np
import torch
from ultralytics import YOLO
from gz.transport13 import Node
from gz.msgs10.image_pb2 import Image
from gz.msgs10.camera_info_pb2 import CameraInfo
from gz.msgs10.pose_v_pb2 import Pose_V
from gz.msgs10.clock_pb2 import Clock

import demo_common as dc

CAMERA_TOPIC = "/camera"
CAMERA_INFO_TOPIC = "/camera_info"
YOLO_MODEL = "yolov8s.pt"     # small model: much better than nano on small/overlapping people
CONF = 0.25
SCAN_IMGSZ = 960              # resolution while scanning (override with --imgsz)
IDLE_IMGSZ = 640              # low resolution the rest of the time
IDLE_DET_HZ = 0.0             # YOLO rate before the scan (0 = off, saves CPU during take-off)
CPU_THREADS = 4               # YOLO CPU threads (override with --threads); rest is for Gazebo
DISPLAY_HZ = 30
CENTER_BAND_DEG = 15.0        # half-width of the counting band (routes are 45 deg apart)
FALLBACK_HFOV_RAD = 1.74      # x500_mono_cam horizontal FOV if /camera_info is missing
SNAP_DIR = dc.META_PATH.parent / "scan_snapshots"

MOTION_HZ = 15
RUN_SPEED = 5.0               # m/s medics running to the cart
WALK_SPEED = 3.0              # m/s medics walking with the stretcher
TREAT_TIME = 1.5              # s at the patient before loading
LOAD_TIME = 0.8               # s to lift the patient onto the stretcher
TURN_TIME = 1.2               # s cart U-turn at the incident
RETURN_SPEED = 10.0           # m/s cart back to the First Aid Center
TRIGGER_AHEAD = 5.0           # person steps aside once the drone is this close (m, along the route)
PEOPLE_SPEED = 5.0            # m/s people hurry to the roadside
BACK_SPEED = 1.4              # m/s people stroll back onto the avenue after the cart has passed
PASS_MARGIN = 7.0             # m the cart must be past someone before they walk back
INCIDENT_CLEAR_PATH = 3.8     # m the onlookers step back from the cart's path ...
INCIDENT_TRIGGER = 3.0        # onlookers only move once the drone is this close (along its path)
SLOW_ZONE = 12.0              # m before its stop the cart slows down (drone clears the onlookers)
LIVE_BOX_MAX_AGE = 0.25       # --boxes live: hide boxes older than this (s)
SYNCED_MAX_AGE = 1.5          # --boxes synced: fall back to live video if YOLO is this far behind
GAP_STOP = 9.0                # the cart waits if the drone is less than this far ahead of it ...
GAP_FULL = 12.0               # ... and drives at full speed once the drone is this far ahead
INCIDENT_CLEAR_PATIENT = 4.8  # ... and from the injured visitor (room for the medics)

SIREN_WAV = "/tmp/emergency_siren.wav"
ALERT_EN = "Attention. Emergency vehicle approaching. Please clear the way."
ALERT_AR = "انتباه. مركبة طوارئ قادمة. الرجاء إفساح الطريق."

STOP = threading.Event()


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.frame = None
        self.frame_t = 0.0      # time the latest camera frame arrived
        self.cam_fps = 0.0      # camera frames per second arriving from Gazebo
        self.new_frame = threading.Event()
        self.boxes = []         # latest detections: [((x0,y0,x1,y1), in_band), ...]
        self.det_frame = None   # the exact camera frame those boxes were found on
        self.det_t = 0.0        # arrival time of the frame the latest detection used
        self.det_fps = 0.0
        self.model_name = YOLO_MODEL
        self.coverage = 0.0     # % of the centre band covered by person boxes (density)
        self.last_view = None   # latest annotated frame (for snapshots)
        self.fx = None
        self.cx = None
        self.count = 0
        self.count_center = 0
        self.siren = False
        self.phase = "WAITING FOR MISSION"
        self.scan = {}
        self.route = None
        self.cart_s = 0.0
        self.cart_v = 0.0       # current cart speed (m/s, sim time) - drone feed-forward
        self.cart_done = False
        self.path_len = 0.0
        self.detect = True      # False once the best route is chosen
        self.ret_s = 0.0        # cart progress on the way back (m)
        self.ret_done = False


S = State()


# ---- Gazebo camera callbacks ------------------------------------------------
def on_image(msg):
    w, h = msg.width, msg.height
    if w == 0 or h == 0:
        return
    arr = np.frombuffer(msg.data, dtype=np.uint8)
    step = msg.step if msg.step else w * 3
    if arr.size < h * step:
        return
    img = arr[: h * step].reshape(h, step)[:, : w * 3].reshape(h, w, 3)
    frame = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    with S.lock:
        S.frame = frame
        now = time.time()
        if S.frame_t:
            S.cam_fps = 0.9 * S.cam_fps + 0.1 / max(1e-3, now - S.frame_t)
        S.frame_t = now
    S.new_frame.set()


def on_info(msg):
    k = list(msg.intrinsics.k)
    if len(k) >= 3 and k[0] > 0:
        with S.lock:
            S.fx, S.cx = k[0], k[2]


def band_geometry(w):
    fx = S.fx if S.fx else (w / 2) / math.tan(FALLBACK_HFOV_RAD / 2)
    cx = S.cx if S.cx else w / 2
    return cx, fx * math.tan(math.radians(CENTER_BAND_DEG))


# ---- YOLO detector (background thread) -------------------------------------
class Detector(threading.Thread):
    def __init__(self, model_path, class_name, scan_imgsz, threads, device, idle_hz):
        super().__init__(daemon=True)
        self.scan_imgsz = scan_imgsz
        self.idle_hz = max(0.0, idle_hz)
        if device:
            self.device = device
            torch.set_num_threads(threads)
            print(f"[yolo] using device: {device}")
        elif torch.cuda.is_available():
            self.device = 0
            print(f"[yolo] using GPU: {torch.cuda.get_device_name(0)}")
        else:
            self.device = "cpu"
            torch.set_num_threads(threads)
            print(f"[yolo] using CPU ({threads} threads) - no CUDA GPU found")
        print(f"[yolo] loading {model_path} ...")
        try:
            self.model = YOLO(model_path, task="detect")
        except Exception as e:  # noqa: BLE001
            raise SystemExit(f"[yolo] could not load {model_path}: {e}\n"
                             "If this is a YOLO26 model, update ultralytics in the venv "
                             "(see instructions).")
        names = self.model.names  # {index: class name}
        matches = [i for i, n in names.items() if str(n).lower() == class_name.lower()]
        if not matches:
            raise SystemExit(f"[yolo] class '{class_name}' not in this model. "
                             f"Its classes are: {names}. Use --cls <name>.")
        self.cls = matches[0]
        print(f"[yolo] counting class '{names[self.cls]}' (index {self.cls}) "
              f"out of {len(names)} classes")
        dummy = np.zeros((480, 640, 3), dtype=np.uint8)
        self.model.predict(dummy, device=self.device, verbose=False)  # warm-up
        print("[yolo] ready")

    def run(self):
        last_run = 0.0
        while not STOP.is_set():
            scanning = S.phase.startswith("SCANNING")
            if not scanning and (not S.detect or self.idle_hz <= 0):
                if S.boxes or S.det_frame is not None:
                    with S.lock:
                        S.boxes, S.count, S.count_center, S.coverage = [], 0, 0, 0.0
                        S.det_frame = None
                time.sleep(0.1)
                continue
            if not scanning and time.time() - last_run < 1.0 / self.idle_hz:
                time.sleep(0.02)
                continue
            if not S.new_frame.wait(timeout=0.5):
                continue
            S.new_frame.clear()
            with S.lock:
                frame, frame_t = S.frame, S.frame_t
            if frame is None:
                continue

            t0 = time.time()
            h, w = frame.shape[:2]
            cx, band = band_geometry(w)
            imgsz = self.scan_imgsz if scanning else IDLE_IMGSZ
            res = self.model.predict(frame, classes=[self.cls], conf=CONF, imgsz=imgsz,
                                     device=self.device, verbose=False)[0]

            boxes = []
            bx0, bx1 = int(max(0, cx - band)), int(min(w, cx + band))
            mask = np.zeros((h, max(1, bx1 - bx0)), dtype=bool)
            for b in res.boxes.xyxy.cpu().numpy():
                center = abs((b[0] + b[2]) / 2 - cx) <= band
                boxes.append((tuple(b), center))
                if center:
                    x0 = int(max(b[0], bx0)) - bx0
                    x1 = int(min(b[2], bx1)) - bx0
                    mask[int(max(0, b[1])):int(min(h, b[3])), max(0, x0):max(0, x1)] = True

            with S.lock:
                S.boxes = boxes
                S.det_frame = frame
                S.count = len(boxes)
                S.count_center = sum(1 for _, c in boxes if c)
                # Density: overlapping people still cover area even when YOLO merges them.
                S.coverage = float(mask.mean() * 100.0)
                S.det_t = frame_t
                S.det_fps = 1.0 / max(1e-3, time.time() - t0)
            last_run = time.time()


# ---- Siren / speaker --------------------------------------------------------
def make_siren_wav(path, dur=3.0, sr=22050):
    t = np.arange(int(dur * sr)) / sr
    freq = 950 + 450 * np.sin(2 * np.pi * 0.5 * t)          # "wail" sweep
    phase = 2 * np.pi * np.cumsum(freq) / sr
    x = 0.6 * np.sin(phase) + 0.25 * np.sign(np.sin(phase))  # a bit harsher than a sine
    x *= np.minimum(1.0, np.minimum(t, dur - t) / 0.05)       # fade in/out, no clicks
    data = (x / np.max(np.abs(x)) * 0.8 * 32767).astype(np.int16)
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(data.tobytes())


class Siren(threading.Thread):
    """Loops: siren sound -> English alert -> Arabic alert, while siren is ON."""

    def __init__(self):
        super().__init__(daemon=True)
        make_siren_wav(SIREN_WAV)
        self.player = shutil.which("paplay") or shutil.which("aplay")
        self.espeak = shutil.which("espeak-ng") or shutil.which("espeak")
        self.spd = shutil.which("spd-say")
        if not self.player:
            print("[siren] no audio player (paplay/aplay) found - siren will be visual only")
        if not (self.espeak or self.spd):
            print("[siren] no TTS found - install with: sudo apt install espeak-ng")

    def play(self):
        if self.player:
            subprocess.run([self.player, SIREN_WAV], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        else:
            time.sleep(3)

    def say(self, text, lang):
        if self.espeak:
            subprocess.run([self.espeak, "-v", lang, "-s", "150", text],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif self.spd:
            subprocess.run([self.spd, "-w", "-l", lang, text],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def run(self):
        while not STOP.is_set():
            if not S.siren:
                time.sleep(0.1)
                continue
            self.play()
            if S.siren and not STOP.is_set():
                self.say(ALERT_EN, "en")
            if S.siren and not STOP.is_set():
                self.say(ALERT_AR, "ar")


# ---- Medics, stretcher, patient ---------------------------------------------
class Rescue:
    """Two medics carrying a stretcher (a 'formation'), plus the injured visitor.

    AT_BASE -> MOVING (run to the cart) -> ON_CART -> MOVING (to the patient) -> TREAT
    -> LOAD -> MOVING (back to the cart) -> READY (World drives the cart back)
    -> RIDING_BACK -> MOVING (into the First Aid Center) -> INSIDE
    """

    def __init__(self, meta):
        rm = meta.get("rescue_models") or {}
        self.enabled = bool(rm) and "first_aid" in meta
        self.medics = rm.get("medics", [])
        self.stretcher = rm.get("stretcher")
        self.patient = rm.get("patient")
        fa = meta.get("first_aid", {})
        self.door = tuple(fa.get("door_enu", (0.0, 0.0)))
        self.door_h = fa.get("door_heading", 0.0)
        self.inside = tuple(fa.get("inside_enu", self.door))
        self.run_wp = [tuple(p) for p in fa.get("medic_run_enu", [])]
        self.p0 = tuple(meta.get("patient_enu", (0.0, 0.0, 0.0)))
        self.reset()

    def reset(self):
        self.state = "AT_BASE"
        self.c, self.h, self.sz = self.door, self.door_h, dc.CARRY_Z
        self.riding = self.loaded = False
        self.wps, self.speed, self.next, self.final_h = [], WALK_SPEED, None, None
        self.t = 0.0
        self.dist = 0.0
        self.side_wp = self.bed = None

    def label(self):
        return {"AT_BASE": "", "ON_CART": "MEDICS ON BOARD", "TREAT": "MEDICS TREATING PATIENT",
                "LOAD": "LOADING PATIENT ON STRETCHER", "READY": "PATIENT ON BOARD",
                "RIDING_BACK": "RETURNING TO FIRST AID CENTER",
                "INSIDE": "PATIENT DELIVERED TO FIRST AID"}.get(self.state, "")

    def walk(self, wps, speed, next_state, final_h=None):
        self.wps, self.speed, self.next, self.final_h = list(wps), speed, next_state, final_h
        self.state = "MOVING"

    def dispatch(self, cart):
        """Scan started: medics run from the First Aid Center to the back of the cart."""
        if self.enabled and self.state == "AT_BASE":
            behind = dc.cart_local(cart, dc.BED_X - 2.4, 0.0)
            self.walk(self.run_wp[1:] + [behind], RUN_SPEED, "ON_CART")
            print("[rescue] medics running to the golf cart")

    def force_board(self):
        """Cart is leaving: anyone not aboard yet jumps on."""
        if self.enabled and self.state in ("AT_BASE", "MOVING"):
            self.state, self.riding = "ON_CART", True

    def dismount(self, cart):
        """Cart stopped at the incident: walk around the cart to the patient."""
        px, py, pyaw = self.p0
        self.bed = dc.cart_local(cart, dc.BED_X, 0.0)
        n = (-math.sin(pyaw), math.cos(pyaw))
        spots = [(px + n[0] * k, py + n[1] * k) for k in (1.0, -1.0)]
        spot = min(spots, key=lambda q: math.hypot(q[0] - self.bed[0], q[1] - self.bed[1]))
        left = (-math.sin(cart[2]), math.cos(cart[2]))
        side = 1.0 if (spot[0] - self.bed[0]) * left[0] + (spot[1] - self.bed[1]) * left[1] >= 0 else -1.0
        self.side_wp = (self.bed[0] + left[0] * side * 2.2, self.bed[1] + left[1] * side * 2.2)
        # hop off the bed sideways, then walk to the patient
        self.riding, self.c, self.sz = False, self.side_wp, dc.CARRY_Z
        self.h = math.atan2(spot[1] - self.c[1], spot[0] - self.c[0])
        self.walk([spot], WALK_SPEED, "TREAT", final_h=pyaw)
        print("[rescue] medics heading to the patient")

    def unload(self, cart):
        """Cart at the First Aid Center: carry the patient inside."""
        self.bed = dc.cart_local(cart, dc.BED_X, 0.0)
        left = (-math.sin(cart[2]), math.cos(cart[2]))
        d = (self.door[0] - self.bed[0]) * left[0] + (self.door[1] - self.bed[1]) * left[1]
        side = 1.0 if d >= 0 else -1.0
        out = (self.bed[0] + left[0] * side * 2.2, self.bed[1] + left[1] * side * 2.2)
        self.riding, self.c, self.sz = False, out, dc.CARRY_Z
        self.h = math.atan2(self.door[1] - out[1], self.door[0] - out[0])
        self.walk([self.door, self.inside], WALK_SPEED, "INSIDE")
        print("[rescue] carrying the patient into the First Aid Center")

    def enter(self, state):
        self.state = state
        if state == "ON_CART":
            self.riding = True
            print("[rescue] medics on board")
        elif state == "TREAT":
            self.sz, self.t = 0.0, TREAT_TIME      # stretcher down next to the patient
            print("[rescue] treating the patient")
        elif state == "READY":
            self.riding = True
            print("[rescue] patient loaded - ready to go back")
        elif state == "INSIDE":
            print("[rescue] patient delivered to the First Aid Center")

    def step(self, dt):
        if self.state == "MOVING":
            mv = self.speed * dt
            while self.wps and mv > 0:
                tx, ty = self.wps[0]
                dx, dy = tx - self.c[0], ty - self.c[1]
                d = math.hypot(dx, dy)
                if d > 1e-6:
                    self.h = math.atan2(dy, dx)
                if d <= mv:
                    self.c = (tx, ty)
                    self.wps.pop(0)
                    mv -= d
                    self.dist += d
                else:
                    self.c = (self.c[0] + dx / d * mv, self.c[1] + dy / d * mv)
                    self.dist += mv
                    mv = 0
            if not self.wps:
                if self.final_h is not None:
                    self.h = self.final_h
                self.enter(self.next)
        elif self.state == "TREAT":
            self.t -= dt
            if self.t <= 0:
                self.loaded, self.state, self.t = True, "LOAD", LOAD_TIME
                print("[rescue] patient on the stretcher")
        elif self.state == "LOAD":
            self.t -= dt
            if self.t <= 0:
                self.sz = dc.CARRY_Z
                self.walk([self.side_wp], WALK_SPEED, "READY")   # then hop onto the bed

    def poses(self, cart):
        out = []
        if self.riding:
            for name, (lx, ly) in zip(self.medics, dc.MEDIC_RIDE):
                x, y = dc.cart_local(cart, lx, ly)
                out.append((name, x, y, cart[2], dc.BED_TOP))
            sx, sy = dc.cart_local(cart, dc.BED_X, 0.0)
            sh, sz = cart[2], dc.BED_TOP
        else:
            ux, uy = math.cos(self.h), math.sin(self.h)
            moving = self.state == "MOVING"
            for name, k, ph in zip(self.medics, (1, -1), (0.0, math.pi / 2)):
                x, y = self.c[0] + ux * k * dc.MEDIC_SPACING, self.c[1] + uy * k * dc.MEDIC_SPACING
                if moving:
                    dz, roll, pitch = gait(self.dist, self.speed, ph)
                    out.append((name, x, y, self.h, dz, roll, min(pitch, math.radians(5))))
                else:
                    out.append((name, x, y, self.h, 0.0))
            sx, sy, sh, sz = self.c[0], self.c[1], self.h, self.sz
        out.append((self.stretcher, sx, sy, sh, sz))
        if self.loaded:
            out.append((self.patient, sx, sy, sh, sz + dc.STRETCHER_TOP))
        else:
            out.append((self.patient, self.p0[0], self.p0[1], self.p0[2], 0.0))
        return out


# ---- Drone pose (for the marker and the GUI follow camera) -------------------
class DronePose:
    def __init__(self):
        self.lock = threading.Lock()
        self.name = None
        self.p = None        # (x, y, z)
        self.v = (0.0, 0.0, 0.0)
        self.t = 0.0

    def on_poses(self, msg):
        for q in msg.pose:
            if q.name.startswith("x500"):
                now = time.time()
                p = (q.position.x, q.position.y, q.position.z)
                with self.lock:
                    if self.p is not None and now - self.t > 1e-3:
                        dt = now - self.t
                        a = min(1.0, dt / 0.15)              # smoothed velocity
                        self.v = tuple(self.v[i] + a * ((p[i] - self.p[i]) / dt - self.v[i]) for i in range(3))
                    self.name, self.p, self.t = q.name, p, now
                return

    def get(self, ahead=0.08):
        with self.lock:
            if self.p is None or time.time() - self.t > 2.0:
                return None
            return tuple(self.p[i] + self.v[i] * ahead for i in range(3))


DRONE = DronePose()


class SimClock:
    """Gazebo simulation time, so the cart and people move at the same pace as the
    drone even when Gazebo runs slower than real time."""

    def __init__(self):
        self.sim = None
        self.wall = 0.0

    def on_clock(self, msg):
        self.sim = msg.sim.sec + msg.sim.nsec * 1e-9
        self.wall = time.time()

    def now(self):
        if self.sim is not None and time.time() - self.wall < 1.0:
            return self.sim
        return None


CLOCK = SimClock()


def start_view(who, view):
    """Optional: aim the Gazebo window's camera with camera_view.py (once, at start)."""
    import camera_view
    if who in ("drone", "escort"):
        for _ in range(60):
            if DRONE.name or STOP.is_set():
                break
            time.sleep(0.5)
    argv = [who] + ([view] if view else [])
    if who in ("drone", "escort") and DRONE.name:
        argv += ["--drone-name", DRONE.name]
    try:
        camera_view.main(argv)
    except SystemExit as e:  # noqa: PERF203
        if e.code not in (0, None):
            print(f"[view] {e}")


# ---- Walkers and route crowd ------------------------------------------------
class Walkers:
    """Visitors strolling around closed loops (sidewalks, paths, fountains)."""

    def __init__(self, meta):
        self.items = []
        for w in meta.get("walkers", []):
            pts = [tuple(p) for p in w["loop"]]
            pts.append(pts[0])
            cum = [0.0]
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                cum.append(cum[-1] + math.hypot(x1 - x0, y1 - y0))
            if cum[-1] > 1.0:
                self.items.append((w["name"], pts, cum, float(w["speed"]), float(w["phase"])))

    def poses(self, t):
        out = []
        for name, pts, cum, v, ph in self.items:
            L = cum[-1]
            s = (ph + v * t) % L
            i = 0
            while i < len(cum) - 2 and cum[i + 1] < s:
                i += 1
            (x0, y0), (x1, y1) = pts[i], pts[i + 1]
            seg = max(cum[i + 1] - cum[i], 1e-6)
            f = (s - cum[i]) / seg
            bob = 0.025 * abs(math.sin(math.pi * s / 0.7))        # a little step bounce
            out.append((name, x0 + (x1 - x0) * f, y0 + (y1 - y0) * f, math.atan2(y1 - y0, x1 - x0), bob))
        return out


def gait(dist, speed, phase0=0.0, group=False):
    """Procedural walk / run for the one-mesh people: step bounce, side-to-side sway and a
    forward lean, synced to the distance covered. Returns (dz, roll, pitch). No extra models
    or messages - it rides on the pose updates that are sent anyway."""
    run = speed > 2.6
    stride = 1.5 if run else 0.75                    # m per step
    ph = math.pi * dist / stride + phase0            # one bounce per step
    if group:                                        # several people in one mesh: bounce only
        return (0.05 if run else 0.025) * abs(math.sin(ph)), 0.0, 0.0
    dz = (0.09 if run else 0.035) * abs(math.sin(ph))
    roll = math.radians(3.0 if run else 4.0) * math.sin(ph)
    pitch = math.radians(10.0 if run else 4.0)       # lean into the direction of travel
    return dz, roll, pitch


def _project(path, p):
    """(distance along path, closest point) of p onto a polyline."""
    best, acc, out = None, 0.0, (0.0, path[0])
    for (x0, y0), (x1, y1) in zip(path, path[1:]):
        ax, ay = x1 - x0, y1 - y0
        L2 = ax * ax + ay * ay
        L = math.sqrt(L2)
        t = 0.0 if L2 < 1e-9 else max(0.0, min(1.0, ((p[0] - x0) * ax + (p[1] - y0) * ay) / L2))
        cx, cy = x0 + t * ax, y0 + t * ay
        d = math.hypot(p[0] - cx, p[1] - cy)
        if best is None or d < best:
            best, out = d, (acc + t * L, (cx, cy))
        acc += L
    return out


def _dist_path(path, p):
    _, c = _project(path, p)
    return math.hypot(p[0] - c[0], p[1] - c[1]), c


class Crowd:
    """Route crowds. Everybody stands on the avenues; on the chosen route they step aside
    once the drone is overhead, walk back onto the avenue after the cart has passed, and
    have to be cleared again by the drone on the way back. The circle round the injured
    visitor opens when the drone arrives and stays open."""

    def __init__(self, meta):
        self.meta = meta
        self.reset()

    def reset(self):
        self.p = {}
        for q in self.meta.get("people", []):
            self.p[q["name"]] = dict(q, hx=q["x"], hy=q["y"], hyaw=q["yaw"], cx=q["x"], cy=q["y"],
                                     yaw=q["yaw"], mode="idle", ps=None, ps_ret=None, cleared_ret=False,
                                     dirty=False)

    def start(self, route, off):
        path = dc.build_path(self.meta, route)
        L = dc.path_length(path)
        pat = self.meta.get("patient_enu", self.meta["incident_enu"])[:2]
        taken = []
        for q in self.p.values():
            if q["route"] == route:
                q["ps"] = q["s"] + off
            elif q["route"] == "incident":
                # they only react once the drone is over the injured visitor
                q["ps"] = L - INCIDENT_TRIGGER - TRIGGER_AHEAD
                x, y = q["hx"], q["hy"]
                for _ in range(3):
                    d_path, (cx, cy) = _dist_path(path, (x, y))
                    if d_path < INCIDENT_CLEAR_PATH:
                        if d_path < 1e-3:
                            cx, cy = cx - 1.0, cy
                            d_path = 1.0
                        k = INCIDENT_CLEAR_PATH + 0.4 * ((hash(q["name"]) % 10) / 10.0)
                        x, y = cx + (x - cx) / d_path * k, cy + (y - cy) / d_path * k
                    dp = math.hypot(x - pat[0], y - pat[1])
                    if dp < INCIDENT_CLEAR_PATIENT:
                        x, y = pat[0] + (x - pat[0]) / dp * INCIDENT_CLEAR_PATIENT, \
                               pat[1] + (y - pat[1]) / dp * INCIDENT_CLEAR_PATIENT
                for _ in range(8):                       # don't all squeeze onto the same spot
                    if all(math.hypot(x - tx, y - ty) >= 0.7 for tx, ty in taken):
                        break
                    dp = max(math.hypot(x - pat[0], y - pat[1]), 1e-3)
                    x, y = x + (x - pat[0]) / dp * 0.7, y + (y - pat[1]) / dp * 0.7
                taken.append((x, y))
                q["clear_x"], q["clear_y"] = x, y

    def start_return(self, ret_path):
        for q in self.p.values():
            if q["ps"] is not None and q["route"] != "incident":
                q["ps_ret"] = _project(ret_path, (q["hx"], q["hy"]))[0]

    def count(self, route):
        return sum(q.get("size", 1) for q in self.p.values() if q["route"] in (route, "incident"))

    def _walk(self, q, tx, ty, speed, step):
        dx, dy = tx - q["cx"], ty - q["cy"]
        d = math.hypot(dx, dy)
        mv = speed * step
        q["dist"] = q.get("dist", 0.0) + min(d, mv)
        q["speed"] = speed
        if d <= mv or d < 1e-6:
            q["cx"], q["cy"] = tx, ty
            return True, dx, dy
        q["cx"] += dx / d * mv
        q["cy"] += dy / d * mv
        if not q.get("group"):                         # groups move without turning
            q["yaw"] = math.atan2(dy, dx)
        return False, dx, dy

    def step(self, step, leg, drone_s, cart_s):
        """leg: None (before the escort), "out" or "ret"; drone_s / cart_s on that leg's path."""
        out = []
        for name, q in self.p.items():
            m = q["mode"]
            ps = q["ps"] if leg == "out" else q["ps_ret"] if leg == "ret" else None
            near = ps is not None and drone_s is not None and drone_s + TRIGGER_AHEAD >= ps
            if m in ("idle", "back") and near and not (leg == "ret" and q["cleared_ret"]):
                q["mode"] = m = "clear"                   # the drone is overhead: get out of the way
                if leg == "ret":
                    q["cleared_ret"] = True
            elif m == "aside" and q["route"] != "incident" and ps is not None:
                passed = cart_s is not None and cart_s > ps + PASS_MARGIN
                far = leg == "ret" and not q["cleared_ret"] and drone_s is not None \
                    and drone_s + TRIGGER_AHEAD + 20.0 < ps
                if passed or far:
                    q["mode"] = m = "back"                # the cart has gone: back onto the avenue
            if m == "clear":
                arrived, dx, dy = self._walk(q, q["clear_x"], q["clear_y"], PEOPLE_SPEED, step)
                if arrived:
                    q["mode"] = "aside"
                    if not q.get("group") and math.hypot(dx, dy) > 1e-6:
                        q["yaw"] = math.atan2(-dy, -dx)  # turn to watch the cart pass
                q["dirty"] = True
            elif m == "back":
                arrived, _, _ = self._walk(q, q["hx"], q["hy"], BACK_SPEED, step)
                if arrived:
                    q["mode"] = "idle"
                    q["yaw"] = q["hyaw"]
                q["dirty"] = True
            if q["dirty"]:
                if q["mode"] in ("clear", "back"):       # still on the move: walk / run
                    dz, roll, pitch = gait(q.get("dist", 0.0), q.get("speed", 1.0),
                                           (hash(name) % 7) * 0.45, bool(q.get("group")))
                    out.append((name, q["cx"], q["cy"], q["yaw"], dz, roll, pitch))
                else:                                    # arrived: stand upright again
                    out.append((name, q["cx"], q["cy"], q["yaw"]))
                q["dirty"] = q["mode"] in ("clear", "back")
        return out

    def all_poses(self):
        return [(n, q["cx"], q["cy"], q["yaw"]) for n, q in self.p.items()]


# ---- Cart + people motion, UDP link -----------------------------------------
class World(threading.Thread):
    def __init__(self, node, meta):
        super().__init__(daemon=True)
        self.node = node
        self.meta = meta
        self.srv = f"/world/{meta['world']}/set_pose_vector"
        self.rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rx.bind(("127.0.0.1", dc.CMD_PORT))
        self.rx.setblocking(False)
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.pose_tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.path = None
        self.L = 0.0
        self.moving = False
        self.speed = 3.0
        self.lead = 12.0
        self.people = []
        self.warned = False
        self.cart = tuple(meta["cart_start_enu"])     # current cart pose (x, y, heading)
        self.rescue = Rescue(meta)
        self.ret_phase = None    # None | "TURN" | "RETURN" | "DONE"
        self.ret_path, self.ret_L = None, 0.0
        self.turn_t, self.turn_h0, self.turn_h1 = 0.0, 0.0, 0.0
        self.rescue_send_until = time.time() + 3.0
        self.crowd = Crowd(meta)
        self.walkers = Walkers(meta)
        self.t0 = time.time()
        print(f"[world] {len(self.walkers.items)} walkers, {len(self.crowd.p)} route people animated")

    def handle(self, cmd):
        c = cmd.get("cmd")
        if c == "scanning":
            S.phase = f"SCANNING {cmd.get('route')}"
            self.rescue.dispatch(self.cart)
        elif c == "snapshot":
            view = S.last_view
            if view is not None:
                SNAP_DIR.mkdir(parents=True, exist_ok=True)
                path = SNAP_DIR / f"scan_{cmd.get('route')}.jpg"
                cv2.imwrite(str(path), view)
                print(f"[scan] saved {path}")
        elif c == "scan_result":
            S.scan = cmd.get("summary", {})
            S.route = cmd.get("best")
            S.phase = f"BEST ROUTE: {S.route}"
            S.detect = False
            print("[yolo] best route chosen - detection OFF")
        elif c == "reset":
            # Put the cart and every person back where gen_world.py placed them
            cx, cy, cyaw = self.meta["cart_start_enu"]
            poses = [("golf_cart", cx, cy, cyaw)]
            self.crowd.reset()
            poses += self.crowd.all_poses()        # everybody back on the avenues
            self.cart = (cx, cy, cyaw)
            self.rescue.reset()
            self.ret_phase, self.ret_path, self.ret_L = None, None, 0.0
            self.rescue_send_until = time.time() + 2.0
            if self.rescue.enabled:
                poses += self.rescue.poses(self.cart)
            self.set_poses(poses)
            self.path, self.moving, self.people = None, False, []
            S.cart_s, S.cart_done, S.path_len, S.cart_v = 0.0, False, 0.0, 0.0
            S.ret_s, S.ret_done, S.detect = 0.0, False, True
            S.route, S.scan, S.siren = None, {}, False
            S.phase = "WAITING FOR MISSION"
            print("[world] reset: cart and people back to start")
        elif c == "siren":
            S.siren = bool(cmd.get("on"))
            print(f"[siren] {'ON' if S.siren else 'OFF'}")
        elif c == "cart_start":
            route = cmd["route"]
            self.path = dc.build_path(self.meta, route)
            self.L = dc.path_length(self.path)
            off = dc.route_offset(self.meta, route)
            self.crowd.start(route, off)
            self.speed = float(cmd.get("speed", 3.0))
            self.lead = float(cmd.get("lead", 12.0))
            S.route, S.cart_s, S.cart_done, S.path_len = route, 0.0, False, self.L
            S.phase = f"ESCORTING ON {route}"
            self.moving = True
            self.rescue.force_board()
            print(f"[cart] start on {route}: {self.L:.0f} m at {self.speed} m/s, "
                  f"{self.crowd.count(route)} people to clear")

    def set_poses(self, poses):
        # Hand the poses to pose_sender.py (separate process). Never blocks,
        # so the camera window and YOLO keep running during the escort.
        data = [[p[0]] + [round(v, 4) for v in p[1:]] for p in poses]
        self.pose_tx.sendto(json.dumps(data).encode(), ("127.0.0.1", dc.POSE_PORT))

    def rescue_tick(self, step):
        """Advance the medics and the return trip. Returns poses to apply."""
        r = self.rescue
        if not r.enabled:
            return []
        poses = []
        if r.state == "READY" and self.ret_phase is None and S.route:
            self.ret_path = dc.build_return_path(self.meta, S.route)
            self.ret_L = dc.path_length(self.ret_path)
            self.crowd.start_return(self.ret_path)
            self.turn_t, self.turn_h0 = 0.0, self.cart[2]
            self.turn_h1 = dc.along(self.ret_path, 0.0)[2]
            self.ret_phase, r.state = "TURN", "RIDING_BACK"
            S.ret_s = 0.0
            print(f"[cart] turning back: {self.ret_L:.0f} m to the First Aid Center")
        if self.ret_phase == "TURN":
            self.turn_t += step
            f = min(1.0, self.turn_t / TURN_TIME)
            yaw = self.turn_h0 + dc.wrap(self.turn_h1 - self.turn_h0) * f
            self.cart = (self.cart[0], self.cart[1], yaw)
            poses.append(("golf_cart", *self.cart))
            if f >= 1.0:
                self.ret_phase = "RETURN"
        elif self.ret_phase == "RETURN":
            v = RETURN_SPEED * self.lead_factor(self.ret_path, S.ret_s, self.ret_L)
            S.ret_s = min(S.ret_s + v * step, self.ret_L)
            S.cart_v = v
            self.cart = dc.along(self.ret_path, S.ret_s)
            poses.append(("golf_cart", *self.cart))
            if S.ret_s >= self.ret_L:
                self.ret_phase, S.ret_done, S.cart_v = "DONE", True, 0.0
                print("[cart] arrived at the First Aid Center")
                r.unload(self.cart)
        r.step(step)
        lab = r.label()
        if lab and not S.phase.startswith("SCANNING") and not S.phase.startswith("BEST") \
                and not S.phase.startswith("ESCORTING"):
            S.phase = lab
        elif lab and self.ret_phase:
            S.phase = lab
        if r.state not in ("AT_BASE",) or time.time() < self.rescue_send_until:
            poses += r.poses(self.cart)
        return poses

    def drone_s(self, path):
        """How far along the path the real drone is (None if unknown / far from it)."""
        dp = DRONE.get(ahead=0.0)
        if dp is None or path is None:
            return None
        s_on, c = _project(path, dp[:2])
        return s_on if math.hypot(dp[0] - c[0], dp[1] - c[1]) < 12.0 else None

    def lead_factor(self, path, s_cart, L):
        """0..1: slow / stop the cart until the drone is 10-14 m ahead of it."""
        ds = self.drone_s(path)
        if ds is None:
            return 1.0
        stop = min(GAP_STOP, L - s_cart - 1.0)
        full = min(GAP_FULL, L - s_cart)
        if ds >= L - 1.0 or full <= stop:
            return 1.0
        return max(0.0, min(1.0, (ds - s_cart - stop) / (full - stop)))

    def run(self):
        dt = 1.0 / MOTION_HZ
        last = time.time()
        last_sim = None
        while not STOP.is_set():
            t0 = time.time()
            while True:
                try:
                    data, _ = self.rx.recvfrom(65535)
                    self.handle(json.loads(data))
                except BlockingIOError:
                    break
                except (ValueError, KeyError) as e:
                    print(f"[world] bad command: {e}")
            sim_now = CLOCK.now()
            if sim_now is not None and last_sim is not None:
                step = sim_now - last_sim            # follow Gazebo's clock
            else:
                step = t0 - last
            step = max(0.0, min(step, 0.5))
            last, last_sim = t0, sim_now

            poses = []
            if self.moving:
                end = self.L - dc.CART_STOP_BEFORE
                v = self.speed
                if end - S.cart_s < SLOW_ZONE:              # ease in while the drone clears the onlookers
                    v = max(2.5, min(self.speed, 0.6 * (end - S.cart_s)))
                v *= self.lead_factor(self.path, S.cart_s, self.L)   # keep the drone 10-20 m ahead
                S.cart_s = min(S.cart_s + v * step, end)
                S.cart_v = v
                x, y, h = dc.along(self.path, S.cart_s)
                self.cart = (x, y, h)
                poses.append(("golf_cart", x, y, h))
                if S.cart_s >= end:
                    self.moving = False
                    S.cart_v = 0.0
                    S.cart_done = True
                    S.phase = "ARRIVED AT INCIDENT"
                    print("[cart] arrived")
                    self.rescue.dismount(self.cart)
            poses += self.rescue_tick(step)
            t = t0 - self.t0
            if self.ret_phase in ("RETURN", "DONE") and self.ret_path:
                leg, path, cs = "ret", self.ret_path, S.ret_s
            elif self.path:
                leg, path, cs = "out", self.path, S.cart_s
            else:
                leg, path, cs = None, None, None
            ds = self.drone_s(path) if path else None
            if ds is None and cs is not None:
                ds = cs + self.lead
            poses += self.crowd.step(step, leg, ds, cs)
            poses += self.walkers.poses(t)
            mk = self.meta.get("marker")
            dp = DRONE.get() if mk else None
            if dp:
                poses.append((mk["name"], dp[0], dp[1], 0.0, dp[2] + mk["dz"]))
            if poses:
                self.set_poses(poses)

            with S.lock:
                status = {"count": S.count, "count_center": S.count_center,
                          "coverage": round(S.coverage, 2), "det_t": S.det_t,
                          "cart_s": round(S.cart_s, 2), "cart_v": round(S.cart_v, 2),
                          "cart_done": S.cart_done,
                          "path_len": round(S.path_len, 2), "route": S.route,
                          "rescue": self.rescue.state if self.rescue.enabled else None,
                          "ret_phase": self.ret_phase,
                          "ret_s": round(S.ret_s, 2), "ret_len": round(self.ret_L, 2),
                          "ret_done": S.ret_done,
                          "cart": [round(v, 3) for v in self.cart], "t": t0}
            self.tx.sendto(json.dumps(status).encode(), ("127.0.0.1", dc.STATUS_PORT))
            time.sleep(max(0.0, dt - (time.time() - t0)))


# ---- Display ----------------------------------------------------------------
def text(frame, line, y, scale=0.8, col=(255, 255, 255)):
    cv2.putText(frame, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4)
    cv2.putText(frame, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, scale, col, 2)


def draw(frame, boxes, synced=False):
    h, w = frame.shape[:2]
    cx, band = band_geometry(w)
    x0, x1 = int(cx - band), int(cx + band)
    cv2.line(frame, (x0, 0), (x0, h), (0, 255, 255), 1)
    cv2.line(frame, (x1, 0), (x1, h), (0, 255, 255), 1)
    for (bx0, by0, bx1, by1), center in boxes:
        col = (0, 255, 0) if center else (160, 160, 160)
        cv2.rectangle(frame, (int(bx0), int(by0)), (int(bx1), int(by1)), col, 2)

    text(frame, S.phase, 30)
    if synced:
        tag = "YOLO frame"
        (tw, _), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
        cv2.putText(frame, tag, (w - tw - 10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 4)
        cv2.putText(frame, tag, (w - tw - 10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
    if S.phase.startswith("SCANNING") or (S.detect and S.det_frame is not None):
        text(frame, f"People in band: {S.count_center}  density: {S.coverage:.1f}%  "
                    f"(whole frame: {S.count})", 62)
    elif S.detect:
        text(frame, "YOLO starts when the route scan starts", 62, 0.7, (200, 200, 200))
    else:
        text(frame, "Detection OFF - route chosen", 62, 0.8, (0, 200, 255))
    text(frame, f"camera: {S.cam_fps:.1f} fps   {S.model_name}: {S.det_fps:.1f} fps",
         90, 0.6, (200, 200, 200))
    y = 122
    for r, v in S.scan.items():
        mark = "  <== BEST" if r == S.route else ""
        text(frame, f"{r}: {v}{mark}", y, 0.7, (0, 255, 0))
        y += 28

    if S.siren:
        red = int(time.time() * 3) % 2 == 0
        col = (0, 0, 255) if red else (255, 60, 0)
        cv2.rectangle(frame, (0, h - 50), (w, h), col, -1)
        cv2.putText(frame, "SIREN ON - CLEAR THE WAY", (w // 2 - 220, h - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 3)
    return frame


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=YOLO_MODEL, help="YOLO weights (.pt)")
    ap.add_argument("--cls", default="person", help="class name to count")
    ap.add_argument("--imgsz", type=int, default=SCAN_IMGSZ, help="YOLO resolution while scanning")
    ap.add_argument("--threads", type=int, default=CPU_THREADS, help="YOLO CPU threads")
    ap.add_argument("--idle-hz", type=float, default=IDLE_DET_HZ,
                    help="max YOLO runs/s outside the scan (use 30 for as fast as possible)")
    ap.add_argument("--device", default=None,
                    help="cpu | 0 (NVIDIA) | intel:gpu | intel:cpu (OpenVINO models). Default: auto")
    ap.add_argument("--boxes", choices=["synced", "live"], default="synced",
                    help="synced: show the frame YOLO analysed (boxes always on target); "
                         "live: live video, boxes only while fresh")
    ap.add_argument("--follow", choices=["none", "cart", "drone", "escort"], default="none",
                    help="aim the Gazebo camera at start (default none = free mouse view)")
    ap.add_argument("--view", default=None, help="camera_view.py preset for --follow")
    ap.add_argument("--no-follow", action="store_true", help=argparse.SUPPRESS)  # old flag, now the default
    args = ap.parse_args()
    model_path = os.path.expanduser(args.model)
    if model_path.endswith(".pt") and os.sep in model_path and not os.path.exists(model_path):
        raise SystemExit(f"Model file not found: {model_path}")
    S.model_name = os.path.basename(model_path)

    meta = dc.load_meta()
    if not meta.get("people"):
        print("NOTE: world_meta.json has no people (test/no-people world) - "
              "crowd clearing is disabled.")

    detector = Detector(model_path, args.cls, args.imgsz, args.threads, args.device, args.idle_hz)   # loads YOLO before anything else starts

    node = Node()
    if not node.subscribe(Image, CAMERA_TOPIC, on_image):
        raise SystemExit(f"Could not subscribe to {CAMERA_TOPIC}")
    node.subscribe(CameraInfo, CAMERA_INFO_TOPIC, on_info)
    node.subscribe(Pose_V, f"/world/{meta['world']}/dynamic_pose/info", DRONE.on_poses)
    node.subscribe(Clock, f"/world/{meta['world']}/clock", CLOCK.on_clock)
    if args.follow != "none" and not args.no_follow:
        threading.Thread(target=start_view, args=(args.follow, args.view), daemon=True).start()

    sender = subprocess.Popen([sys.executable, str(Path(__file__).with_name("pose_sender.py")),
                               meta["world"]])
    World(node, meta).start()
    Siren().start()
    detector.start()
    print("Running. Now start mission.py in another terminal (without the venv). "
          "Press q in the window to quit.")

    try:
        while not STOP.is_set():
            t0 = time.time()
            synced = False
            with S.lock:
                now = time.time()
                if (args.boxes == "synced" and S.det_frame is not None
                        and now - S.det_t < SYNCED_MAX_AGE):
                    # the frame YOLO analysed + its own boxes: they always line up
                    frame, boxes, synced = S.det_frame.copy(), list(S.boxes), True
                else:
                    frame = None if S.frame is None else S.frame.copy()
                    fresh = args.boxes == "live" and now - S.det_t < LIVE_BOX_MAX_AGE
                    boxes = list(S.boxes) if fresh else []
            if frame is not None:
                view = draw(frame, boxes, synced)
                S.last_view = view
                cv2.imshow("Emergency drone - camera", view)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
            time.sleep(max(0.0, 1.0 / DISPLAY_HZ - (time.time() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        S.siren = False
        STOP.set()
        sender.terminate()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
