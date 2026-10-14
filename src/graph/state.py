from __future__ import annotations

import operator
from typing import Annotated, Optional, TypedDict

from core.models import ExecutionResult, FileBundle, GradeResult, VerificationResult


class GraphState(TypedDict, total=False):
    task: str
    reference: Optional[str]
    reference_uploads: list[tuple[str, bytes]]
    reference_context: str
    max_score: int
    stdin_data: Optional[str]
    uploads: list[tuple[str, bytes]]

    bundle: FileBundle
    context: str
    chunks: list[str]
    execution: ExecutionResult

    analyses: Annotated[list[str], operator.add]
    grade: GradeResult
    grade_errors: list[str]
    retries: int

    result: VerificationResult
    warnings: list[str]


class AnalyzePayload(TypedDict):
    task: str
    reference: Optional[str]
    execution: str
    chunk: str
