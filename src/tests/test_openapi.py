from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from api.app import create_app
from config import Settings

ROOT = Path(__file__).resolve().parents[2]
COMMITTED_SPEC = ROOT / "docs" / "openapi.json"


def make_app():
    return create_app(Settings(_env_file=None, sandbox_enabled=False))


def make_spec() -> dict[str, Any]:
    return make_app().openapi()


def resolve_ref(spec: dict[str, Any], ref: str) -> dict[str, Any]:
    assert ref.startswith("#/"), ref
    node: Any = spec
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def test_openapi_metadata():
    info = make_spec()["info"]

    assert info["title"] == "TaskCheckerAI"
    assert info["version"] == "0.1.0"
    assert "проверк" in info["description"].lower()
    assert info["contact"]["url"] == "https://github.com/KriptYashka/TaskCheckerAI"


def test_openapi_tags():
    spec = make_spec()
    tags = {tag["name"]: tag for tag in spec["tags"]}

    assert set(tags) == {"verification", "health"}
    assert "проверк" in tags["verification"]["description"].lower()
    assert tags["health"]["description"]


def test_openapi_documents_all_endpoints():
    spec = make_spec()
    expected = {
        "/health": ("get", "getHealth"),
        "/api/v1/verify": ("post", "verifySolution"),
        "/api/v1/verify/stream": ("post", "verifySolutionStream"),
        "/api/v1/verify-batch": ("post", "verifyBatch"),
        "/api/v1/verify-batch/stream": ("post", "verifyBatchStream"),
    }

    for path, (method, operation_id) in expected.items():
        assert path in spec["paths"], path
        operation = spec["paths"][path][method]
        assert operation["operationId"] == operation_id
        assert operation["summary"]
        assert operation["description"]


def test_verify_request_body_documented():
    spec = make_spec()
    request_body = spec["paths"]["/api/v1/verify"]["post"]["requestBody"]
    content = request_body["content"]["multipart/form-data"]
    body_schema = resolve_ref(spec, content["schema"]["$ref"])

    assert request_body["required"] is True
    assert body_schema["required"] == ["files"]
    assert set(body_schema["properties"]) == {
        "files",
        "task",
        "reference",
        "reference_files",
        "max_score",
        "stdin_data",
    }
    assert body_schema["properties"]["task"]["examples"]
    assert body_schema["properties"]["max_score"]["examples"] == [10]


def test_batch_request_body_documented():
    spec = make_spec()
    request_body = spec["paths"]["/api/v1/verify-batch"]["post"]["requestBody"]
    body_schema = resolve_ref(
        spec, request_body["content"]["multipart/form-data"]["schema"]["$ref"]
    )

    assert body_schema["required"] == ["submissions"]
    assert "submissions" in body_schema["properties"]


def test_json_responses_reference_schemas():
    spec = make_spec()

    verify = spec["paths"]["/api/v1/verify"]["post"]["responses"]
    verify_ok = verify["200"]["content"]["application/json"]["schema"]
    assert verify_ok["$ref"] == "#/components/schemas/VerifyResponse"
    assert verify["400"]["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/ErrorResponse"
    )
    assert verify["422"]["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/ErrorResponse"
    )

    batch = spec["paths"]["/api/v1/verify-batch"]["post"]["responses"]
    batch_ok = batch["200"]["content"]["application/json"]["schema"]
    assert batch_ok["$ref"] == "#/components/schemas/BatchResponse"


def test_streaming_responses_documented_as_sse():
    spec = make_spec()

    for path in ("/api/v1/verify/stream", "/api/v1/verify-batch/stream"):
        responses = spec["paths"][path]["post"]["responses"]
        assert set(responses["200"]["content"]) == {"text/event-stream"}
        assert "data: [DONE]" in responses["200"]["content"]["text/event-stream"]["example"]
        assert "400" in responses
        assert "422" in responses


def test_response_models_have_examples_and_descriptions():
    spec = make_spec()
    schemas = spec["components"]["schemas"]

    assert schemas["VerifyResponse"]["examples"]
    assert schemas["BatchResponse"]["examples"]
    assert schemas["StudentResult"]["examples"]
    assert schemas["HealthResponse"]["examples"]
    assert schemas["ErrorResponse"]["properties"]["detail"]["description"]
    assert schemas["VerificationResult"]["properties"]["score"]["description"]
    assert schemas["ExecutionResult"]["properties"]["status"]["description"]


def test_swagger_redoc_and_openapi_are_served():
    client = TestClient(make_app())

    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "swagger-ui" in docs.text

    redoc = client.get("/redoc")
    assert redoc.status_code == 200
    assert "redoc" in redoc.text

    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    assert openapi.json()["info"]["title"] == "TaskCheckerAI"


def test_committed_openapi_spec_is_up_to_date():
    assert COMMITTED_SPEC.is_file(), (
        "docs/openapi.json не найден. Запустите python scripts/export_openapi.py"
    )
    committed = json.loads(COMMITTED_SPEC.read_text(encoding="utf-8"))
    assert committed == make_spec(), (
        "docs/openapi.json устарел. Запустите python scripts/export_openapi.py"
    )
