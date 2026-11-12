from __future__ import annotations

import json
import logging
from pathlib import PurePosixPath
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from api.schemas import BatchResponse, ErrorResponse, StudentResult, VerifyResponse
from config import Settings
from core.ingest.loaders import IngestError, is_archive_name
from services.verification import VerificationService, describe_error, is_timeout_error

router = APIRouter(prefix="/api/v1", tags=["verification"])
logger = logging.getLogger(__name__)

MAX_TASK_CHARS = 20_000
MAX_REFERENCE_CHARS = 20_000
MAX_STDIN_CHARS = 64_000
MAX_SCORE_LIMIT = 1_000
READ_CHUNK_SIZE = 64 * 1024
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}

SOURCE_RULE = (
    "Нужно передать хотя бы одно из полей: `task` (условие задачи) "
    "или `reference_files` (эталонное решение)."
)

FILES_DESCRIPTION = (
    "Файлы решения, один архив (`zip`/`tar`/`tar.gz`) или ноутбук `.ipynb`. "
    "Можно передать несколько файлов одной работы. Необязательно, если передан `texts`."
)

TEXTS_DESCRIPTION = (
    "Исходный код программы (Python) в виде массива строк. Каждый элемент — отдельный "
    "файл. Без файлов первый текст получает имя `main.py` (точка входа), остальные — "
    "`text_2.py`, `text_3.py` и т. д. Передаётся повторяющимися полями формы."
)

INPUT_RULE = (
    "Нужно передать хотя бы одно из полей: `files` (файлы/архив/ноутбук) "
    "или `texts` (исходный код текстом)."
)

REFERENCE_FILES_DESCRIPTION = (
    "Эталонное решение: файлы, один архив или ноутбук `.ipynb` (опционально)."
)

VERIFY_DESCRIPTION = f"""\
Проверяет одну работу ученика и возвращает итоговый балл.

**Вход:** `multipart/form-data`.

{INPUT_RULE}

{SOURCE_RULE}

**Что происходит:** файлы разбираются (архивы распаковываются, ноутбуки превращаются
в текст), решение по возможности запускается в песочнице, LLM анализирует код и формирует
список снятых баллов. Итоговый балл считается детерминированно:
`score = max_score - Σ deductions.points`.

**Ошибки:** `400` — не переданы обязательные поля; `422` — не удалось разобрать
файлы; `413` — превышен лимит загрузки; `503`/`504` — LLM недоступна или превысила
таймаут; `429` — превышена частота запросов; `401` — неверный API-ключ.

Если на сервере задан `API_KEY`, передайте его в заголовке `X-API-Key`.
"""

VERIFY_STREAM_DESCRIPTION = f"""\
Проверяет одну работу и стримит прогресс в формате Server-Sent Events (SSE).

Поля запроса совпадают с `POST /api/v1/verify`.

Каждое событие — строка `data: <json>`. В поле `node` приходит этап проверки
(`prepare`, `analyze`, `grade`, `validate`), в поле `update` — краткий результат этапа.
При ошибке LLM/песочницы приходит событие `node: "error"` с полем `detail`.
Поток всегда завершается событием `data: [DONE]`.

{INPUT_RULE}

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
Ошибка одной работы приходит как событие `node: "error"` с полем `student` и не
прерывает обработку остальных. Поток завершается событиями `batch_done` и
`data: [DONE]`.

{SOURCE_RULE}
"""

ERROR_RESPONSES = {
    400: {
        "model": ErrorResponse,
        "description": "Не переданы обязательные поля или отсутствует условие/эталон.",
    },
    401: {
        "model": ErrorResponse,
        "description": "Неверный или отсутствующий API-ключ (если на сервере задан `API_KEY`).",
    },
    413: {
        "model": ErrorResponse,
        "description": "Превышен лимит размера или количества загруженных файлов.",
    },
    422: {
        "model": ErrorResponse,
        "description": "Не удалось разобрать входные файлы, архив или ноутбук.",
    },
    429: {
        "model": ErrorResponse,
        "description": "Превышена частота запросов (если включён rate limit).",
    },
    503: {
        "model": ErrorResponse,
        "description": "LLM или песочница временно недоступны.",
    },
    504: {
        "model": ErrorResponse,
        "description": "Превышено время ожидания ответа LLM.",
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


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _service_http_error(exc: Exception) -> HTTPException:
    status_code = 504 if is_timeout_error(exc) else 503
    return HTTPException(status_code=status_code, detail=describe_error(exc))


async def _collect(
    files: Optional[list[UploadFile]],
    settings: Settings,
    field: str,
) -> list[tuple[str, bytes]]:
    uploads: list[tuple[str, bytes]] = []
    total_bytes = 0

    for upload in files or []:
        if len(uploads) >= settings.max_files:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Превышено максимальное количество файлов ({field}): "
                    f"{settings.max_files}"
                ),
            )

        name = upload.filename or "unnamed"
        per_file_limit = (
            settings.max_total_bytes if is_archive_name(name) else settings.max_file_bytes
        )
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = await upload.read(READ_CHUNK_SIZE)
            if not chunk:
                break
            size += len(chunk)
            if size > per_file_limit:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"Файл '{name}' превышает лимит {per_file_limit} байт"
                    ),
                )
            if total_bytes + size > settings.max_total_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        "Превышен общий лимит размера файлов: "
                        f"{settings.max_total_bytes} байт"
                    ),
                )
            chunks.append(chunk)

        total_bytes += size
        uploads.append((name, b"".join(chunks)))

    return uploads


