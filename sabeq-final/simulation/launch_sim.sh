#!/usr/bin/env bash
# Launch the expo_demo world, then PX4 once the world is fully loaded.
# Avoids PX4's "Service call timed out" on heavy worlds.
#
# Usage:  ~/emergency_drone/launch_sim.sh
# Stop:   type `shutdown` at pxh>  (Gazebo is closed automatically)

WORLD=expo_demo
PX4_DIR="$HOME/PX4-Autopilot"
WORLD_FILE="$PX4_DIR/Tools/simulation/gz/worlds/$WORLD.sdf"
DRONE_POSE="0,-0.1,2.28,0,0,0"   # on the golf cart roof, facing east (printed by gen_world.py)

# Clean up anything left from a previous run
pkill -f "gz sim" 2>/dev/null
pkill -x px4 2>/dev/null
sleep 1

if [ ! -f "$WORLD_FILE" ]; then
  echo "World file not found: $WORLD_FILE"
  echo "Run: python3 ~/emergency_drone/gen_world.py"
  exit 1
fi

# Same environment PX4 sets when it starts Gazebo itself
export GZ_SIM_RESOURCE_PATH="$PX4_DIR/Tools/simulation/gz/models:$PX4_DIR/Tools/simulation/gz/worlds:$GZ_SIM_RESOURCE_PATH"
export GZ_SIM_SYSTEM_PLUGIN_PATH="$PX4_DIR/build/px4_sitl_default/src/modules/simulation/gz_plugins:$GZ_SIM_SYSTEM_PLUGIN_PATH"
export GZ_SIM_SERVER_CONFIG_PATH="$PX4_DIR/Tools/simulation/gz/server.config"

echo "[1/3] Starting Gazebo with $WORLD ..."
gz sim -r "$WORLD_FILE" > /tmp/gz_sim.log 2>&1 &
GZ_PID=$!
trap 'echo "Closing Gazebo..."; kill $GZ_PID 2>/dev/null; pkill -f "gz sim" 2>/dev/null' EXIT

echo "[2/3] Waiting for the world to finish loading (can take a minute) ..."
for i in $(seq 1 180); do
  if gz service -l 2>/dev/null | grep -q "/world/$WORLD/create"; then
    break
  fi
  if ! kill -0 $GZ_PID 2>/dev/null; then
    echo "Gazebo exited. Last lines of /tmp/gz_sim.log:"
    tail -20 /tmp/gz_sim.log
    exit 1
  fi
  sleep 1
done
sleep 5   # let models finish spawning
echo "      World ready after ~$i s."

echo "[3/3] Starting PX4 (drone spawns on the golf cart) ..."
cd "$PX4_DIR" && \
PX4_GZ_STANDALONE=1 PX4_GZ_WORLD=$WORLD PX4_GZ_MODEL_POSE="$DRONE_POSE" \
  make px4_sitl gz_x500_mono_cam
