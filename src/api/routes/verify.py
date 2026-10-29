from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from api.schemas import BatchResponse, ErrorResponse, StudentResult, VerifyResponse
from core.ingest.loaders import IngestError
from services.verification import VerificationService

router = APIRouter(prefix="/api/v1", tags=["verification"])

SOURCE_RULE = (
    "Нужно передать хотя бы одно из полей: `task` (условие задачи) "
    "или `reference_files` (эталонное решение)."
)

FILES_DESCRIPTION = (
    "Файлы решения, один архив (`zip`/`tar`/`tar.gz`) или ноутбук `.ipynb`. "
    "Можно передать несколько файлов одной работы."
)

REFERENCE_FILES_DESCRIPTION = (
    "Эталонное решение: файлы, один архив или ноутбук `.ipynb` (опционально)."
)

VERIFY_DESCRIPTION = f"""\
Проверяет одну работу ученика и возвращает итоговый балл.

**Вход:** `multipart/form-data`. Поле `files` обязательно.

{SOURCE_RULE}

**Что происходит:** файлы разбираются (архивы распаковываются, ноутбуки превращаются
в текст), решение по возможности запускается в песочнице, LLM анализирует код и формирует
список снятых баллов. Итоговый балл считается детерминированно:
`score = max_score - Σ deductions.points`.

**Ошибки:** `400` — не переданы обязательные поля; `422` — не удалось разобрать файлы.
"""

VERIFY_STREAM_DESCRIPTION = f"""\
Проверяет одну работу и стримит прогресс в формате Server-Sent Events (SSE).

Поля запроса совпадают с `POST /api/v1/verify`.

Каждое событие — строка `data: <json>`. В поле `node` приходит этап проверки
(`prepare`, `analyze`, `grade`, `validate`), в поле `update` — краткий результат этапа.
Поток всегда завершается событием `data: [DONE]`.

{SOURCE_RULE}
"""

VERIFY_BATCH_DESCRIPTION = f"""\
Проверяет пакет работ учеников под одну задачу или один эталон.

**Вход:** `multipart/form-data`. Каждый файл в поле `submissions` — отдельная работа
(ноутбук или архив). Несколько файлов одной работы группируются по префиксу до `__`
или по имени папки: `ivanov__main.py` и `ivanov__utils.py` → ученик `ivanov`.

{SOURCE_RULE}

Работы, которые не удалось разобрать, возвращаются с заполненным полем `error`,
остальные — с результатом проверки в поле `result`.
"""

VERIFY_BATCH_STREAM_DESCRIPTION = f"""\
Проверяет пакет работ и стримит прогресс по каждому ученику в формате SSE.

Поля запроса совпадают с `POST /api/v1/verify-batch`.

События `student_start` сообщают о начале проверки работы, далее идут этапы
`prepare`, `analyze`, `grade`, `validate`, затем финальный `result`.
Поток завершается событиями `batch_done` и `data: [DONE]`.

{SOURCE_RULE}
"""

ERROR_RESPONSES = {
    400: {
        "model": ErrorResponse,
        "description": "Не переданы обязательные поля или отсутствует условие/эталон.",
    },
    422: {
        "model": ErrorResponse,
        "description": "Не удалось разобрать входные файлы, архив или ноутбук.",
    },
}

STREAM_RESPONSES = {
    200: {
        "description": (
            "Поток SSE-событий. Каждое событие — `data: <json>`, "
            "завершение потока — `data: [DONE]`."
        ),
        "content": {
            "text/event-stream": {
                "schema": {"type": "string"},
                "example": (
                    'data: {"node": "prepare", "update": {"chunks": 1, '
                    '"execution_status": "ok"}}\n\n'
                    'data: {"node": "grade", "update": {"grade": {"summary": "..."}}}\n\n'
                    "data: [DONE]\n\n"
                ),
            }
        },
    },
    **ERROR_RESPONSES,
}


def _service(request: Request) -> VerificationService:
    return request.app.state.service


