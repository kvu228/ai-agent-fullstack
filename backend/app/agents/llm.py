"""Model factories. The rest of the code never imports a provider SDK directly."""

from typing import Any

from langchain.chat_models import init_chat_model
from langchain.embeddings import init_embeddings
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from app.core.config import Settings


def _provider_kwargs(model: str, settings: Settings) -> dict[str, Any]:
    provider = model.split(":", 1)[0]
    if provider == "anthropic" and settings.anthropic_api_key:
        return {"api_key": settings.anthropic_api_key.get_secret_value()}
    if provider == "openai" and settings.openai_api_key:
        return {"api_key": settings.openai_api_key.get_secret_value()}
    return {}


def get_chat_model(settings: Settings) -> BaseChatModel:
    """Build a chat model from a "<provider>:<model>" string, e.g. "anthropic:claude-sonnet-5-5".

    Swapping provider is a config change, not a code change.
    """
    model: BaseChatModel = init_chat_model(
        settings.llm_model,
        temperature=settings.llm_temperature,
        **_provider_kwargs(settings.llm_model, settings),
    )
    return model


def get_embeddings(settings: Settings) -> Embeddings:
    """Embedding model from "<provider>:<model>", e.g. "openai:text-embedding-3-small".

    Changing it later means re-ingesting every document: vectors from different
    models are not comparable.
    """
    return init_embeddings(
        settings.embedding_model, **_provider_kwargs(settings.embedding_model, settings)
    )
