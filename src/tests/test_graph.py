from __future__ import annotations

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from config import Settings
from core.models import Deduction, ExecutionResult, ExecutionStatus, GradeResult
from graph.builder import build_graph


class DummyRunner:
    name = "dummy"

    def is_available(self) -> bool:
        return True

    def run(self, bundle, entrypoint, stdin_data, reference):
        return ExecutionResult(status=ExecutionStatus.SKIPPED, detail="dummy")


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def base_state(max_score: int = 10) -> dict:
    return {
        "task": "сложить два числа",
        "reference": "3",
        "max_score": max_score,
        "uploads": [("main.py", b"a=1\nb=2\nprint(a+b)")],
        "analyses": [],
        "grade_errors": [],
        "retries": 0,
        "warnings": [],
    }


def test_graph_produces_result_with_fake_models():
    settings = make_settings(max_context_chars=500, max_chunk_chars=200)
    analyze_model = RunnableLambda(lambda _: AIMessage(content="анализ: код рабочий"))
    grade_model = RunnableLambda(
        lambda _: GradeResult(
            deductions=[Deduction(points=2, reason="нет обработки пустого ввода")],
            summary="В целом верно",
        )
    )
    graph = build_graph(settings, analyze_model, grade_model, DummyRunner())
    final = graph.invoke(base_state())
    result = final["result"]
    assert result.score == 8
    assert result.max_score == 10
    assert len(result.deductions) == 1


def test_graph_retries_when_grade_invalid_then_fixes():
    settings = make_settings(max_context_chars=500, max_chunk_chars=150)
    calls = {"n": 0}

    def grade_fn(_):
        calls["n"] += 1
        if calls["n"] == 1:
            return GradeResult(
                deductions=[Deduction(points=99, reason="слишком много")], summary="bad"
            )
        return GradeResult(deductions=[], summary="good")

    analyze_model = RunnableLambda(lambda _: AIMessage(content="ok"))
    graph = build_graph(settings, analyze_model, RunnableLambda(grade_fn), DummyRunner())
    final = graph.invoke(base_state())
    assert final["result"].score == 10
    assert calls["n"] == 2
    assert final["result"].retries_used == 1


def test_graph_sanitizes_after_max_retries():
    settings = make_settings(max_context_chars=500, max_chunk_chars=150, grade_max_retries=1)
    analyze_model = RunnableLambda(lambda _: AIMessage(content="ok"))
    bad = RunnableLambda(
        lambda _: GradeResult(
            deductions=[Deduction(points=50, reason="x")], summary="s"
        )
    )
    graph = build_graph(settings, analyze_model, bad, DummyRunner())
    final = graph.invoke(base_state())
    result = final["result"]
    assert 0 <= result.score <= 10
    assert sum(d.points for d in result.deductions) <= 10
    assert result.warnings
