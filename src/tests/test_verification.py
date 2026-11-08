from __future__ import annotations

import asyncio

import pytest

from config import Settings
from core.models import Deduction, ExecutionResult, ExecutionStatus, VerificationResult
from services.verification import VerificationService


class FakeVerificationService(VerificationService):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def verify(self, task, uploads, **kwargs):
        if uploads[0][0].startswith("bad"):
            raise RuntimeError("LLM failed")
        return VerificationResult(
            score=10,
            max_score=kwargs.get("max_score") or self.settings.default_max_score,
            deductions=[Deduction(points=0, reason="ok")],
            summary="ok",
            execution=ExecutionResult(status=ExecutionStatus.SKIPPED, detail="stub"),
        )


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, sandbox_enabled=False, **overrides)


def test_batch_isolates_student_failures():
    service = FakeVerificationService(make_settings())
    results = asyncio.run(
        service.verify_batch(
            task="t",
            submissions=[("good.py", b"print(1)"), ("bad.py", b"print(2)")],
            max_score=10,
        )
    )
    by_student = {student: (result, error) for student, result, error in results}
    assert by_student["good"][0] is not None
    assert by_student["good"][1] is None
    assert by_student["bad"][0] is None
    assert "Не удалось получить результат" in by_student["bad"][1]


def test_initial_state_rejects_zero_max_score():
    service = FakeVerificationService(make_settings())
    with pytest.raises(ValueError):
        service._initial_state("t", [("main.py", b"x")], None, 0, None)


def test_initial_state_uses_default_max_score():
    service = FakeVerificationService(make_settings(default_max_score=42))
    state = service._initial_state("t", [("main.py", b"x")], None, None, None)
    assert state["max_score"] == 42


def test_service_does_not_keep_memory_checkpointer():
    service = VerificationService(make_settings())
    assert service.graph.checkpointer is None
