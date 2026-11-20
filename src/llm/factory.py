from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_ollama import ChatOllama

from config import Settings


def _structured_method(settings: Settings) -> str:
    """Метод structured output с разумным значением по умолчанию для провайдера."""
    if settings.llm_structured_method:
        return settings.llm_structured_method
    # Ollama уверенно поддерживает json_schema. У OpenAI-совместимых API он
    # доступен не везде, поэтому по умолчанию используем function_calling:
    # он не требует специальных указаний о JSON в промпте.
    return "json_schema" if settings.llm_provider == "ollama" else "function_calling"


def create_chat_model(settings: Settings) -> BaseChatModel:
    if settings.llm_provider == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "Для LLM_PROVIDER=openai установите пакет langchain-openai"
            ) from exc

        if not settings.llm_api_key:
            raise ValueError(
                "Не задан LLM_API_KEY: он обязателен при LLM_PROVIDER=openai"
            )

        return ChatOpenAI(
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            temperature=settings.llm_temperature,
            seed=settings.llm_seed,
            request_timeout=settings.llm_request_timeout,
        )

    return ChatOllama(
        model=settings.llm_model,
        base_url=settings.ollama_base_url,
        temperature=settings.llm_temperature,
        seed=settings.llm_seed,
        num_ctx=settings.llm_num_ctx,
        client_kwargs={"timeout": settings.llm_request_timeout},
    )


def create_structured_model(settings: Settings, schema: type) -> Runnable:
    model = create_chat_model(settings)
    return model.with_structured_output(schema, method=_structured_method(settings))
