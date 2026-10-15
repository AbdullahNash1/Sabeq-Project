# Sabeq Backend

FastAPI backend for the Sabeq dashboard. The API is in [`API_CONTRACT.md`](./API_CONTRACT.md), the ROS topics are in [`ROS_TOPICS.md`](./ROS_TOPICS.md), and the code layout and rules are in [`DEVELOPMENT.md`](./DEVELOPMENT.md).

Each part (camera, route, drone, cart) runs on `mock` or `ros`, set by `SOURCE_CAMERA`, `SOURCE_ROUTE`, `SOURCE_DRONE` and `SOURCE_CART`. The default is mock, which needs no ROS at all.

## Mock (any machine)

```sh
python3.10 -m venv .venv
.venv/bin/pip install -r requirements.txt
DEV_MODE=true MOCK_SPEED=5 .venv/bin/uvicorn app.main:app --port 8000
.venv/bin/python -m pytest -q          # no ROS needed, including the ROS co-simulation
```

## With ROS 2 (Linux, ROS 2 Humble, Python 3.10)

**For the full system (simulation, nodes, backend and dashboard), use [`linux/LINUX_SETUP.md`](../linux/LINUX_SETUP.md) and the terminal scripts in `linux/run/`.** This section is the backend part only.

`rclpy`, `sensor_msgs`, `std_msgs` and `vision_msgs` come from the ROS apt packages, not from pip. The venv must be able to see them.

```sh
source /opt/ros/humble/setup.bash               # in every terminal that runs the backend
cd Backend
python3 -m venv .venv --system-site-packages    # must see rclpy from /opt/ros/humble
.venv/bin/pip install -r requirements.txt
.venv/bin/python -c "import rclpy, vision_msgs.msg; print('ROS ok')"

SOURCE_CAMERA=ros SOURCE_ROUTE=ros SOURCE_DRONE=ros SOURCE_CART=ros \
  .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Start the simulation and the nodes first: Gazebo + `ros_gz_bridge`, then `ros2 launch sabeq_mission sabeq.launch.py`, which starts mission_node, world_node and Node 2 (add `model_path:=…/yolov8s.pt` for Node 1; see `ros_ws/src/sabeq_mission/README.md`). Then start the backend and the frontend (`Frontend/README.md`).

- The backend runs one ROS node, **`backend_bridge`**, with `use_sim_time` on (`SABEQ_ROS_USE_SIM_TIME`), so the `simTimeS` in its commands is Gazebo's clock.
- Parts can be mixed. For example, `SOURCE_CAMERA=ros` alone streams the Gazebo camera and YOLO boxes on top of a mock mission. The backend only waits for acks from the nodes of the parts that are `ros`: mission_node for the drone, world_node for the cart.
- Operator commands wait up to 3 s for the nodes' `command_ack` (`SABEQ_COMMAND_ACK_TIMEOUT_S`). A refusal shows as a "Command rejected by ROS" toast with the node's reason (for example `patient_not_loaded`). No reply shows as "No reply from ROS".
- With `DEV_MODE=true` and `SOURCE_CART=ros`, **Reset sim** also puts world_node's cart, crowd, medics and patient back to their start poses.
- If ROS isn't sourced, startup fails with a `RosUnavailable` error that says what to run.

Quick checks on the ROS machine:

```sh
ros2 node list | grep backend_bridge
ros2 topic echo /mission/command           # one message per operator command
ros2 topic echo /mission_events            # command_ack from mission_node and world_node
```