async def _collect_required(
    files: Optional[list[UploadFile]],
    field: str,
    settings: Settings,
) -> list[tuple[str, bytes]]:
    uploads = await _collect(files, settings, field)
    if not uploads:
        raise HTTPException(status_code=400, detail=f"Не передан ни один файл ({field})")
    return uploads


def _text_filenames(existing: list[tuple[str, bytes]], count: int) -> list[str]:
    used = {PurePosixPath(name.replace("\\", "/")).name for name, _ in existing}
    names: list[str] = []
    for index in range(count):
        if not existing and index == 0:
            base = "main.py"
        else:
            base = f"text_{index + 1}.py"
        name = base
        suffix = 2
        stem = PurePosixPath(base).stem
        while name in used:
            name = f"{stem}_{suffix}.py"
            suffix += 1
        used.add(name)
        names.append(name)
    return names


def _collect_texts(
    texts: Optional[list[str]],
    settings: Settings,
    existing: list[tuple[str, bytes]],
) -> list[tuple[str, bytes]]:
    cleaned = [text for text in (texts or []) if text and text.strip()]
    if not cleaned:
        return []
    if len(existing) + len(cleaned) > settings.max_files:
        raise HTTPException(
            status_code=413,
            detail=f"Превышено максимальное количество файлов: {settings.max_files}",
        )

    names = _text_filenames(existing, len(cleaned))
    total_bytes = sum(len(data) for _, data in existing)
    uploads: list[tuple[str, bytes]] = []
    for name, text in zip(names, cleaned):
        data = text.encode("utf-8")
        if len(data) > settings.max_file_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Текст '{name}' превышает лимит {settings.max_file_bytes} байт",
            )
        total_bytes += len(data)
        if total_bytes > settings.max_total_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    "Превышен общий лимит размера файлов: "
                    f"{settings.max_total_bytes} байт"
                ),
            )
        uploads.append((name, data))
    return uploads


