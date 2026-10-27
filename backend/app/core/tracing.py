"""LangSmith tracing setup."""

import logging
import os

from app.core.config import Settings

logger = logging.getLogger(__name__)


def configure_tracing(settings: Settings) -> None:
    """Copy the LangSmith settings into os.environ, the only place LangSmith reads them.

    pydantic-settings loads `.env` into `Settings` but never into os.environ, so
    `LANGSMITH_*` lines in `.env` alone do nothing. Call this once at startup.
    """
    api_key = settings.langsmith_api_key
    if not (settings.langsmith_tracing and api_key):
        if settings.langsmith_tracing:
            logger.warning("LANGSMITH_TRACING is on but LANGSMITH_API_KEY is empty")
        os.environ["LANGSMITH_TRACING"] = "false"
        return

    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = api_key.get_secret_value()
    # Empty values in `.env` (e.g. `LANGSMITH_ENDPOINT=`) mean "use LangSmith's default".
    if settings.langsmith_endpoint:
        os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    if settings.langsmith_project:
        os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    logger.info("LangSmith tracing on (project %r)", settings.langsmith_project)
