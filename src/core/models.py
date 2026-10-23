from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class FileBundle(BaseModel):
    files: list["SourceFile"] = Field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)


class SourceFile(BaseModel):
    path: str
    size: int
    encoding: str = "utf-8"
    content: Optional[str] = None
    is_binary: bool = False
    is_truncated: bool = False

    @property
    def language(self) -> str:
        ext = self.path.rsplit(".", 1)[-1].lower() if "." in self.path else ""
        return {
            "py": "python",
            "cpp": "c++",
            "cc": "c++",
            "cxx": "c++",
            "c": "c",
            "java": "java",
            "js": "javascript",
            "ts": "typescript",
            "kt": "kotlin",
            "go": "go",
            "rs": "rust",
            "pas": "pascal",
            "cs": "c#",
            "php": "php",
            "rb": "ruby",
            "sql": "sql",
            "txt": "text",
            "md": "text",
            "ipynb": "notebook",
        }.get(ext, ext or "unknown")


class ExecutionStatus(str, Enum):
    OK = "ok"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"
    ERROR = "error"


class ExecutionResult(BaseModel):
    status: ExecutionStatus = Field(
        default=ExecutionStatus.SKIPPED,
        description=(
            "Статус запуска: `ok` — успешно, `failed` — ненулевой код возврата, "
            "`timeout` — превышен таймаут, `skipped` — запуск не выполнялся, "
            "`error` — ошибка запуска."
        ),
        examples=[ExecutionStatus.OK],
    )
    exit_code: Optional[int] = Field(
        None,
        description="Код возврата процесса. `null`, если запуск не выполнялся.",
        examples=[0],
    )
    stdout: str = Field(
        "",
        description="Стандартный вывод программы (может быть обрезан по лимиту).",
        examples=["7\n"],
    )
    stderr: str = Field(
        "",
        description="Стандартный поток ошибок программы.",
        examples=[""],
    )
    matches_reference: Optional[bool] = Field(
        None,
        description="Совпал ли вывод с эталоном. `null`, если сравнение не выполнялось.",
        examples=[True],
    )
    detail: str = Field(
        "",
        description="Пояснение к запуску: бэкенд песочницы, причина пропуска или ошибки.",
        examples=["Локальный запуск без изоляции (Docker недоступен)"],
    )


class Deduction(BaseModel):
    points: float = Field(
        ge=0,
        description="Сколько баллов снято",
        examples=[2.0],
    )
    reason: str = Field(
        min_length=1,
        description="За что сняты баллы",
        examples=["не обработан пустой ввод"],
    )


class GradeResult(BaseModel):
    deductions: list[Deduction] = Field(
        default_factory=list,
        description="За что сняты баллы. Сумма points не должна превышать максимальный балл.",
    )
    summary: str = Field(min_length=1, description="Краткое резюме проверки")


class VerificationResult(BaseModel):
    score: float = Field(
        description="Итоговый балл: `max_score - Σ deductions.points`.",
        examples=[8.0],
    )
    max_score: int = Field(
        description="Максимально возможный балл для этой работы.",
        examples=[10],
    )
    deductions: list[Deduction] = Field(
        description="Список снятых баллов с причинами.",
    )
    summary: str = Field(
        description="Краткое резюме проверки.",
        examples=["Решение в целом верное, есть замечания по надёжности."],
    )
    execution: ExecutionResult = Field(
        description="Результат запуска решения в песочнице.",
    )
    retries_used: int = Field(
        0,
        description="Сколько раз оценка запрашивалась повторно из-за несогласованности.",
        examples=[0],
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Предупреждения валидации и ограничения контекста.",
    )
