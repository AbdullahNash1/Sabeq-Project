# سابق — V2.2 High Speed

هذه النسخة فوق V2.1 وتعدل فقط المطلوب للديمو:

- القولف كارت حتى 30 كم/س.
- الدرون تطلب من PX4 سرعة مرافقة تقريباً 43 كم/س وتستهدف 30 متر أمام العربة.
- العربة ممنوع تتجاوز الدرون: إذا صار التقدم أقل من 18 متر تتوقف، وترجع تتحرك عند 25 متر.
- البيانات المعروضة للدرون تبقى من PX4 / MAVSDK الحقيقي.
- حساب وقت المسار صار متوافق مع السرعة الجديدة ويعطي وزن أكبر للازدحام.
- إذا A و C تعادلوا تماماً في الوقت المقدر، تكون أولوية التعادل لـ C_east.
- بطاقة الوصول تعرض الوقت الموفر عن أفضل بديل: + يعني وفرنا وقتاً.

## التشغيل

أوقف النسخة القديمة أولاً، ثم:

```bash
mkdir -p ~/Sabeq0/dashboard_v2_2
unzip -o ~/Downloads/SABEQ0_Dashboard_Integration_V2_2_HIGH_SPEED.zip -d ~/Sabeq0/dashboard_v2_2
cd ~/Sabeq0/dashboard_v2_2
bash scripts/setup_once.sh
bash scripts/run_integrated.sh
```

بعد ظهور READY افتح:

`http://localhost:5173`

واضغط إطلاق الطائرة.

## أهم Logs متوقعة

Mission node:

- `REAL PX4 telemetry enabled | escort lead=30.0 m | cruise=12.0 m/s`
- `PX4 MPC_XY_CRUISE=12.0 m/s`
- `PX4 MPC_XY_VEL_MAX=15.0 m/s`

Adapter:

- `Cart released: real PX4 drone lead=... m`
- وإذا اقتربت العربة زيادة: `Cart paused: drone lead dropped to ... m`

هذا التوقف حماية متعمدة لضمان أن الدرون تبقى أمام القولف كارت.
