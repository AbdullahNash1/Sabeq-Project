#!/usr/bin/env bash
set -e -o pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/Backend"
FRONTEND="$ROOT/Frontend"
WS="$HOME/Sabeq0/ros2_ws"
PKG="$WS/src/sabeq_ros"
MISSION_DST="$PKG/sabeq_ros/mission_node.py"
CART_DST="$PKG/sabeq_ros/cart_node.py"
ROUTES="$PKG/config/routes.yaml"
WORLD_META="$HOME/emergency_drone/world_meta.json"
MISSION_SRC="$ROOT/ros_overrides/mission_node.py"

echo "========================================="
echo " SABEQ Dashboard V2.7 - ONE TIME SETUP"
echo "========================================="

[ -f /opt/ros/humble/setup.bash ] || { echo "[ERROR] ROS 2 Humble not found"; exit 1; }
[ -d "$WS" ] || { echo "[ERROR] ROS workspace not found: $WS"; exit 1; }
[ -f "$WORLD_META" ] || { echo "[ERROR] world_meta.json not found: $WORLD_META"; exit 1; }
[ -f "$ROUTES" ] || { echo "[ERROR] routes.yaml not found: $ROUTES"; exit 1; }
[ -f "$MISSION_DST" ] || { echo "[ERROR] mission_node.py not found: $MISSION_DST"; exit 1; }
[ -f "$CART_DST" ] || { echo "[ERROR] cart_node.py not found: $CART_DST"; exit 1; }
[ -f "$MISSION_SRC" ] || { echo "[ERROR] v2 mission override missing: $MISSION_SRC"; exit 1; }

# ROS setup scripts are not compatible with bash nounset (-u), so this script deliberately
# uses `set -e -o pipefail` only.
source /opt/ros/humble/setup.bash

echo "[1/6] Applying ONLY the required ROS integration changes"

# One stable backup of the working pre-v2 files.
[ -f "$MISSION_DST.pre_dashboard_v22.bak" ] || cp "$MISSION_DST" "$MISSION_DST.pre_dashboard_v22.bak"
[ -f "$CART_DST.pre_dashboard_v22.bak" ] || cp "$CART_DST" "$CART_DST.pre_dashboard_v22.bak"
[ -f "$ROUTES.pre_dashboard_v22.bak" ] || cp "$ROUTES" "$ROUTES.pre_dashboard_v22.bak"

# 1) Mission node: real PX4 telemetry + high-speed 30 m escort lead.
cp "$MISSION_SRC" "$MISSION_DST"

# 2) Cart: 30 km/h max + short launch delay. No path/control algorithm changes.
python3 - "$CART_DST" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
s = path.read_text()
new, n1 = re.subn(r'(?m)^MAX_SPEED\s*=\s*[0-9.]+\s*$', 'MAX_SPEED = 8.33', s, count=1)
if n1 != 1:
    raise SystemExit('ERROR: could not find exactly one top-level MAX_SPEED constant in cart_node.py')
new, n2 = re.subn(r'(?m)^START_DELAY_SEC\s*=\s*[0-9.]+\s*$', 'START_DELAY_SEC = 1.0', new, count=1)
if n2 != 1:
    raise SystemExit('ERROR: could not find exactly one START_DELAY_SEC constant in cart_node.py')
path.write_text(new)
print('[OK] cart_node MAX_SPEED = 8.33 m/s (~30 km/h)')
print('[OK] cart_node START_DELAY_SEC = 1.0 s')
PY

# 3) Backend world validation requires top-level start + incident in routes.yaml.
#    Values are copied from the CURRENT world_meta.json; no route waypoint is changed.
python3 - "$ROUTES" "$WORLD_META" <<'PY'
import json
import re
import sys
from pathlib import Path

routes_path = Path(sys.argv[1])
meta_path = Path(sys.argv[2])
meta = json.loads(meta_path.read_text())
start = [float(x) for x in meta.get('cart_start_enu', [0.0, 0.0])[:2]]
incident = [float(x) for x in meta['incident_enu'][:2]]
s = routes_path.read_text()

