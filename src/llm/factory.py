from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_ollama import ChatOllama

from config import Settings


def create_chat_model(settings: Settings) -> BaseChatModel:
    return ChatOllama(
        model=settings.llm_model,
        base_url=settings.ollama_base_url,
        temperature=settings.llm_temperature,
        num_ctx=settings.llm_num_ctx,
        client_kwargs={"timeout": settings.llm_request_timeout},
    )


def create_structured_model(settings: Settings, schema: type) -> Runnable:
    model = create_chat_model(settings)
    return model.with_structured_output(schema, method="json_schema")
