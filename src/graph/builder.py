from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from config import Settings
from core.sandbox.runner import SandboxRunner
from graph.nodes.analyze import make_analyze_node, make_fan_out_chunks
from graph.nodes.grade import make_grade_node
from graph.nodes.prepare import make_prepare_node
from graph.nodes.validate import make_validate_node, route_after_validate
from graph.state import GraphState


def build_graph(
    settings: Settings,
    analyze_model: BaseChatModel,
    grade_model: BaseChatModel,
    runner: SandboxRunner,
    checkpointer=None,
):
    builder = StateGraph(GraphState)
    builder.add_node("prepare", make_prepare_node(settings, runner))
    builder.add_node("analyze", make_analyze_node(analyze_model))
    builder.add_node("grade", make_grade_node(settings, grade_model))
    builder.add_node("validate", make_validate_node(settings))

    builder.add_edge(START, "prepare")
    builder.add_conditional_edges("prepare", make_fan_out_chunks(settings), ["analyze"])
    builder.add_edge("analyze", "grade")
    builder.add_edge("grade", "validate")
    builder.add_conditional_edges(
        "validate",
        route_after_validate,
        {"grade": "grade", END: END},
    )

    return builder.compile(checkpointer=checkpointer)
