from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from api.app import create_app
from config import Settings
from core.models import GradeResult
from llm.factory import create_chat_model, create_structured_model


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_default_provider_is_ollama():
    model = create_chat_model(make_settings())
    assert isinstance(model, ChatOllama)


def test_openai_provider_uses_api_key_and_base_url():
    settings = make_settings(
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        llm_api_key="sk-test",
        llm_base_url="https://api.example.com/v1",
        llm_temperature=0.2,
        llm_seed=7,
        llm_request_timeout=42,
    )
    model = create_chat_model(settings)
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "gpt-4o-mini"
    assert model.openai_api_key.get_secret_value() == "sk-test"
    assert model.openai_api_base == "https://api.example.com/v1"
    assert model.temperature == 0.2
    assert model.seed == 7
    assert model.request_timeout == 42


def test_openai_provider_requires_api_key():
    settings = make_settings(llm_provider="openai")
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        create_chat_model(settings)


def test_invalid_provider_rejected_by_settings():
    with pytest.raises(ValueError):
        make_settings(llm_provider="unknown")


def test_structured_method_defaults(monkeypatch):
    calls: list[dict] = []
    for cls in (ChatOllama, ChatOpenAI):
        original = cls.with_structured_output

        def spy(self, schema, _original=original, **kwargs):
            calls.append(kwargs)
            return _original(self, schema, **kwargs)

        monkeypatch.setattr(cls, "with_structured_output", spy)

    create_structured_model(make_settings(), GradeResult)
    create_structured_model(
        make_settings(llm_provider="openai", llm_api_key="sk-test"), GradeResult
    )

    assert [call["method"] for call in calls] == ["json_schema", "function_calling"]


def test_structured_method_can_be_overridden(monkeypatch):
    calls: list[dict] = []
    original = ChatOpenAI.with_structured_output

    def spy(self, schema, **kwargs):
        calls.append(kwargs)
        return original(self, schema, **kwargs)

    monkeypatch.setattr(ChatOpenAI, "with_structured_output", spy)
    create_structured_model(
        make_settings(
            llm_provider="openai",
            llm_api_key="sk-test",
            llm_structured_method="json_schema",
        ),
        GradeResult,
    )
    assert calls[0]["method"] == "json_schema"


class FakeResponse:
    status_code = 200


def test_health_reports_active_llm_provider():
    settings = make_settings(
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        llm_api_key="sk-test",
        llm_base_url="https://api.example.com/v1",
    )
    app = create_app(settings)
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as get:
        get.return_value = FakeResponse()
        with TestClient(app) as client:
            body = client.get("/health").json()

    assert body["llm_provider"] == "openai"
    assert body["llm_available"] is True
    assert body["ollama_available"] is False
    assert get.call_args.args[0] == "https://api.example.com/v1/models"
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-test"
