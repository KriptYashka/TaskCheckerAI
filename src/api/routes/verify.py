from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from api.schemas import BatchResponse, StudentResult, VerifyResponse
from core.ingest.loaders import IngestError
from services.verification import VerificationService

router = APIRouter(prefix="/api/v1", tags=["verification"])


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


@router.post("/verify", response_model=VerifyResponse)
async def verify(
    request: Request,
    files: list[UploadFile] = File(..., description="Файлы решения или один архив/ноутбук"),
    task: Optional[str] = Form(None, description="Условие задачи (если нет эталонного решения)"),
    reference: Optional[str] = Form(None, description="Эталонный ответ текстом (опционально)"),
    reference_files: Optional[list[UploadFile]] = File(
        None, description="Эталонное решение: файлы или один архив/ноутбук (опционально)"
    ),
    max_score: Optional[int] = Form(None, description="Максимальный балл"),
    stdin_data: Optional[str] = Form(None, description="Входные данные для запуска"),
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


@router.post("/verify/stream")
async def verify_stream(
    request: Request,
    files: list[UploadFile] = File(..., description="Файлы решения или один архив/ноутбук"),
    task: Optional[str] = Form(None, description="Условие задачи (если нет эталонного решения)"),
    reference: Optional[str] = Form(None, description="Эталонный ответ текстом (опционально)"),
    reference_files: Optional[list[UploadFile]] = File(
        None, description="Эталонное решение: файлы или один архив/ноутбук (опционально)"
    ),
    max_score: Optional[int] = Form(None, description="Максимальный балл"),
    stdin_data: Optional[str] = Form(None, description="Входные данные для запуска"),
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


@router.post("/verify-batch", response_model=BatchResponse)
async def verify_batch(
    request: Request,
    submissions: list[UploadFile] = File(
        ...,
        description="Работы учеников. Каждый файл — отдельная работа: ноутбук/архив/файл, "
        "или группа файлов с префиксом 'ученик__имя'. Например ivanov__main.py, ivanov__utils.py",
    ),
    task: Optional[str] = Form(None, description="Условие задачи (если нет эталонного решения)"),
    reference: Optional[str] = Form(None, description="Эталонный ответ текстом (опционально)"),
    reference_files: Optional[list[UploadFile]] = File(
        None, description="Эталонное решение: файлы или один архив/ноутбук (опционально)"
    ),
    max_score: Optional[int] = Form(None, description="Максимальный балл"),
    stdin_data: Optional[str] = Form(None, description="Входные данные для запуска"),
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


@router.post("/verify-batch/stream")
async def verify_batch_stream(
    request: Request,
    submissions: list[UploadFile] = File(..., description="Работы учеников (см. /verify-batch)"),
    task: Optional[str] = Form(None, description="Условие задачи (если нет эталонного решения)"),
    reference: Optional[str] = Form(None, description="Эталонный ответ текстом (опционально)"),
    reference_files: Optional[list[UploadFile]] = File(
        None, description="Эталонное решение: файлы или один архив/ноутбук (опционально)"
    ),
    max_score: Optional[int] = Form(None, description="Максимальный балл"),
    stdin_data: Optional[str] = Form(None, description="Входные данные для запуска"),
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
