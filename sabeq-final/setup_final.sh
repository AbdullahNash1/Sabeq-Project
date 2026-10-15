#!/usr/bin/env bash
set -e -o pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$HOME/Sabeq0"
WS="$TARGET/ros2_ws"
SIM_TARGET="$HOME/emergency_drone"
DASH_TARGET="$TARGET/dashboard_final"
PX4="$HOME/PX4-Autopilot"
fail(){ echo "[ERROR] $*" >&2; exit 1; }

[ -f /opt/ros/humble/setup.bash ] || fail "ROS 2 Humble not found"
command -v gz >/dev/null 2>&1 || fail "Gazebo Harmonic command 'gz' not found"
[ -d "$PX4" ] || fail "PX4-Autopilot not found at $PX4"
command -v node >/dev/null 2>&1 || fail "Node.js not found"
command -v npm >/dev/null 2>&1 || fail "npm not found"

mkdir -p "$WS/src"
rm -rf "$WS/src/sabeq_ros"
cp -a "$ROOT/ros2_ws/src/sabeq_ros" "$WS/src/sabeq_ros"

rm -rf "$TARGET/ai"
cp -a "$ROOT/ai" "$TARGET/ai"

rm -rf "$SIM_TARGET"
cp -a "$ROOT/simulation" "$SIM_TARGET"
rm -rf "$SIM_TARGET/generated_reference" 2>/dev/null || true

rm -rf "$DASH_TARGET"
cp -a "$ROOT/dashboard" "$DASH_TARGET"

source /opt/ros/humble/setup.bash

echo "[1/2] Generating expo_demo world into installed PX4..."
python3 "$SIM_TARGET/gen_world.py" || {
  echo "[ERROR] gen_world.py failed. Check Gazebo Fuel models cache."
  exit 1
}

echo "[2/2] Building ROS + Backend + Frontend..."
cd "$DASH_TARGET"
bash scripts/setup_once.sh

echo
echo "========================================="
echo " SABEQ FINAL SETUP COMPLETE"
echo "========================================="
echo "Run: bash '$ROOT/run_final.sh'"
