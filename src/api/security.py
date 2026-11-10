from __future__ import annotations

import secrets
import threading
import time
from collections import deque
from typing import Optional

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description=(
        "API-ключ. Проверяется только если на сервере задан `API_KEY`. "
        "Передаётся в заголовке `X-API-Key`."
    ),
)


async def require_api_key(
    request: Request,
    api_key: Optional[str] = Security(api_key_header),
) -> None:
    configured = (request.app.state.settings.api_key or "").strip()
    if not configured:
        return
    if not api_key or not secrets.compare_digest(
        api_key.encode("utf-8"), configured.encode("utf-8")
    ):
        raise HTTPException(
            status_code=401,
            detail="Неверный или отсутствующий API-ключ",
            headers={"WWW-Authenticate": "ApiKey"},
        )


class RateLimiter:
    """Простой in-memory rate limiter по скользящему окну.

    Подходит для одного процесса. Для нескольких реплик нужен общий store
    (Redis) или ограничение на уровне reverse proxy.
    """

    def __init__(self, window_seconds: float = 60.0) -> None:
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit: int) -> Optional[float]:
        """Возвращает Retry-After в секундах или None, если запрос разрешён."""

        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            events = self._events.setdefault(key, deque())
            while events and events[0] <= cutoff:
                events.popleft()

            if len(events) >= limit:
                return max(0.0, events[0] + self.window_seconds - now)

            events.append(now)
            if len(self._events) > 10_000:
                self._prune(cutoff)
            return None

    def _prune(self, cutoff: float) -> None:
        stale = [key for key, events in self._events.items() if not events or events[-1] <= cutoff]
        for key in stale:
            del self._events[key]
