from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Liveness: the process is up. Cheap, no dependency checks."""

    status: Literal["ok"] = "ok"
    app_name: str
    environment: str


class ReadinessResponse(BaseModel):
    """Readiness: can we serve traffic? Checks every backing service."""

    status: Literal["ok", "degraded"]
    checks: dict[str, bool]