async def _collect(files: Optional[list[UploadFile]]) -> list[tuple[str, bytes]]:
    uploads: list[tuple[str, bytes]] = []
    for upload in files or []:
        data = await upload.read()
        uploads.append((upload.filename or "unnamed", data))
    return uploads


async def _collect_required(files: Optional[list[UploadFile]], field: str) -> list[tuple[str, bytes]]:
    uploads = await _collect(files)
    if not uploads:
        raise HTTPException(status_code=400, detail=f"Не передан ни один файл ({field})")
    return uploads


def _validate_source(task: Optional[str], reference_files: list[tuple[str, bytes]]) -> None:
    if not (task and task.strip()) and not reference_files:
        raise HTTPException(
            status_code=400,
            detail="Нужно передать текст задачи или эталонное решение (файлы/ноутбук)",
        )


@router.post(
    "/verify",
    response_model=VerifyResponse,
    summary="Проверить одну работу",
    description=VERIFY_DESCRIPTION,
    response_description="Балл, снятые баллы, резюме проверки и результат запуска.",
    responses=ERROR_RESPONSES,
    operation_id="verifySolution",
)
async def verify(
    request: Request,
    files: list[UploadFile] = File(..., description=FILES_DESCRIPTION),
    task: Optional[str] = Form(
        None,
        description="Условие задачи (если нет эталонного решения).",
        examples=["Считайте два числа и выведите их сумму"],
    ),
    reference: Optional[str] = Form(
        None,
        description="Эталонный ответ текстом (опционально).",
        examples=["7"],
    ),
    reference_files: Optional[list[UploadFile]] = File(
        None, description=REFERENCE_FILES_DESCRIPTION
    ),
    max_score: Optional[int] = Form(
        None,
        description="Максимальный балл. По умолчанию берётся из конфигурации.",
        examples=[10],
    ),
    stdin_data: Optional[str] = Form(
        None,
        description="Входные данные, которые подаются решению при запуске.",
        examples=["3 4"],
    ),
) -> VerifyResponse:
    service = _service(request)
    uploads = await _collect_required(files, "files")
    reference_uploads = await _collect(reference_files)
    _validate_source(task, reference_uploads)
    try:
        result = await service.verify(
            task=task or "",
            uploads=uploads,
            reference=reference,
            max_score=max_score,
            stdin_data=stdin_data,
            reference_uploads=reference_uploads,
        )
    except IngestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return VerifyResponse(**result.model_dump())


