#!/usr/bin/env bash
set -e -o pipefail

WS="$HOME/Sabeq0/ros2_ws"
PKG="$WS/src/sabeq_ros"
MISSION="$PKG/sabeq_ros/mission_node.py"
CART="$PKG/sabeq_ros/cart_node.py"
ROUTES="$PKG/config/routes.yaml"

restore_one() {
  local dst="$1"
  local bak="$2"
  if [ -f "$bak" ]; then
    cp "$bak" "$dst"
    echo "[RESTORED] $dst"
  else
    echo "[SKIP] Backup not found: $bak"
  fi
}

restore_one "$MISSION" "$MISSION.pre_dashboard_v22.bak"
restore_one "$CART" "$CART.pre_dashboard_v22.bak"
restore_one "$ROUTES" "$ROUTES.pre_dashboard_v22.bak"

source /opt/ros/humble/setup.bash
cd "$WS"
colcon build --packages-select sabeq_ros --symlink-install

echo "Pre-v2 ROS files restored."