lines = []
if not re.search(r'(?m)^start\s*:', s):
    lines.append(f"start: [{start[0]:.1f}, {start[1]:.1f}]")
if not re.search(r'(?m)^incident\s*:', s):
    lines.append(f"incident: [{incident[0]:.1f}, {incident[1]:.1f}]")
if lines:
    s = '\n'.join(lines) + '\n\n' + s
    routes_path.write_text(s)
    print('[OK] routes.yaml start / incident added from world_meta.json')
else:
    print('[OK] routes.yaml already contains start / incident')
PY

python3 -m py_compile \
  "$MISSION_DST" \
  "$CART_DST" \
  "$PKG/sabeq_ros/yolo_node.py" \
  "$ROOT/integration/backend_adapter_node.py" \
  "$HOME/emergency_drone/crowd_clear_node.py" \
  "$HOME/emergency_drone/rescue_return_node.py"

echo "[2/6] Building sabeq_ros"
cd "$WS"
colcon build --packages-select sabeq_ros --symlink-install
source install/setup.bash

echo "[3/6] Backend Python environment"
cd "$BACKEND"
# A fresh clone has no .env (it is gitignored); start from the real-ROS settings.
[ -f .env ] || cp .env.ros .env
if [ ! -d .venv ]; then
  python3 -m venv --system-site-packages .venv
fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

echo "[4/6] Backend config / world validation"
.venv/bin/python -m py_compile app/main.py app/config.py app/world.py app/ros/converters.py app/sources/ros/camera.py
.venv/bin/python - <<'PY'
from app.config import get_settings
from app.world import load_world
settings = get_settings()
world = load_world(settings)
print('[OK] Backend world validation passed:', ', '.join(world.routes))
PY

echo "[5/6] Frontend dependencies"
command -v node >/dev/null 2>&1 || { echo "[ERROR] Node.js is not installed"; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "[ERROR] npm is not installed"; exit 1; }
NODE_MAJOR="$(node -p 'Number(process.versions.node.split(".")[0])')"
NODE_MINOR="$(node -p 'Number(process.versions.node.split(".")[1])')"
NODE_OK=false
if [ "$NODE_MAJOR" -eq 22 ] && [ "$NODE_MINOR" -ge 18 ]; then
  NODE_OK=true
elif [ "$NODE_MAJOR" -eq 24 ] && [ "$NODE_MINOR" -ge 12 ]; then
  NODE_OK=true
elif [ "$NODE_MAJOR" -gt 24 ]; then
  NODE_OK=true
fi
if [ "$NODE_OK" != true ]; then
  echo "[ERROR] Frontend requires Node ^22.18.0 or >=24.12.0 (current: $(node -v))"
  exit 1
fi
cd "$FRONTEND"
rm -rf node_modules
npm ci

echo "[6/6] Frontend build check"
npm run build

echo
echo "========================================="
echo " SABEQ V2.7 SETUP COMPLETE"
echo "========================================="
echo "Applied changes:"
echo "  - mission_node: REAL PX4 position / heading / speed / battery"
echo "  - mission_node: stable selected-route escort, approximately 30 m ahead of REAL cart"
echo "  - PX4 escort cruise: 12 m/s (~43 km/h), max 15 m/s
  - cart MAX_SPEED: 8.33 m/s (~30 km/h)
  - cart safety governor: pauses cart below 18 m lead, resumes at 25 m"
echo "  - routes.yaml start/incident synced from world_meta"
echo "  - dashboard camera: 640x480, JPEG 62, max 10 FPS"
echo "  - rescue: two medics + stretcher + patient animation, then automatic same-route return"
echo "  - YOLO: image_size=1280; annotated image disabled at runtime"
echo "  - route selection unchanged: least crowded CENTER-ROI median; shortest distance breaks ties"
echo
echo "Run:"
echo "  bash $ROOT/scripts/run_integrated.sh"
