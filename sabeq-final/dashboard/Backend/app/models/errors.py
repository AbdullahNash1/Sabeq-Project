from typing import Any

from pydantic import Field

from .base import ApiModel


class ErrorBody(ApiModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(ApiModel):
    """Every non-2xx response (§1.1)."""

    error: ErrorBody
