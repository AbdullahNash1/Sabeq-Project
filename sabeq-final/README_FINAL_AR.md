# SABEQ — Final Submission

الحزمة تحتوي مشروع سابق نفسه، ولا تحتوي نسخ Gazebo أو ROS 2 أو PX4-Autopilot.

## داخل الحزمة
- `ros2_ws/src/sabeq_ros/`: YOLO + Autonomy + Cart + PX4/MAVSDK Mission + config.
- `simulation/`: `gen_world.py` و `launch_sim.sh` و Expo assets / metadata.
- `ai/`: كود/ملفات AI ووزن YOLO `.pt`.
- `dashboard/`: FastAPI Backend + Vue Frontend + ROS Adapter + integrated scripts.
- `setup_final.sh`: تجهيز المشروع على جهاز جديد.
- `run_final.sh`: تشغيل النظام كامل.
- `MANIFEST_SHA256.txt`: بصمات الملفات.

## Dependencies خارجية غير مضمّنة
- Ubuntu
- ROS 2 Humble
- Gazebo Harmonic + ros_gz_bridge
- PX4-Autopilot في `~/PX4-Autopilot`
- Python / MAVSDK dependencies
- Node.js / npm
- Gazebo Fuel cache

## أول تشغيل
```bash
cd SABEQ_FINAL
bash setup_final.sh
```

## التشغيل الكامل
```bash
bash run_final.sh
```
ثم افتح `http://localhost:5173` واضغط **إطلاق الطائرة**.

## Gazebo Fuel models المطلوبة
`gen_world.py` يتوقع وجود:
- Standing person
- Walking person
- Casual female

Fuel cache لا يتم تضمينه حتى لا تتحول الحزمة إلى نسخة ضخمة من Gazebo assets.

## إضافات V2.5 للعرض النهائي

- اختيار المسار ما زال حقيقيًا من `CENTER ROI + MEDIAN`؛ لم يتم تثبيت مسار معيّن.
- قيمة الوقت الموفر في واجهة العرض ثابتة: `+03:07`.
- تمت إزالة خانة `مشاة تم تنبيههم`.
- بث الكاميرا للواجهة: `800x600` بحد أقصى `12 FPS` وجودة JPEG `68`.
- YOLO تعمل بـ `image_size=960`، وتم تعطيل صورة ROS المشروحة غير المستخدمة لتخفيف الحمل.
- عند وصول العربة للحادث يبدأ تسلسل إنقاذ بصري داخل Gazebo، ثم تعود العربة بنفس المسار والدرون الحقيقية من PX4 تتقدمها.


## تحديث V2.6
- بث الكاميرا بقي حيًا من Gazebo لكنه صار أخف: 640×480 حتى 10 FPS، مع YOLO بحجم 640 وفاصل 0.15 ثانية بين الاستدلالات.
- حركة PX4 أثناء الذهاب والعودة تستخدم هدفًا يتحرك باستمرار على المسار بدل نمط الوقوف والانتظار.
- زر العودة اليدوي أزيل؛ العودة تبدأ تلقائيًا بعد تحميل المريض كما في سيناريو الإنقاذ الحالي.
- الموقع والارتفاع والاتجاه والسرعة والبطارية للدرون ما زالت من MAVSDK/PX4 الحقيقي.

## تحديث V2.7
- رجعت حركة الدرون إلى منطق V2.5 المستقر: الهدف يُحسب فقط على المسار المختار وبناءً على تقدم القولف، بدل هدف V2.6 السريع الذي كان يجعل PX4 تقطع الزوايا وتخرج بصريًا عن المسار.
- YOLO رجعت إلى `image_size=1280` بدون cooldown لرفع اكتشاف الأشخاص.
- تحسين بث الداشبورد الخفيف بقي منفصلًا: 640×480 / 10 FPS / JPEG 62، ولا يغير دقة استدلال YOLO.
- زر العودة اليدوي ما زال محذوفًا، والعودة بعد الإنقاذ تلقائية.
