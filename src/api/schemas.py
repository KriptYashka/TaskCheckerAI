from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from core.models import Deduction, ExecutionResult, VerificationResult


class VerifyResponse(VerificationResult):
    pass


class StudentResult(BaseModel):
    student: str
    result: Optional[VerificationResult] = None
    error: Optional[str] = None


class BatchResponse(BaseModel):
    max_score: int
    count: int
    students: list[StudentResult]


class HealthResponse(BaseModel):
    status: str
    model: str
    sandbox_backend: str
    ollama_available: bool


__all__ = [
    "VerifyResponse",
    "StudentResult",
    "BatchResponse",
    "HealthResponse",
    "VerificationResult",
    "Deduction",
    "ExecutionResult",
]
