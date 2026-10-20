from __future__ import annotations

import uuid
from typing import AsyncIterator, Optional

from langgraph.checkpoint.memory import MemorySaver

from config import Settings, get_settings
from core.ingest.grouping import group_submissions
from core.ingest.loaders import IngestError
from core.models import GradeResult, VerificationResult
from core.sandbox.runner import create_runner
from graph.builder import build_graph
from llm.factory import create_chat_model


class VerificationService:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        runner = create_runner(self.settings)
        analyze_model = create_chat_model(self.settings)
        grade_model = create_chat_model(self.settings).with_structured_output(
            GradeResult, method="json_schema"
        )
        self.runner_name = runner.name
        self.graph = build_graph(
            self.settings, analyze_model, grade_model, runner, checkpointer=MemorySaver()
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
        return {
            "task": task,
            "reference": reference,
            "reference_uploads": reference_uploads or [],
            "max_score": max_score or self.settings.default_max_score,
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
        state = self._initial_state(
            task, uploads, reference, max_score, stdin_data, reference_uploads
        )
        config = {"configurable": {"thread_id": thread_id or str(uuid.uuid4())}}
        final_state = await self.graph.ainvoke(state, config=config)
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
        state = self._initial_state(
            task, uploads, reference, max_score, stdin_data, reference_uploads
        )
        config = {"configurable": {"thread_id": thread_id or str(uuid.uuid4())}}
        async for event in self.graph.astream(state, config=config, stream_mode="updates"):
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
            except IngestError as exc:
                results.append((student, None, str(exc)))
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
        yield {"node": "batch_start", "update": {"students": list(groups.keys())}}
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
            except IngestError as exc:
                yield {"student": student, "node": "error", "update": {"detail": str(exc)}}
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
