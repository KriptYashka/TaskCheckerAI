from __future__ import annotations

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from config import Settings
from core.models import Deduction, ExecutionResult, ExecutionStatus, GradeResult
from graph.builder import build_graph
from graph.nodes.grade import make_grade_node


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


def test_grade_prompt_includes_student_code():
    settings = make_settings()
    captured = {}

    def grade_fn(prompt_value):
        captured["text"] = prompt_value.to_string()
        return GradeResult(deductions=[], summary="ok")

    node = make_grade_node(settings, RunnableLambda(grade_fn))
    node(
        {
            "task": "создать класс",
            "reference_context": "эталон",
            "max_score": 10,
            "analyses": ["код создаёт класс"],
            "context": "===== ФАЙЛ: main.py =====\nprint('unique_code_marker')",
        }
    )

    assert "unique_code_marker" in captured["text"]


def test_grade_fix_prompt_includes_code_and_analysis():
    settings = make_settings()
    captured = {}

    def grade_fn(prompt_value):
        captured["text"] = prompt_value.to_string()
        return GradeResult(deductions=[], summary="ok")

    node = make_grade_node(settings, RunnableLambda(grade_fn))
    node(
        {
            "task": "создать класс",
            "reference_context": "эталон",
            "max_score": 10,
            "analyses": ["unique_analysis_marker"],
            "context": "===== ФАЙЛ: main.py =====\nprint('unique_code_marker')",
            "grade_errors": ["сумма превышает максимум"],
            "grade": GradeResult(
                deductions=[Deduction(points=20, reason="слишком много")],
                summary="плохо",
            ),
        }
    )

    assert "unique_code_marker" in captured["text"]
    assert "unique_analysis_marker" in captured["text"]


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
