#!/usr/bin/env bash
set -e -o pipefail

DASH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$HOME/Sabeq0"
WS="$ROOT/ros2_ws"
PROJECT="$HOME/emergency_drone"
ROS_SETUP="/opt/ros/humble/setup.bash"
BACKEND="$DASH_ROOT/Backend"
FRONTEND="$DASH_ROOT/Frontend"
ADAPTER="$DASH_ROOT/integration/backend_adapter_node.py"
CAM_GZ_TOPIC="/world/expo_demo/model/x500_mono_cam_0/link/camera_link/sensor/camera/image"

YOLO_MODEL=""
for candidate in "$ROOT/ai/yolo26s.pt" "$ROOT/ai/yolo11-26s.pt"; do
  if [ -f "$candidate" ]; then YOLO_MODEL="$candidate"; break; fi
done
if [ -z "$YOLO_MODEL" ] && [ -d "$ROOT/ai" ]; then
  YOLO_MODEL="$(find "$ROOT/ai" -maxdepth 2 -type f -name '*.pt' | head -n 1 || true)"
fi

term() {
  local title="$1"
  local cmd="$2"
  gnome-terminal --title="$title" -- bash -lc "
    echo '=========================================';
    echo ' SABEQ0 V2.7: $title';
    echo '=========================================';
    source '$ROS_SETUP';
    cd '$WS';
    source install/setup.bash;
    $cmd;
    status=\$?;
    echo;
    echo '[SABEQ0] Process exited with code:' \$status;
    echo '[SABEQ0] Terminal kept open for debugging.';
    exec bash
  "
}

publisher_count() {
  ros2 topic info "$1" 2>/dev/null | awk -F': ' '/Publisher count:/ {print $2}' | tail -n 1
}

wait_for_publisher() {
  local topic="$1" timeout="$2" label="$3"
  echo "[WAIT] $label -> $topic"
  for ((i=1; i<=timeout; i++)); do
    local count="$(publisher_count "$topic" || true)"
    if [[ "$count" =~ ^[0-9]+$ ]] && [ "$count" -gt 0 ]; then
      echo "[OK] $label ready"
      return 0
    fi
    sleep 1
  done
  echo "[ERROR] $label did not become ready on $topic"
  return 1
}

wait_for_http() {
  local url="$1" timeout="$2" label="$3"
  echo "[WAIT] $label -> $url"
  for ((i=1; i<=timeout; i++)); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "[OK] $label ready"
      return 0
    fi
    sleep 1
  done
  echo "[ERROR] $label did not become ready: $url"
  return 1
}

echo "========================================="
echo " SABEQ0 V2.7 + STABLE ROUTE ESCORT + 1280 YOLO"
echo "========================================="

[ -f "$ROS_SETUP" ] || { echo "[ERROR] ROS 2 Humble missing"; exit 1; }
[ -d "$WS" ] || { echo "[ERROR] Workspace missing: $WS"; exit 1; }
[ -f "$PROJECT/launch_sim.sh" ] || { echo "[ERROR] launch_sim.sh missing"; exit 1; }
[ -f "$ADAPTER" ] || { echo "[ERROR] Adapter missing: $ADAPTER"; exit 1; }
[ -x "$BACKEND/.venv/bin/python" ] || { echo "[ERROR] Backend .venv missing. Run setup_once.sh first."; exit 1; }
[ -d "$FRONTEND/node_modules" ] || { echo "[ERROR] Frontend node_modules missing. Run setup_once.sh first."; exit 1; }
[ -n "$YOLO_MODEL" ] && [ -f "$YOLO_MODEL" ] || { echo "[ERROR] YOLO model not found under $ROOT/ai"; exit 1; }
command -v gnome-terminal >/dev/null 2>&1 || { echo "[ERROR] gnome-terminal missing"; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "[ERROR] curl missing"; exit 1; }

# /camera/image is ~3.7 MB per raw frame. With Linux's default ~208 KB UDP receive buffer,
# DDS drops fragments of those frames, so the dashboard and YOLO see a stuttering, lagging feed.
RMEM_MAX="$(sysctl -n net.core.rmem_max 2>/dev/null || echo 0)"
if [ "$RMEM_MAX" -lt 67108864 ]; then
  echo "[WARN] net.core.rmem_max=$RMEM_MAX is too small for raw camera frames over DDS."
  echo "[WARN] Fix (until reboot): sudo sysctl -w net.core.rmem_max=2147483647 net.core.rmem_default=67108864 net.ipv4.ipfrag_time=3 net.ipv4.ipfrag_high_thresh=134217728"
fi

source "$ROS_SETUP"
cd "$WS"
colcon build --packages-select sabeq_ros --symlink-install
source install/setup.bash

echo "[1/14] Gazebo + PX4"
term "01 - Gazebo + PX4" "bash '$PROJECT/launch_sim.sh'"
sleep 10

echo "[2/14] Camera + Clock Bridge"
term "02 - Camera + Clock Bridge" "
ros2 run ros_gz_bridge parameter_bridge \\
'${CAM_GZ_TOPIC}@sensor_msgs/msg/Image[gz.msgs.Image' \\
'/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock' \\
--ros-args -r '${CAM_GZ_TOPIC}:=/camera/image'
"
wait_for_publisher /camera/image 30 "Camera Bridge" || exit 1

