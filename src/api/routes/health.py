from __future__ import annotations

import httpx
from fastapi import APIRouter, Request

from api.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Проверить состояние сервиса",
    description=(
        "Возвращает статус API, имя используемой LLM-модели, активный бэкенд песочницы "
        "и результат проверки доступности Ollama. Используется для мониторинга и "
        "диагностики окружения."
    ),
    response_description="Текущее состояние сервиса и его зависимостей.",
    operation_id="getHealth",
)
async def health(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    sandbox = request.app.state.service.runner_name

    ollama_available = False
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            ollama_available = resp.status_code == 200
    except httpx.HTTPError:
        ollama_available = False

    return HealthResponse(
        status="ok",
        model=settings.llm_model,
        sandbox_backend=sandbox,
        ollama_available=ollama_available,
    )
