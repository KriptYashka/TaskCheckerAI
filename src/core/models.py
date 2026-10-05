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
    status: ExecutionStatus = ExecutionStatus.SKIPPED
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    matches_reference: Optional[bool] = None
    detail: str = ""


class Deduction(BaseModel):
    points: float = Field(ge=0, description="Сколько баллов снято")
    reason: str = Field(min_length=1, description="За что сняты баллы")


class GradeResult(BaseModel):
    deductions: list[Deduction] = Field(
        default_factory=list,
        description="За что сняты баллы. Сумма points не должна превышать максимальный балл.",
    )
    summary: str = Field(min_length=1, description="Краткое резюме проверки")


class VerificationResult(BaseModel):
    score: float
    max_score: int
    deductions: list[Deduction]
    summary: str
    execution: ExecutionResult
    retries_used: int = 0
    warnings: list[str] = Field(default_factory=list)
