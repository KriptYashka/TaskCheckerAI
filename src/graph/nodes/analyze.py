from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.types import Send

from config import Settings
from graph.state import AnalyzePayload, GraphState
from llm.budget import fit_reference
from llm.prompts import ANALYZE_PROMPT

TASK_FALLBACK = "текст задачи не задан отдельно — ориентируйся на эталонное решение"


def make_analyze_node(model: BaseChatModel):
    chain = ANALYZE_PROMPT | model

    def analyze(payload: AnalyzePayload) -> dict:
        result = chain.invoke(
            {
                "task": payload["task"],
                "reference": payload.get("reference") or "не предоставлен",
                "execution": payload.get("execution") or "нет данных",
                "chunk": payload["chunk"],
            }
        )
        return {"analyses": [result.content]}

    return analyze


def make_fan_out_chunks(settings: Settings):
    def fan_out_chunks(state: GraphState) -> list[Send]:
        execution = state.get("execution")
        execution_text = execution.model_dump_json(indent=2) if execution else ""
        task = state.get("task") or TASK_FALLBACK
        reference = state.get("reference_context") or ""
        sends: list[Send] = []
        for chunk in state.get("chunks", []):
            reserved = len(task) + len(execution_text) + len(chunk)
            sends.append(
                Send(
                    "analyze",
                    AnalyzePayload(
                        task=task,
                        reference=fit_reference(reference, reserved, settings),
                        execution=execution_text,
                        chunk=chunk,
                    ),
                )
            )
        return sends

    return fan_out_chunks