# The dashboard streams JPEG made here in C++, so no raw 3.7 MB frame goes through Python.
# Without the plugin the backend falls back to the raw /camera/image (slower).
if ros2 pkg prefix compressed_image_transport >/dev/null 2>&1; then
  term "02b - Camera JPEG" "
ros2 run image_transport republish raw compressed --ros-args \\
-r in:=/camera/image \\
-r out/compressed:=/camera/image/compressed \\
-p out.jpeg_quality:=75 \\
-p out.compressed.jpeg_quality:=75
"
  wait_for_publisher /camera/image/compressed 15 "Camera JPEG" || \
    echo "[WARN] Camera JPEG not ready; dashboard falls back to the raw camera"
else
  echo "[WARN] compressed_image_transport missing; dashboard camera will be slow."
  echo "[WARN] Install: sudo apt install ros-humble-image-transport-plugins"
fi

echo "[3/14] Golf Cart Bridge"
term "03 - Golf Cart Bridge" "
ros2 run ros_gz_bridge parameter_bridge \\
'/golf_cart/cmd_vel@geometry_msgs/msg/Twist]gz.msgs.Twist' \\
'/golf_cart/odom@nav_msgs/msg/Odometry[gz.msgs.Odometry'
"
sleep 2

echo "[4/14] YOLO (1280px detection; annotated ROS image disabled)"
term "04 - YOLO Node" "
ros2 run sabeq_ros yolo_node --ros-args \\
-p model_path:='$YOLO_MODEL' \\
-p image_size:=1280 \
-p publish_annotated:=false
"
wait_for_publisher /person_detections 30 "YOLO Node" || exit 1

echo "[5/14] Autonomy"
term "05 - Autonomy Node" "ros2 run sabeq_ros autonomy_node"
sleep 2

echo "[6/14] Golf Cart Node (30 km/h max; governed output)"
term "06 - Golf Cart Node" "
ros2 run sabeq_ros cart_node --ros-args \
-r /selected_route:=/execution/selected_route \
-r /golf_cart/cmd_vel:=/golf_cart/cmd_vel_raw
"
sleep 2

echo "[7/14] ROS <-> Backend Adapter"
term "07 - Backend Adapter" "
python3 '$ADAPTER' --ros-args -p use_sim_time:=true -p lead_m:=30.0
"
sleep 2

echo "[8/14] Crowd Clearance (REAL PX4-driven, non-blocking)"
term "08 - Crowd Clearance" "
python3 '$PROJECT/crowd_clear_node.py' --ros-args \
-p use_sim_time:=true \
-p trigger_ahead_m:=12.0 \
-p move_speed_mps:=2.5 \
-p update_hz:=2.0
"
if wait_for_publisher /crowd_clear/status 20 "Crowd Clearance"; then
  echo "[OK] Crowd Clearance node is alive"
else
  echo "[WARN] Crowd Clearance did not become ready."
  echo "[WARN] Backend and frontend WILL STILL START. Check terminal: 08 - Crowd Clearance"
fi

echo "[9/14] Rescue + automatic return visualizer"
term "09 - Rescue + Return" "
python3 '$PROJECT/rescue_return_node.py' --ros-args \
-p use_sim_time:=true \
-p update_hz:=4.0
"
if wait_for_publisher /rescue/status 15 "Rescue + Return"; then
  echo "[OK] Rescue visualizer ready"
else
  echo "[WARN] Rescue visualizer did not become ready; main mission will still start."
fi

echo "[10/14] FastAPI Backend"
term "10 - FastAPI Backend" "
cd '$BACKEND';
'$BACKEND/.venv/bin/python' -m uvicorn app.main:app --host 0.0.0.0 --port 8000
"

# Do not launch the UI until the backend really passed world validation and opened port 8000.
wait_for_http "http://127.0.0.1:8000/api/system/status" 30 "FastAPI Backend" || {
  echo "[STOP] Check terminal: 10 - FastAPI Backend"
  exit 1
}

echo "[11/14] Vue Frontend"
term "11 - Vue Frontend" "
cd '$FRONTEND';
npm run dev -- --host 0.0.0.0
"
wait_for_http "http://127.0.0.1:5173" 30 "Vue Frontend" || {
  echo "[STOP] Check terminal: 11 - Vue Frontend"
  exit 1
}

echo "[12/14] Creating demo incident"
python3 "$DASH_ROOT/scripts/create_demo_incident.py"

echo "[13/14] Integration ownership"
echo "  Before Launch: Adapter publishes docked drone pose/status."
echo "  After Launch : mission_node v2 publishes REAL PX4 telemetry."
echo "  Escort       : PX4 targets 30 m ahead; cart is hard-gated to remain behind."
echo "  Rescue       : two medics load patient onto stretcher/cart in Gazebo."
echo "  Return       : same selected route reversed; real PX4 leads the cart."

echo "[14/14] Ready"
echo
echo "========================================="
echo " SABEQ V2.7 FINAL DEMO READY"
echo "========================================="
echo "Frontend: http://localhost:5173"
echo "Backend : http://localhost:8000"
echo
echo "Press Launch in the frontend."
echo "Expected mission terminal after Launch:"
echo "  SABEQ Mission Node v2 started"
echo "  REAL PX4 telemetry enabled | escort lead=30.0 m | cruise=12.0 m/s"
echo
echo "Useful live proof after Launch:"
echo "  ros2 topic echo /drone/position"
echo "The field source must be: px4_mavsdk"
