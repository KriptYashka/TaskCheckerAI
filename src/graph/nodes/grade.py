from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from config import Settings
from core.models import ExecutionResult, ExecutionStatus
from graph.state import GraphState
from llm.budget import fit_reference, truncate
from llm.prompts import GRADE_FIX_PROMPT, GRADE_PROMPT

TASK_FALLBACK = "текст задачи не задан отдельно — ориентируйся на эталонное решение"


def _execution_text(execution: ExecutionResult | None) -> str:
    if execution is None:
        return "запуск не выполнялся"
    parts = [f"статус: {execution.status.value}"]
    if execution.status != ExecutionStatus.SKIPPED:
        parts.append(f"exit_code: {execution.exit_code}")
        if execution.matches_reference is not None:
            parts.append(
                "вывод совпадает с эталоном: " + ("да" if execution.matches_reference else "нет")
            )
        if execution.detail:
            parts.append(f"пояснение: {execution.detail}")
        if execution.stdout:
            parts.append(f"stdout:\n{execution.stdout}")
        if execution.stderr:
            parts.append(f"stderr:\n{execution.stderr}")
    elif execution.detail:
        parts.append(execution.detail)
    return "\n".join(parts)


def make_grade_node(settings: Settings, model: BaseChatModel):
    chain = GRADE_PROMPT | model

    def grade(state: GraphState) -> dict:
        analysis = "\n\n---\n\n".join(state.get("analyses", [])) or "анализ недоступен"
        analysis = truncate(analysis, settings.max_analysis_chars)
        task = state.get("task") or TASK_FALLBACK
        execution = _execution_text(state.get("execution"))
        reference = state.get("reference_context") or "не предоставлен"

        def fit(reserved: int) -> str:
            if reference == "не предоставлен":
                return reference
            return fit_reference(reference, reserved, settings)

        errors = state.get("grade_errors") or []
        previous = state.get("grade")
        if errors and previous is not None:
            fix_chain = GRADE_FIX_PROMPT | model
            errors_text = "\n".join(f"- {e}" for e in errors)
            previous_json = previous.model_dump_json(indent=2)
            reserved = (
                len(task) + len(execution) + len(analysis) + len(errors_text) + len(previous_json)
            )
            payload = {
                "task": task,
                "reference": fit(reserved),
                "max_score": state["max_score"],
                "execution": execution,
                "analysis": analysis,
                "errors": errors_text,
                "previous": previous_json,
            }
            return {"grade": fix_chain.invoke(payload), "grade_errors": []}

        reserved = len(task) + len(execution) + len(analysis)
        base_input = {
            "task": task,
            "reference": fit(reserved),
            "max_score": state["max_score"],
            "execution": execution,
            "analysis": analysis,
        }

        return {"grade": chain.invoke(base_input), "grade_errors": []}

    return grade
