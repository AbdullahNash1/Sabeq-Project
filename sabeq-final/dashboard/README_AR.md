# SABEQ0 Dashboard Integration V2

هذه النسخة مبنية على نسخة الربط الأولى، وتعدّل فقط الأشياء التي ظهرت في الاختبار الحقيقي.

## التعديلات المقصودة فقط

1. **موقع الدرون من PX4 الحقيقي**
   - `mission_node` تنشر `/drone/position` من `MAVSDK position_velocity_ned()`.
   - تنشر الاتجاه والسرعة والارتفاع الحقيقيين.
   - `/drone/status` يحتوي بطارية PX4 الحقيقية عند توفرها.
   - بعد الضغط على Launch، الـAdapter يتوقف عن توليد موقع Drone افتراضي.

2. **الدرون أمام العربة فعلياً**
   - أثناء المرافقة، الدرون تستهدف نقطة تبعد `20 m` أمام موقع العربة الحقيقي القادم من `/golf_cart/odom`.
   - الهدف يتحدث `5 Hz` أثناء حركة العربة.

3. **زيادة سرعة القولف كارت**
   - `MAX_SPEED = 3.0 m/s`، أي تقريباً `10.8 km/h`.
   - لا يتم تغيير منطق الـwaypoints أو التحكم الآخر.

4. **تحسين بث الكاميرا للDashboard**
   - YOLO تعمل فعليًا على `image_size=960` لتخفيف الحمل.
   - صورة YOLO الأصلية في ROS لا يتم تصغيرها بواسطة الـBackend.
   - فقط MJPEG المعروض في الـDashboard يتم تصغيره إلى حد أقصى `800x600`.
   - JPEG quality = `68` وMJPEG max = `12 FPS`.

5. **تصحيح عرض الوقت**
   - ETA المعروض يستخدم `3 m/s + 0.5 s/person` بدلاً من ثابت `7 m/s` القديم.
   - النص صار: `فرق الوقت عن أسرع مسار بديل` حتى يكون `+00:10` واضحاً أنه فرق موجب/تأخير عن الأسرع.
   - توصية Node 2 نفسها لم يتم تغييرها.

6. **تشغيل Backend مضبوط**
   - `setup_once.sh` يضيف `start` و`incident` إلى `routes.yaml` من `world_meta.json` فقط إذا كانا مفقودين.
   - `run_integrated.sh` ينتظر `FastAPI` فعلياً قبل تشغيل Frontend وإنشاء البلاغ.
   - تم إزالة `set -u` حتى لا يتعارض مع `/opt/ros/humble/setup.bash`.

## الملفات التي يتم لمسها داخل ROS workspace

فقط:

- `~/Sabeq0/ros2_ws/src/sabeq_ros/sabeq_ros/mission_node.py`
- `~/Sabeq0/ros2_ws/src/sabeq_ros/sabeq_ros/cart_node.py` — ثابت السرعة فقط
- `~/Sabeq0/ros2_ws/src/sabeq_ros/config/routes.yaml` — `start` و`incident` فقط عند الحاجة

قبل أول تعديل، `setup_once.sh` ينشئ Backup ثابت باسم `.pre_dashboard_v2.bak`.

## التشغيل

```bash
cd ~/Sabeq0/dashboard_v2
bash scripts/setup_once.sh
bash scripts/run_integrated.sh
```

ثم افتح:

```text
http://localhost:5173
```

واضغط **إطلاق الطائرة**.

في Terminal الـMission بعد Launch يجب أن ترى:

```text
SABEQ Mission Node v2 started
REAL PX4 telemetry enabled | escort lead=20.0 m
```

وللتأكد أن الخريطة تأخذ PX4 الحقيقي:

```bash
source /opt/ros/humble/setup.bash
source ~/Sabeq0/ros2_ws/install/setup.bash
ros2 topic echo /drone/position
```

يجب أن يظهر:

```json
"source": "px4_mavsdk"
```

## الرجوع للنسخة السابقة

```bash
bash scripts/restore_pre_v2.sh
```