@router.post(
    "/verify/stream",
    response_class=StreamingResponse,
    summary="Проверить одну работу (SSE)",
    description=VERIFY_STREAM_DESCRIPTION,
    response_description="SSE-поток с прогрессом проверки и финальным результатом.",
    responses=STREAM_RESPONSES,
    operation_id="verifySolutionStream",
)
async def verify_stream(
    request: Request,
    files: list[UploadFile] = File(..., description=FILES_DESCRIPTION),
    task: Optional[str] = Form(
        None,
        description="Условие задачи (если нет эталонного решения).",
        examples=["Считайте два числа и выведите их сумму"],
    ),
    reference: Optional[str] = Form(
        None,
        description="Эталонный ответ текстом (опционально).",
        examples=["7"],
    ),
    reference_files: Optional[list[UploadFile]] = File(
        None, description=REFERENCE_FILES_DESCRIPTION
    ),
    max_score: Optional[int] = Form(
        None,
        description="Максимальный балл. По умолчанию берётся из конфигурации.",
        examples=[10],
    ),
    stdin_data: Optional[str] = Form(
        None,
        description="Входные данные, которые подаются решению при запуске.",
        examples=["3 4"],
    ),
) -> StreamingResponse:
    service = _service(request)
    uploads = await _collect_required(files, "files")
    reference_uploads = await _collect(reference_files)
    _validate_source(task, reference_uploads)

    async def event_stream():
        try:
            async for event in service.verify_stream(
                task=task or "",
                uploads=uploads,
                reference=reference,
                max_score=max_score,
                stdin_data=stdin_data,
                reference_uploads=reference_uploads,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        except IngestError as exc:
            yield f"data: {json.dumps({'node': 'error', 'detail': str(exc)}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post(
    "/verify-batch",
    response_model=BatchResponse,
    summary="Проверить пакет работ",
    description=VERIFY_BATCH_DESCRIPTION,
    response_description="Результаты по каждому ученику и количество обработанных работ.",
    responses=ERROR_RESPONSES,
    operation_id="verifyBatch",
)
async def verify_batch(
    request: Request,
    submissions: list[UploadFile] = File(
        ...,
        description=(
            "Работы учеников. Каждый файл — отдельная работа: ноутбук/архив/файл "
            "или группа файлов с префиксом 'ученик__имя'. "
            "Например: ivanov__main.py, ivanov__utils.py."
        ),
    ),
    task: Optional[str] = Form(
        None,
        description="Условие задачи (если нет эталонного решения).",
        examples=["Считайте два числа и выведите их сумму"],
    ),
    reference: Optional[str] = Form(
        None,
        description="Эталонный ответ текстом (опционально).",
        examples=["7"],
    ),
    reference_files: Optional[list[UploadFile]] = File(
        None, description=REFERENCE_FILES_DESCRIPTION
    ),
    max_score: Optional[int] = Form(
        None,
        description="Максимальный балл. По умолчанию берётся из конфигурации.",
        examples=[10],
    ),
    stdin_data: Optional[str] = Form(
        None,
        description="Входные данные, которые подаются решению при запуске.",
        examples=["3 4"],
    ),
) -> BatchResponse:
    service = _service(request)
    items = await _collect_required(submissions, "submissions")
    reference_uploads = await _collect(reference_files)
    _validate_source(task, reference_uploads)
    results = await service.verify_batch(
        task=task or "",
        submissions=items,
        reference=reference,
        max_score=max_score,
        stdin_data=stdin_data,
        reference_uploads=reference_uploads,
    )
    students = [
        StudentResult(student=student, result=result, error=error)
        for student, result, error in results
    ]
    return BatchResponse(
        max_score=max_score or service.settings.default_max_score,
        count=len(students),
        students=students,
    )


@router.post(
    "/verify-batch/stream",
    response_class=StreamingResponse,
    summary="Проверить пакет работ (SSE)",
    description=VERIFY_BATCH_STREAM_DESCRIPTION,
    response_description="SSE-поток с прогрессом и результатами по каждому ученику.",
    responses=STREAM_RESPONSES,
    operation_id="verifyBatchStream",
)
async def verify_batch_stream(
    request: Request,
    submissions: list[UploadFile] = File(
        ...,
        description=(
            "Работы учеников (см. `POST /api/v1/verify-batch`). Каждый файл — отдельная "
            "работа или группа файлов с префиксом 'ученик__имя'."
        ),
    ),
    task: Optional[str] = Form(
        None,
        description="Условие задачи (если нет эталонного решения).",
        examples=["Считайте два числа и выведите их сумму"],
    ),
    reference: Optional[str] = Form(
        None,
        description="Эталонный ответ текстом (опционально).",
        examples=["7"],
    ),
    reference_files: Optional[list[UploadFile]] = File(
        None, description=REFERENCE_FILES_DESCRIPTION
    ),
    max_score: Optional[int] = Form(
        None,
        description="Максимальный балл. По умолчанию берётся из конфигурации.",
        examples=[10],
    ),
    stdin_data: Optional[str] = Form(
        None,
        description="Входные данные, которые подаются решению при запуске.",
        examples=["3 4"],
    ),
) -> StreamingResponse:
    service = _service(request)
    items = await _collect_required(submissions, "submissions")
    reference_uploads = await _collect(reference_files)
    _validate_source(task, reference_uploads)

    async def event_stream():
        try:
            async for event in service.verify_batch_stream(
                task=task or "",
                submissions=items,
                reference=reference,
                max_score=max_score,
                stdin_data=stdin_data,
                reference_uploads=reference_uploads,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        except IngestError as exc:
            yield f"data: {json.dumps({'node': 'error', 'detail': str(exc)}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
