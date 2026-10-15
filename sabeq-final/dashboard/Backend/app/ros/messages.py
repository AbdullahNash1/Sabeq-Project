"""Typed commands -> ROS std_msgs/String JSON (ROS_TOPICS.md §4.11, §4.12, §4.15).

simTimeS is None here; the rclpy publisher fills it from the ROS clock.
"""
from app.sources.commands import Command, IncidentCommand, MissionCommand, PayloadCommand

MISSION_COMMAND = "/mission/command"
PAYLOAD_CMD = "/drone/payload_cmd"
INCIDENT = "/incident"


def to_ros(cmd: Command) -> tuple:
    """Return (topic, message dict)."""
    if isinstance(cmd, MissionCommand):
        return MISSION_COMMAND, {
            "commandId": cmd.command_id,
            "missionId": cmd.mission_id,
            "simTimeS": None,
            "command": cmd.command,
            "route": cmd.route,
            "source": cmd.source,
            "polylineLocalM": cmd.polyline_local,
            "scanAltitudeM": cmd.scan_altitude_m,
            "reason": cmd.reason,
        }
    if isinstance(cmd, PayloadCommand):
        return PAYLOAD_CMD, {
            "missionId": cmd.mission_id, "simTimeS": None, "speaker": cmd.speaker, "led": cmd.led,
        }
    if isinstance(cmd, IncidentCommand):
        if not cmd.active:
            return INCIDENT, {"active": False, "missionId": None, "simTimeS": None}
        return INCIDENT, {
            "active": True,
            "missionId": cmd.mission_id,
            "incidentId": cmd.incident_id,
            "simTimeS": None,
            "severity": cmd.severity,
            "type": cmd.type,
            "zone": cmd.zone,
            "localM": {"e": cmd.local_m[0], "n": cmd.local_m[1]} if cmd.local_m else None,
            "scanAltitudeM": cmd.scan_altitude_m,
        }
    raise TypeError(f"unknown command {type(cmd).__name__}")
