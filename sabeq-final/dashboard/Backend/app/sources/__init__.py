"""Data sources: one per part (camera, route, drone, cart), each mock or ros.

They change mission state only through MissionService hooks and receive
commands through the CommandBus. See base.py for the rules."""
