from __future__ import annotations

import math
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from api.routes.health import router as health_router
from api.routes.verify import router as verify_router
from api.security import RateLimiter, require_api_key
from config import Settings, get_settings
from services.verification import VerificationService

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

API_DESCRIPTION = """\
Сервис автоматической проверки учебных решений по информатике.

Принимает файлы, архивы, ноутбуки `.ipynb` или исходный код текстом, по возможности
запускает решение в песочнице, анализирует код с помощью LLM (Ollama) и возвращает
итоговый балл со списком снятых баллов.

### Как проверить API

1. Откройте `POST /api/v1/verify` (одна работа) или `POST /api/v1/verify-batch`
   (пакет работ) и нажмите **Try it out**.
2. Загрузите файлы решения в поле `files`/`submissions` или передайте код текстом
   в поле `texts` (для одной работы).
3. Передайте `task` (условие) или `reference_files` (эталонное решение) — хотя бы одно.
4. Нажмите **Execute** и посмотрите ответ.

Потоковые варианты `/api/v1/verify/stream` и `/api/v1/verify-batch/stream`
возвращают Server-Sent Events (SSE) с прогрессом проверки.

Спецификация OpenAPI доступна по `/openapi.json`, альтернативная документация
ReDoc — по `/redoc`. Инструкция по экспорту спецификации — в README.
"""

OPENAPI_TAGS = [
    {
        "name": "verification",
        "description": (
            "Проверка работ: одна работа, пакет работ и потоковые SSE-варианты. "
            "Все эндпоинты принимают `multipart/form-data`."
        ),
    },
    {
        "name": "health",
        "description": "Проверка состояния сервиса, модели и песочницы.",
    },
]

SWAGGER_UI_PARAMETERS = {
    "filter": True,
    "displayRequestDuration": True,
    "tryItOutEnabled": True,
    "docExpansion": "list",
    "defaultModelsExpandDepth": 1,
}


BODY_SIZE_OVERHEAD = 1024 * 1024


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Отсекает заведомо слишком большие multipart-запросы до разбора."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/"):
            settings = request.app.state.settings
            limit = settings.max_total_bytes + BODY_SIZE_OVERHEAD
            raw_length = request.headers.get("content-length")
            if raw_length:
                try:
                    length = int(raw_length)
                except ValueError:
                    length = 0
                if length > limit:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"Размер запроса превышает лимит {limit} байт"},
                    )
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = request.app.state.settings
        limit = settings.rate_limit_per_minute
        if limit > 0 and request.url.path.startswith("/api/"):
            client = request.client.host if request.client else "unknown"
            retry_after = request.app.state.rate_limiter.check(client, limit)
            if retry_after is not None:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Слишком много запросов. Попробуйте позже."},
                    headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
                )
        return await call_next(request)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.service = VerificationService(settings)
        yield

    app = FastAPI(
        title="TaskCheckerAI",
        summary="Проверка учебных решений по информатике с помощью LLM",
        description=API_DESCRIPTION,
        version="0.1.0",
        contact={
            "name": "TaskCheckerAI",
            "url": "https://github.com/KriptYashka/TaskCheckerAI",
        },
        openapi_tags=OPENAPI_TAGS,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        swagger_ui_parameters=SWAGGER_UI_PARAMETERS,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.rate_limiter = RateLimiter()
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestSizeLimitMiddleware)

    app.include_router(health_router)
    app.include_router(verify_router, dependencies=[Depends(require_api_key)])

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app
