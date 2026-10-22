from __future__ import annotations

from config import Settings
from core.models import ExecutionResult, ExecutionStatus, GradeResult, VerificationResult
from graph.state import GraphState


def collect_grade_errors(grade: GradeResult | None, max_score: int) -> list[str]:
    if grade is None:
        return ["Оценка не была сформирована моделью"]

    errors: list[str] = []
    total = 0.0
    for i, d in enumerate(grade.deductions):
        if d.points < 0:
            errors.append(f"deductions[{i}].points отрицательный ({d.points})")
        if not d.reason.strip():
            errors.append(f"deductions[{i}].reason пустой")
        total += d.points

    if total > max_score + 1e-9:
        errors.append(
            f"сумма снятых баллов ({total}) превышает максимальный балл ({max_score})"
        )

    if not grade.summary.strip():
        errors.append("summary пустой")

    return errors


def compute_score(grade: GradeResult, max_score: int) -> float:
    total = sum(d.points for d in grade.deductions if d.points > 0 and d.reason.strip())
    total = min(total, max_score)
    return round(max_score - total, 4)


def _dedupe(deductions: list) -> list:
    seen: set[tuple] = set()
    result: list = []
    for d in deductions:
        key = (round(d.points, 4), d.reason.strip())
        if key in seen:
            continue
        seen.add(key)
        result.append(d)
    return result


def make_validate_node(settings: Settings):
    def validate(state: GraphState) -> dict:
        grade = state.get("grade")
        if grade is not None:
            grade = grade.model_copy(update={"deductions": _dedupe(grade.deductions)})
        max_score = state["max_score"]
        errors = collect_grade_errors(grade, max_score)
        retries = state.get("retries", 0)

        if errors and retries < settings.grade_max_retries:
            return {"grade_errors": errors, "retries": retries + 1}

        warnings = list(state.get("warnings", []))
        if errors:
            warnings.append(
                "Модель не вернула согласованную оценку после "
                f"{retries} попыток: " + "; ".join(errors)
            )
            grade = _sanitize(grade, max_score)
        elif grade is None:
            grade = GradeResult(summary="Не удалось получить оценку от модели.")

        execution = state.get("execution")
        if execution is None:
            execution = ExecutionResult(status=ExecutionStatus.SKIPPED, detail="запуск не выполнялся")

        result = VerificationResult(
            score=compute_score(grade, max_score),
            max_score=max_score,
            deductions=grade.deductions,
            summary=grade.summary,
            execution=execution,
            retries_used=retries,
            warnings=warnings,
        )
        return {"result": result, "warnings": warnings}

    return validate


def _sanitize(grade: GradeResult | None, max_score: int) -> GradeResult:
    if grade is None:
        return GradeResult(summary="Не удалось получить корректную оценку от модели.")
    deductions = [d for d in grade.deductions if d.points > 0 and d.reason.strip()]
    deductions = _dedupe(deductions)
    total = sum(d.points for d in deductions)
    if total > max_score:
        deductions = _scale(deductions, max_score)
    summary = grade.summary.strip() or "Оценка сформирована частично."
    return GradeResult(deductions=deductions, summary=summary)


def _scale(deductions: list, limit: float) -> list:
    total = sum(d.points for d in deductions)
    if total <= limit or total <= 0:
        return deductions
    factor = limit / total
    return [d.model_copy(update={"points": round(d.points * factor, 4)}) for d in deductions]


def route_after_validate(state: GraphState) -> str:
    if state.get("grade_errors"):
        return "grade"
    return "__end__"
