import asyncio
import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from app.api.deps import SettingsDep
from app.schemas.health import HealthResponse, ReadinessResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=HealthResponse)
async def liveness(settings: SettingsDep) -> HealthResponse:
    """Is the process alive? Used by the orchestrator to decide on restarts."""
    return HealthResponse(app_name=settings.app_name, environment=settings.environment)


@router.get("/ready", response_model=ReadinessResponse)
async def readiness(request: Request, response: Response) -> ReadinessResponse:
    """Can we serve traffic? Used by the load balancer to route (or not) to this instance."""
    state = request.app.state

    async def database() -> None:
        async with state.session_factory() as session:
            await session.execute(text("SELECT 1"))

    async def storage() -> None:
        await state.storage.ensure_bucket()

    async def vector_store() -> None:
        await asyncio.to_thread(state.chroma_client.heartbeat)

    async def check(name: str, probe: Callable[[], Awaitable[None]]) -> tuple[str, bool]:
        try:
            await asyncio.wait_for(probe(), timeout=3)
            return name, True
        except Exception:
            logger.warning("Readiness check failed: %s", name, exc_info=True)
            return name, False

    results = await asyncio.gather(
        check("database", database), check("storage", storage), check("vector_store", vector_store)
    )
    checks = dict(results)
    healthy = all(checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ok" if healthy else "degraded", checks=checks)
