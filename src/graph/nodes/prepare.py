from __future__ import annotations

from config import Settings
from core.ingest.context import build_context, split_context
from core.ingest.loaders import IngestError, load_submission
from core.models import ExecutionStatus
from core.sandbox.runner import SandboxRunner, run_solution
from graph.state import GraphState


def build_reference_context(state: GraphState, settings: Settings) -> tuple[str, list[str]]:
    parts: list[str] = []
    warnings: list[str] = []

    answer = (state.get("reference") or "").strip()
    if answer:
        parts.append(f"Эталонный ответ:\n{answer}")

    reference_uploads = state.get("reference_uploads") or []
    if reference_uploads:
        try:
            bundle = load_submission(reference_uploads, settings)
            reference_context = build_context(bundle, settings.max_context_chars)
            parts.append(f"Эталонное решение:\n{reference_context}")
        except IngestError as exc:
            warnings.append(f"Эталонное решение не удалось прочитать: {exc}")

    return "\n\n".join(parts), warnings


def make_prepare_node(settings: Settings, runner: SandboxRunner):
    def prepare(state: GraphState) -> dict:
        bundle = load_submission(state["uploads"], settings)
        context = build_context(bundle, settings.max_context_chars)
        chunks = split_context(context, settings.max_chunk_chars)

        execution = run_solution(
            runner,
            bundle,
            state.get("stdin_data"),
            state.get("reference"),
            settings,
        )

        warnings: list[str] = []
        if execution.status in (ExecutionStatus.TIMEOUT, ExecutionStatus.FAILED):
            warnings.append(f"Запуск решения завершился со статусом '{execution.status.value}'")

        reference_context, reference_warnings = build_reference_context(state, settings)
        warnings.extend(reference_warnings)

        return {
            "bundle": bundle,
            "context": context,
            "chunks": chunks,
            "execution": execution,
            "reference_context": reference_context,
            "warnings": warnings,
        }

    return prepare
