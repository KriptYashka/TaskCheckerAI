from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.models import Deduction, ExecutionResult, VerificationResult


class VerifyResponse(VerificationResult):
    """Результат проверки одной работы."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "score": 8.0,
                    "max_score": 10,
                    "deductions": [
                        {"points": 2, "reason": "не обработан пустой ввод"},
                    ],
                    "summary": "Решение в целом верное, замечания по надёжности.",
                    "execution": {
                        "status": "ok",
                        "exit_code": 0,
                        "stdout": "7\n",
                        "stderr": "",
                        "matches_reference": True,
                        "detail": "Docker-песочница",
                    },
                    "retries_used": 0,
                    "warnings": [],
                }
            ]
        }
    )


class ErrorResponse(BaseModel):
    """Ошибка выполнения запроса."""

    detail: str | list[dict[str, Any]] = Field(
        description=(
            "Текст ошибки, пригодный для показа пользователю. Для ошибок валидации "
            "FastAPI (`422`) здесь может быть список объектов с полями `type`, `loc`, "
            "`msg` и `input`."
        ),
        examples=["Нужно передать текст задачи или эталонное решение (файлы/ноутбук)"],
    )


class StudentResult(BaseModel):
    """Результат проверки работы одного ученика в пакетном запросе."""

    student: str = Field(
        description=(
            "Идентификатор ученика. Определяется по имени файла, префиксу до `__` "
            "или имени папки."
        ),
        examples=["ivanov"],
    )
    result: Optional[VerificationResult] = Field(
        None,
        description="Результат проверки. `null`, если работу не удалось разобрать.",
    )
    error: Optional[str] = Field(
        None,
        description="Текст ошибки разбора работы. `null`, если проверка выполнена.",
        examples=[None],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "student": "ivanov",
                    "result": {
                        "score": 7.0,
                        "max_score": 10,
                        "deductions": [{"points": 3, "reason": "неполное решение"}],
                        "summary": "Не все ветки алгоритма обработаны.",
                        "execution": {"status": "skipped", "detail": "stub"},
                        "retries_used": 0,
                        "warnings": [],
                    },
                    "error": None,
                },
                {
                    "student": "petrov",
                    "result": None,
                    "error": "Архив повреждён или имеет неподдерживаемый формат",
                },
            ]
        }
    )


class BatchResponse(BaseModel):
    """Результаты пакетной проверки работ."""

    max_score: int = Field(
        description="Максимальный балл, применённый ко всем работам пакета.",
        examples=[10],
    )
    count: int = Field(
        description="Количество работ в ответе.",
        examples=[2],
    )
    students: list[StudentResult] = Field(
        description="Результаты по каждому ученику.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "max_score": 10,
                    "count": 2,
                    "students": [
                        {
                            "student": "ivanov",
                            "result": {
                                "score": 7.0,
                                "max_score": 10,
                                "deductions": [
                                    {"points": 3, "reason": "неполное решение"}
                                ],
                                "summary": "Не все ветки алгоритма обработаны.",
                                "execution": {
                                    "status": "skipped",
                                    "detail": "stub",
                                },
                                "retries_used": 0,
                                "warnings": [],
                            },
                            "error": None,
                        },
                        {
                            "student": "petrov",
                            "result": None,
                            "error": "Архив повреждён или имеет неподдерживаемый формат",
                        },
                    ],
                }
            ]
        }
    )


class HealthResponse(BaseModel):
    """Состояние сервиса и его зависимостей."""

    status: str = Field(
        description="Статус API. `ok`, если приложение запущено.",
        examples=["ok"],
    )
    model: str = Field(
        description="Имя LLM-модели, используемой для проверки.",
        examples=["qwen2.5-coder:7b"],
    )
    sandbox_backend: str = Field(
        description="Активный бэкенд песочницы: `docker`, `local` или `off`.",
        examples=["local"],
    )
    ollama_available: bool = Field(
        description="Доступен ли сервер Ollama по адресу `OLLAMA_BASE_URL`.",
        examples=[True],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "status": "ok",
                    "model": "qwen2.5-coder:7b",
                    "sandbox_backend": "local",
                    "ollama_available": True,
                }
            ]
        }
    )


__all__ = [
    "VerifyResponse",
    "ErrorResponse",
    "StudentResult",
    "BatchResponse",
    "HealthResponse",
    "VerificationResult",
    "Deduction",
    "ExecutionResult",
]