async def _collect_solution_inputs(
    files: Optional[list[UploadFile]],
    texts: Optional[list[str]],
    settings: Settings,
) -> list[tuple[str, bytes]]:
    uploads = await _collect(files, settings, "files")
    uploads.extend(_collect_texts(texts, settings, uploads))
    if not uploads:
        raise HTTPException(
            status_code=400,
            detail="Передайте файлы (files) или тексты кода (texts)",
        )
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
    files: Optional[list[UploadFile]] = File(None, description=FILES_DESCRIPTION),
    texts: Optional[list[str]] = Form(None, description=TEXTS_DESCRIPTION),
    task: Optional[str] = Form(
        None,
        max_length=MAX_TASK_CHARS,
        description="Условие задачи (если нет эталонного решения).",
        examples=["Считайте два числа и выведите их сумму"],
    ),
    reference: Optional[str] = Form(
        None,
        max_length=MAX_REFERENCE_CHARS,
        description="Эталонный ответ текстом (опционально).",
        examples=["7"],
    ),
    reference_files: Optional[list[UploadFile]] = File(
        None, description=REFERENCE_FILES_DESCRIPTION
    ),
    max_score: Optional[int] = Form(
        None,
        ge=1,
        le=MAX_SCORE_LIMIT,
        description=(
            "Максимальный балл (от 1 до 1000). По умолчанию берётся из конфигурации."
        ),
        examples=[10],
    ),
    stdin_data: Optional[str] = Form(
        None,
        max_length=MAX_STDIN_CHARS,
        description="Входные данные, которые подаются решению при запуске.",
        examples=["3 4"],
    ),
) -> VerifyResponse:
    service = _service(request)
    settings = request.app.state.settings
    uploads = await _collect_solution_inputs(files, texts, settings)
    reference_uploads = await _collect(reference_files, settings, "reference_files")
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
    except Exception as exc:
        logger.exception("Ошибка проверки одной работы")
        raise _service_http_error(exc) from exc
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
    files: Optional[list[UploadFile]] = File(None, description=FILES_DESCRIPTION),
    texts: Optional[list[str]] = Form(None, description=TEXTS_DESCRIPTION),
    task: Optional[str] = Form(
        None,
        max_length=MAX_TASK_CHARS,
        description="Условие задачи (если нет эталонного решения).",
        examples=["Считайте два числа и выведите их сумму"],
    ),
    reference: Optional[str] = Form(
        None,
        max_length=MAX_REFERENCE_CHARS,
        description="Эталонный ответ текстом (опционально).",
        examples=["7"],
    ),
    reference_files: Optional[list[UploadFile]] = File(
        None, description=REFERENCE_FILES_DESCRIPTION
    ),
    max_score: Optional[int] = Form(
        None,
        ge=1,
        le=MAX_SCORE_LIMIT,
        description=(
            "Максимальный балл (от 1 до 1000). По умолчанию берётся из конфигурации."
        ),
        examples=[10],
    ),
    stdin_data: Optional[str] = Form(
        None,
        max_length=MAX_STDIN_CHARS,
        description="Входные данные, которые подаются решению при запуске.",
        examples=["3 4"],
    ),
) -> StreamingResponse:
    service = _service(request)
    settings = request.app.state.settings
    uploads = await _collect_solution_inputs(files, texts, settings)
    reference_uploads = await _collect(reference_files, settings, "reference_files")
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
                yield _sse(event)
        except IngestError as exc:
            yield _sse({"node": "error", "detail": str(exc)})
        except Exception as exc:
            logger.exception("Ошибка SSE-проверки одной работы")
            yield _sse(
                {
                    "node": "error",
                    "detail": describe_error(exc),
                    "status": 504 if is_timeout_error(exc) else 503,
                }
            )
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(), media_type="text/event-stream", headers=SSE_HEADERS
    )


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
        max_length=MAX_TASK_CHARS,
        description="Условие задачи (если нет эталонного решения).",
        examples=["Считайте два числа и выведите их сумму"],
    ),
    reference: Optional[str] = Form(
        None,
        max_length=MAX_REFERENCE_CHARS,
        description="Эталонный ответ текстом (опционально).",
        examples=["7"],
    ),
    reference_files: Optional[list[UploadFile]] = File(
        None, description=REFERENCE_FILES_DESCRIPTION
    ),
    max_score: Optional[int] = Form(
        None,
        ge=1,
        le=MAX_SCORE_LIMIT,
        description=(
            "Максимальный балл (от 1 до 1000). По умолчанию берётся из конфигурации."
        ),
        examples=[10],
    ),
    stdin_data: Optional[str] = Form(
        None,
        max_length=MAX_STDIN_CHARS,
        description="Входные данные, которые подаются решению при запуске.",
        examples=["3 4"],
    ),
) -> BatchResponse:
    service = _service(request)
    settings = request.app.state.settings
    items = await _collect_required(submissions, "submissions", settings)
    reference_uploads = await _collect(reference_files, settings, "reference_files")
    _validate_source(task, reference_uploads)
    try:
        results = await service.verify_batch(
            task=task or "",
            submissions=items,
            reference=reference,
            max_score=max_score,
            stdin_data=stdin_data,
            reference_uploads=reference_uploads,
        )
    except Exception as exc:
        logger.exception("Ошибка пакетной проверки")
        raise _service_http_error(exc) from exc
    students = [
        StudentResult(student=student, result=result, error=error)
        for student, result, error in results
    ]
    return BatchResponse(
        max_score=max_score if max_score is not None else settings.default_max_score,
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
        max_length=MAX_TASK_CHARS,
        description="Условие задачи (если нет эталонного решения).",
        examples=["Считайте два числа и выведите их сумму"],
    ),
    reference: Optional[str] = Form(
        None,
        max_length=MAX_REFERENCE_CHARS,
        description="Эталонный ответ текстом (опционально).",
        examples=["7"],
    ),
    reference_files: Optional[list[UploadFile]] = File(
        None, description=REFERENCE_FILES_DESCRIPTION
    ),
    max_score: Optional[int] = Form(
        None,
        ge=1,
        le=MAX_SCORE_LIMIT,
        description=(
            "Максимальный балл (от 1 до 1000). По умолчанию берётся из конфигурации."
        ),
        examples=[10],
    ),
    stdin_data: Optional[str] = Form(
        None,
        max_length=MAX_STDIN_CHARS,
        description="Входные данные, которые подаются решению при запуске.",
        examples=["3 4"],
    ),
) -> StreamingResponse:
    service = _service(request)
    settings = request.app.state.settings
    items = await _collect_required(submissions, "submissions", settings)
    reference_uploads = await _collect(reference_files, settings, "reference_files")
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
                yield _sse(event)
        except IngestError as exc:
            yield _sse({"node": "error", "detail": str(exc)})
        except Exception as exc:
            logger.exception("Ошибка SSE-проверки пакета")
            yield _sse(
                {
                    "node": "error",
                    "detail": describe_error(exc),
                    "status": 504 if is_timeout_error(exc) else 503,
                }
            )
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(), media_type="text/event-stream", headers=SSE_HEADERS
    )
