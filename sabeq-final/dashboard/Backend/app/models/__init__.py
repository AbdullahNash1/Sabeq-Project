"""Pydantic models for every data model in API_CONTRACT.md (camelCase on the wire)."""
from .base import ApiModel, RequestModel
from .camera import Box, CameraDetections, CameraInfo, ImageSize
from .cart import Cart
from .common import LocalizedText, Position, RouteId
from .drone import Detector, Drone, DronePayload
from .errors import ErrorBody, ErrorEnvelope
from .incident import Incident, IncidentLocation, IncidentType
from .mission import (
    EscortInfo,
    Mission,
    MissionError,
    MissionResult,
    MissionSummary,
    PayloadState,
    RescueInfo,
    ScanInfo,
    TimelineEntry,
)
from .requests import (
    AbortRequest,
    CloseRequest,
    CreateIncident,
    CreateIncidentLocation,
    EmptyRequest,
    PayloadRequest,
    SelectRouteRequest,
)
from .route import Route
from .site import Site, SiteMap, ViewBox
from .snapshot import OpenIncidents, Snapshot
from .system import BackendStatus, JetsonStatus, RosStatus, SystemStatus

__all__ = [
    "AbortRequest", "ApiModel", "BackendStatus", "Box", "CameraDetections", "CameraInfo",
    "Cart", "CloseRequest", "CreateIncident", "CreateIncidentLocation", "Detector", "Drone",
    "DronePayload", "EmptyRequest", "ErrorBody", "ErrorEnvelope", "EscortInfo", "ImageSize",
    "Incident", "IncidentLocation", "IncidentType", "JetsonStatus", "LocalizedText", "Mission",
    "MissionError", "MissionResult", "MissionSummary", "OpenIncidents", "PayloadRequest",
    "PayloadState", "Position", "RequestModel", "RescueInfo", "RosStatus", "Route", "RouteId", "ScanInfo",
    "SelectRouteRequest", "Site", "SiteMap", "Snapshot", "SystemStatus", "TimelineEntry",
    "ViewBox",
]
