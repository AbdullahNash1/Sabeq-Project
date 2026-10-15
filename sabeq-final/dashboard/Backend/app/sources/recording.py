"""RecordingSink: keeps every command in ROS wire format (tests, debugging)."""
from app.ros.messages import to_ros
from app.sources.commands import Command


class RecordingSink:
    def __init__(self) -> None:
        self.sent: list = []  # (topic, message dict)

    def handle(self, cmd: Command) -> None:
        self.sent.append(to_ros(cmd))

    def on(self, topic: str) -> list:
        return [m for t, m in self.sent if t == topic]
