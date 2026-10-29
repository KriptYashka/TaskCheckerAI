from __future__ import annotations

from core.models import Deduction, GradeResult
from graph.nodes.validate import _dedupe, _sanitize, collect_grade_errors, compute_score


def test_consistent_grade_has_no_errors():
    grade = GradeResult(
        deductions=[Deduction(points=2, reason="не обработан пустой ввод")],
        summary="ok",
    )
    assert collect_grade_errors(grade, max_score=10) == []
    assert compute_score(grade, 10) == 8


def test_sum_exceeding_max_detected():
    grade = GradeResult(
        deductions=[Deduction(points=12, reason="много")],
        summary="ok",
    )
    errors = collect_grade_errors(grade, max_score=10)
    assert any("превышает" in e for e in errors)


def test_empty_reason_detected():
    grade = GradeResult(
        deductions=[Deduction(points=1, reason=" ")],
        summary="ok",
    )
    errors = collect_grade_errors(grade, max_score=10)
    assert any("reason пустой" in e for e in errors)


def test_compute_score_clamps_to_zero():
    grade = GradeResult(
        deductions=[Deduction(points=50, reason="a")],
        summary="ok",
    )
    assert compute_score(grade, 10) == 0


def test_sanitize_scales_deductions_to_limit():
    grade = GradeResult(
        deductions=[Deduction(points=10, reason="a"), Deduction(points=10, reason="b")],
        summary="ok",
    )
    fixed = _sanitize(grade, max_score=10)
    assert abs(sum(d.points for d in fixed.deductions) - 10) < 1e-6


def test_sanitize_drops_zero_and_blank_reasons():
    grade = GradeResult(
        deductions=[
            Deduction(points=0, reason=" "),
            Deduction(points=5, reason="грубая ошибка"),
        ],
        summary="ok",
    )
    fixed = _sanitize(grade, max_score=10)
    assert len(fixed.deductions) == 1
    assert fixed.deductions[0].points == 5


def test_dedupe_removes_exact_duplicates():
    deductions = [
        Deduction(points=1, reason="дублирующая причина"),
        Deduction(points=1, reason="дублирующая причина"),
        Deduction(points=1, reason="дублирующая причина"),
        Deduction(points=2, reason="другая причина"),
    ]
    result = _dedupe(deductions)
    assert len(result) == 2
    assert [d.points for d in result] == [1, 2]


def test_dedupe_keeps_distinct_reasons_with_same_points():
    deductions = [
        Deduction(points=1, reason="причина A"),
        Deduction(points=1, reason="причина B"),
    ]
    assert len(_dedupe(deductions)) == 2
