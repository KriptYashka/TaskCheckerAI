from __future__ import annotations

import httpx
from fastapi import APIRouter, Request

from api.schemas import HealthResponse
from config import Settings

router = APIRouter(tags=["health"])


async def _endpoint_available(url: str, api_key: str = "") -> bool:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url, headers=headers)
            return resp.status_code == 200
    except httpx.HTTPError:
        return False


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Проверить состояние сервиса",
    description=(
        "Возвращает статус API, активного провайдера и имя LLM-модели, бэкенд песочницы "
        "и результат проверки доступности выбранного LLM-эндпоинта. Для Ollama "
        "дополнительно сохраняется поле `ollama_available`."
    ),
    response_description="Текущее состояние сервиса и его зависимостей.",
    operation_id="getHealth",
)
async def health(request: Request) -> HealthResponse:
    settings: Settings = request.app.state.settings
    sandbox = request.app.state.service.runner_name

    if settings.llm_provider == "ollama":
        llm_available = await _endpoint_available(f"{settings.ollama_base_url}/api/tags")
        ollama_available = llm_available
    else:
        llm_available = await _endpoint_available(
            f"{settings.llm_base_url.rstrip('/')}/models", settings.llm_api_key
        )
        ollama_available = False

    return HealthResponse(
        status="ok",
        model=settings.llm_model,
        llm_provider=settings.llm_provider,
        llm_available=llm_available,
        sandbox_backend=sandbox,
        ollama_available=ollama_available,
    )
