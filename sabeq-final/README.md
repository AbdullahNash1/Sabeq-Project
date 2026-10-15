# SABEQ

Emergency drone + golf-cart rescue demo: a PX4 drone in Gazebo Harmonic scans three routes with YOLO,
picks the least crowded one and escorts a golf cart to the incident, with a FastAPI + Vue dashboard.
Arabic notes: [README_FINAL_AR.md](README_FINAL_AR.md).

## Requirements (Ubuntu 22.04)
- ROS 2 Humble (`/opt/ros/humble`) with `ros_gz_bridge`, `vision_msgs`, `cv_bridge` and
  `ros-humble-image-transport-plugins` (JPEG camera stream for the dashboard)
- Gazebo Harmonic (`gz`)
- PX4-Autopilot cloned and built at `~/PX4-Autopilot` (`make px4_sitl gz_x500_mono_cam` must work)
- Python packages: `mavsdk`, `ultralytics`
- Node.js `^22.18` or `>=24.12` with npm, plus `gnome-terminal` and `curl`
- Gazebo Fuel models in the local cache: *Standing person*, *Walking person*, *Casual female*

## Setup (once, and again after every `git pull`)
```bash
git clone https://github.com/AbdullahNash1/sabeq-final.git
cd sabeq-final
bash setup_final.sh
```
This installs everything into `~/Sabeq0` and `~/emergency_drone` and generates the world into PX4.

## Run
```bash
# Recommended once per boot: lets ROS 2 carry the raw camera frames without lag
sudo sysctl -w net.core.rmem_max=2147483647 net.core.rmem_default=67108864 \
  net.ipv4.ipfrag_time=3 net.ipv4.ipfrag_high_thresh=134217728

bash run_final.sh
```
Then open http://localhost:5173 and press **Launch** (إطلاق الطائرة).
