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


def test_verify_endpoint_requires_files_or_texts():
    app = make_app()
    with TestClient(app) as client:
        app.state.service = StubService()
        resp = client.post("/api/v1/verify", data={"task": "t"})
    assert resp.status_code == 400
    assert "files" in resp.json()["detail"]
    assert "texts" in resp.json()["detail"]


class CapturingService(StubService):
    def __init__(self):
        self.uploads = None

    async def verify(self, task, uploads, **kwargs):
        self.uploads = uploads
        return await super().verify(task, uploads, **kwargs)

    async def verify_stream(self, task, uploads, **kwargs):
        self.uploads = uploads
        yield {"node": "validate", "update": {}}


def test_verify_accepts_texts_without_files():
    app = make_app()
    service = CapturingService()
    with TestClient(app) as client:
        app.state.service = service
        resp = client.post(
            "/api/v1/verify",
            data={"task": "t", "texts": ["print(1)"]},
        )
    assert resp.status_code == 200
    assert service.uploads == [("main.py", b"print(1)")]


def test_verify_accepts_multiple_texts():
    app = make_app()
    service = CapturingService()
    with TestClient(app) as client:
        app.state.service = service
        resp = client.post(
            "/api/v1/verify",
            data={"task": "t", "texts": ["print(1)", "print(2)"]},
        )
    assert resp.status_code == 200
    assert service.uploads == [
        ("main.py", b"print(1)"),
        ("text_2.py", b"print(2)"),
    ]


def test_verify_stream_accepts_texts():
    app = make_app()
    service = CapturingService()
    with TestClient(app) as client:
        app.state.service = service
        resp = client.post(
            "/api/v1/verify/stream",
            data={"task": "t", "texts": ["print(1)"]},
        )
    assert resp.status_code == 200
    assert service.uploads == [("main.py", b"print(1)")]
    assert "data: [DONE]" in resp.text


def test_verify_combines_files_and_texts_without_name_collision():
    app = make_app()
    service = CapturingService()
    with TestClient(app) as client:
        app.state.service = service
        resp = client.post(
            "/api/v1/verify",
            data={"task": "t", "texts": ["print(2)"]},
            files={"files": ("main.py", b"print(1)", "text/x-python")},
        )
    assert resp.status_code == 200
    assert service.uploads == [
        ("main.py", b"print(1)"),
        ("text_1.py", b"print(2)"),
    ]


def test_verify_rejects_oversized_text_early():
    settings = Settings(_env_file=None, sandbox_enabled=False, max_total_bytes=10)
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.service = StubService()
        resp = client.post(
            "/api/v1/verify",
            data={"task": "t", "texts": ["print('too large')"]},
        )
    assert resp.status_code == 413


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


class FailingService(StubService):
    async def verify(self, *args, **kwargs):
        raise RuntimeError("Ollama is down")


class TimeoutService(StubService):
    async def verify(self, *args, **kwargs):
        raise TimeoutError("LLM timeout")


class FailingStreamService(StubService):
    async def verify_stream(self, *args, **kwargs):
        yield {"node": "prepare", "update": {"chunks": 1}}
        raise RuntimeError("Ollama is down")


def test_verify_rejects_non_positive_max_score():
    app = make_app()
    with TestClient(app) as client:
        app.state.service = StubService()
        resp = client.post(
            "/api/v1/verify",
            data={"task": "t", "max_score": "0"},
            files={"files": ("main.py", b"print(1)", "text/x-python")},
        )
    assert resp.status_code == 422


def test_verify_rejects_too_large_max_score():
    app = make_app()
    with TestClient(app) as client:
        app.state.service = StubService()
        resp = client.post(
            "/api/v1/verify",
            data={"task": "t", "max_score": "1001"},
            files={"files": ("main.py", b"print(1)", "text/x-python")},
        )
    assert resp.status_code == 422


def test_verify_rejects_oversized_upload_early():
    settings = Settings(_env_file=None, sandbox_enabled=False, max_total_bytes=10)
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.service = StubService()
        resp = client.post(
            "/api/v1/verify",
            data={"task": "t"},
            files={"files": ("main.py", b"print('too large')", "text/x-python")},
        )
    assert resp.status_code == 413


def test_verify_returns_503_when_llm_fails():
    app = make_app()
    with TestClient(app) as client:
        app.state.service = FailingService()
        resp = client.post(
            "/api/v1/verify",
            data={"task": "t"},
            files={"files": ("main.py", b"print(1)", "text/x-python")},
        )
    assert resp.status_code == 503


def test_verify_returns_504_on_timeout():
    app = make_app()
    with TestClient(app) as client:
        app.state.service = TimeoutService()
        resp = client.post(
            "/api/v1/verify",
            data={"task": "t"},
            files={"files": ("main.py", b"print(1)", "text/x-python")},
        )
    assert resp.status_code == 504


def test_stream_emits_error_and_done_on_llm_failure():
    app = make_app()
    with TestClient(app) as client:
        app.state.service = FailingStreamService()
        resp = client.post(
            "/api/v1/verify/stream",
            data={"task": "t"},
            files={"files": ("main.py", b"print(1)", "text/x-python")},
        )
    assert resp.status_code == 200
    assert "data: [DONE]" in resp.text
    assert '"node": "error"' in resp.text
    assert '"status": 503' in resp.text


def test_api_key_is_enforced_when_configured():
    settings = Settings(_env_file=None, sandbox_enabled=False, api_key="secret")
    app = create_app(settings)
    payload = {
        "data": {"task": "t"},
        "files": {"files": ("main.py", b"print(1)", "text/x-python")},
    }
    with TestClient(app) as client:
        app.state.service = StubService()
        assert client.post("/api/v1/verify", **payload).status_code == 401
        assert (
            client.post(
                "/api/v1/verify", headers={"X-API-Key": "wrong"}, **payload
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/verify", headers={"X-API-Key": "secret"}, **payload
            ).status_code
            == 200
        )


def test_rate_limit_returns_429():
    settings = Settings(
        _env_file=None,
        sandbox_enabled=False,
        rate_limit_per_minute=1,
    )
    app = create_app(settings)
    payload = {
        "data": {"task": "t"},
        "files": {"files": ("main.py", b"print(1)", "text/x-python")},
    }
    with TestClient(app) as client:
        app.state.service = StubService()
        assert client.post("/api/v1/verify", **payload).status_code == 200
        response = client.post("/api/v1/verify", **payload)
    assert response.status_code == 429
    assert "Retry-After" in response.headers
