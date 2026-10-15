#!/usr/bin/env python3
"""Create one demo incident after the real ROS backend is ready.
Does not launch the mission; the operator presses Launch in the frontend.
"""
import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"


def request(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.loads(r.read().decode() or "null")


for _ in range(60):
    try:
        request("GET", "/api/system/status")
        break
    except Exception:
        time.sleep(1)
else:
    raise SystemExit("Backend did not become ready on :8000")

# A fresh backend has no incident. If one already exists, leave it alone.
try:
    active = request("GET", "/api/incidents?status=active,queued")
except Exception:
    active = []

if active:
    print(f"Incident already exists: {active[0].get('id')}")
    raise SystemExit(0)

body = {
    "severity": "critical",
    "type": "medical_emergency",
    "title": {"en": "Medical emergency", "ar": "حالة طبية طارئة"},
    "location": {
        "zone": "Incident Plaza",
        "description": {"en": "Incident Plaza", "ar": "ساحة الحادث"},
        "x": 500,
        "y": 135,
    },
    "source": "staff_radio",
}

try:
    incident = request("POST", "/api/incidents", body)
except urllib.error.HTTPError as exc:
    print(exc.read().decode())
    raise

print("Demo incident ready:", incident.get("id"), "mission:", incident.get("missionId"))
print("Open http://localhost:5173 and press Launch.")
