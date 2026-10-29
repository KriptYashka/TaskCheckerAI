from __future__ import annotations

from fastapi.testclient import TestClient

from api.app import create_app
from config import Settings
from core.models import Deduction, ExecutionResult, ExecutionStatus, VerificationResult


class StubService:
    runner_name = "stub"
    settings = Settings(_env_file=None, sandbox_enabled=False)

    async def verify(
        self,
        task,
        uploads,
        reference=None,
        max_score=None,
        stdin_data=None,
        reference_uploads=None,
        thread_id=None,
    ):
        return VerificationResult(
            score=7,
            max_score=max_score or 10,
            deductions=[Deduction(points=3, reason="неполное решение")],
            summary="Тестовая оценка",
            execution=ExecutionResult(status=ExecutionStatus.SKIPPED, detail="stub"),
        )

    async def verify_batch(
        self,
        task,
        submissions,
        reference=None,
        max_score=None,
        stdin_data=None,
        reference_uploads=None,
    ):
        students = sorted({name.rsplit(".", 1)[0] for name, _ in submissions})
        results = []
        for student in students:
            result = await self.verify(task, submissions, max_score=max_score)
            results.append((student, result, None))
        return results


def make_app():
    settings = Settings(_env_file=None, sandbox_enabled=False)
    return create_app(settings)


def test_health_endpoint():
    app = make_app()
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["sandbox_backend"] in {"docker", "local", "off"}


def test_index_page_served():
    app = make_app()
    with TestClient(app) as client:
        resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "TaskCheckerAI" in resp.text


def test_verify_endpoint_with_stub():
    app = make_app()
    with TestClient(app) as client:
        app.state.service = StubService()
        resp = client.post(
            "/api/v1/verify",
            data={"task": "Сложить два числа", "max_score": "10"},
            files={"files": ("main.py", b"print(1+2)", "text/x-python")},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["score"] == 7
    assert body["max_score"] == 10
    assert body["deductions"][0]["reason"] == "неполное решение"


def test_verify_endpoint_requires_files():
    app = make_app()
    with TestClient(app) as client:
        app.state.service = StubService()
        resp = client.post("/api/v1/verify", data={"task": "t"})
    assert resp.status_code == 422


def test_verify_requires_task_or_reference():
    app = make_app()
    with TestClient(app) as client:
        app.state.service = StubService()
        resp = client.post(
            "/api/v1/verify",
            files={"files": ("main.py", b"print(1)", "text/x-python")},
        )
    assert resp.status_code == 400


def test_verify_batch_endpoint_with_stub():
    app = make_app()
    with TestClient(app) as client:
        app.state.service = StubService()
        resp = client.post(
            "/api/v1/verify-batch",
            data={"task": "Сложить два числа", "max_score": "10"},
            files=[
                ("submissions", ("ivanov.ipynb", b"{}", "application/json")),
                ("submissions", ("petrov.ipynb", b"{}", "application/json")),
            ],
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert {s["student"] for s in body["students"]} == {"ivanov", "petrov"}
    assert all(s["result"]["score"] == 7 for s in body["students"])
