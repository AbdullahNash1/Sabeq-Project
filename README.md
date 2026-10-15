# SABEQ — Final Demo

Autonomous drone system for crowd-aware route selection and emergency vehicle support.

---

## Requirements

Before running SABEQ, make sure the following are already installed:

- **Ubuntu 22.04**
- **ROS 2 Humble** (`/opt/ros/humble`)
  - `ros_gz_bridge`
  - `vision_msgs`
  - `cv_bridge`
  - `ros-humble-image-transport-plugins`
- **Gazebo Harmonic** (`gz`)
- **PX4-Autopilot** cloned and built at:

```bash
~/PX4-Autopilot
```

The following PX4 command should work:

```bash
cd ~/PX4-Autopilot
make px4_sitl gz_x500_mono_cam
```

- **Python 3** with:
  - `mavsdk`
  - `ultralytics`
- **Node.js 22.18+** with `npm`
- `gnome-terminal`
- `curl`

Gazebo Fuel models should also be available in the local cache:

- Standing person
- Walking person
- Casual female

---

## 1. Extract the ZIP

Extract the SABEQ ZIP file anywhere on the machine.

Example:

```bash
cd ~/Downloads
unzip sabeq-final.zip
cd sabeq-final
```

> If the extracted folder has a different name, enter that folder instead.

---

## 2. Setup

Run this once after extracting the project:

```bash
bash setup_final.sh
```

The setup script prepares the project under:

```bash
~/Sabeq0
~/emergency_drone
```

and generates the required Gazebo world for PX4.

Wait until the setup finishes successfully before continuing.

---

## 3. Run SABEQ

From the extracted project folder:

```bash
bash run_final.sh
```

Wait for all required terminals and services to start.

---

## 4. Open the Dashboard

Open this address

```text
http://localhost:5173
```

Then press:

**Launch — إطلاق الطائرة**

The demo will start the autonomous mission.

---

## Quick Start

```bash
bash setup_final.sh
bash run_final.sh
```

Then open:

```text
http://localhost:5173
```

and press **Launch**.

---

## Notes

- `setup_final.sh` is normally required only once after extracting a new project version.
- `run_final.sh` is used each time you want to start the demo.
- Keep `~/PX4-Autopilot` in its expected location.
- For the best demo experience, use Google Chrome for the dashboard.
