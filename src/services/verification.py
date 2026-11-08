from __future__ import annotations

import logging
from typing import AsyncIterator, Optional

from config import Settings, get_settings
from core.ingest.grouping import group_submissions
from core.ingest.loaders import IngestError
from core.models import GradeResult, VerificationResult
from core.sandbox.runner import create_runner
from graph.builder import build_graph
from llm.factory import create_chat_model, create_structured_model

logger = logging.getLogger(__name__)


def is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    return any("timeout" in cls.__name__.lower() for cls in type(exc).__mro__)


def describe_error(exc: BaseException) -> str:
    if isinstance(exc, IngestError):
        return str(exc)
    if is_timeout_error(exc):
        return "Превышено время ожидания при обращении к LLM"
    return "Не удалось получить результат проверки (ошибка LLM или песочницы)"


class VerificationService:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        runner = create_runner(self.settings)
        analyze_model = create_chat_model(self.settings)
        grade_model = create_structured_model(self.settings, GradeResult)
        self.runner_name = runner.name
        # Проверки stateless: чекпоинтер не нужен и иначе копил бы состояние
        # (включая загруженные файлы) до перезапуска процесса.
        self.graph = build_graph(
            self.settings, analyze_model, grade_model, runner, checkpointer=None
        )

    def _initial_state(
        self,
        task: str,
        uploads: list[tuple[str, bytes]],
        reference: Optional[str],
        max_score: Optional[int],
        stdin_data: Optional[str],
        reference_uploads: Optional[list[tuple[str, bytes]]] = None,
    ) -> dict:
        if max_score is not None and max_score < 1:
            raise ValueError("max_score должен быть не меньше 1")
        return {
            "task": task,
            "reference": reference,
            "reference_uploads": reference_uploads or [],
            "max_score": max_score if max_score is not None else self.settings.default_max_score,
            "stdin_data": stdin_data,
            "uploads": uploads,
            "analyses": [],
            "grade_errors": [],
            "retries": 0,
            "warnings": [],
        }

    async def verify(
        self,
        task: str,
        uploads: list[tuple[str, bytes]],
        reference: Optional[str] = None,
        max_score: Optional[int] = None,
        stdin_data: Optional[str] = None,
        reference_uploads: Optional[list[tuple[str, bytes]]] = None,
        thread_id: Optional[str] = None,
    ) -> VerificationResult:
        # thread_id оставлен для совместимости, но чекпоинтер не используется.
        _ = thread_id
        state = self._initial_state(
            task, uploads, reference, max_score, stdin_data, reference_uploads
        )
        final_state = await self.graph.ainvoke(state)
        return final_state["result"]

    async def verify_stream(
        self,
        task: str,
        uploads: list[tuple[str, bytes]],
        reference: Optional[str] = None,
        max_score: Optional[int] = None,
        stdin_data: Optional[str] = None,
        reference_uploads: Optional[list[tuple[str, bytes]]] = None,
        thread_id: Optional[str] = None,
    ) -> AsyncIterator[dict]:
        _ = thread_id
        state = self._initial_state(
            task, uploads, reference, max_score, stdin_data, reference_uploads
        )
        async for event in self.graph.astream(state, stream_mode="updates"):
            for node, update in event.items():
                yield {"node": node, "update": _serialize(node, update)}

    async def verify_batch(
        self,
        task: str,
        submissions: list[tuple[str, bytes]],
        reference: Optional[str] = None,
        max_score: Optional[int] = None,
        stdin_data: Optional[str] = None,
        reference_uploads: Optional[list[tuple[str, bytes]]] = None,
    ) -> list[tuple[str, Optional[VerificationResult], Optional[str]]]:
        results: list[tuple[str, Optional[VerificationResult], Optional[str]]] = []
        for student, items in group_submissions(submissions).items():
            try:
                result = await self.verify(
                    task=task,
                    uploads=items,
                    reference=reference,
                    max_score=max_score,
                    stdin_data=stdin_data,
                    reference_uploads=reference_uploads,
                )
                results.append((student, result, None))
            except Exception as exc:
                # Одна упавшая работа не должна ломать весь пакет.
                logger.exception("Не удалось проверить работу ученика %s", student)
                results.append((student, None, describe_error(exc)))
        return results

    async def verify_batch_stream(
        self,
        task: str,
        submissions: list[tuple[str, bytes]],
        reference: Optional[str] = None,
        max_score: Optional[int] = None,
        stdin_data: Optional[str] = None,
        reference_uploads: Optional[list[tuple[str, bytes]]] = None,
    ) -> AsyncIterator[dict]:
        groups = group_submissions(submissions)
        resolved_max_score = (
            max_score if max_score is not None else self.settings.default_max_score
        )
        yield {
            "node": "batch_start",
            "update": {"students": list(groups.keys()), "max_score": resolved_max_score},
        }
        for student, items in groups.items():
            yield {"node": "student_start", "update": {"student": student}}
            try:
                async for event in self.verify_stream(
                    task=task,
                    uploads=items,
                    reference=reference,
                    max_score=max_score,
                    stdin_data=stdin_data,
                    reference_uploads=reference_uploads,
                ):
                    yield {"student": student, **event}
            except Exception as exc:
                logger.exception("Не удалось проверить работу ученика %s", student)
                yield {
                    "student": student,
                    "node": "error",
                    "update": {"detail": describe_error(exc)},
                }
        yield {"node": "batch_done", "update": {}}


def _serialize(node: str, update: dict) -> dict:
    if node == "prepare":
        return {
            "chunks": len(update.get("chunks", [])),
            "execution_status": getattr(update.get("execution"), "status", None),
        }
    if node == "analyze":
        return {"analyses": len(update.get("analyses", []))}
    if node == "grade" and update.get("grade") is not None:
        return {"grade": update["grade"].model_dump()}
    if node == "validate":
        result = update.get("result")
        return {"result": result.model_dump() if result else None}
    return {}
