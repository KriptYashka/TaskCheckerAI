from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

from charset_normalizer import from_bytes

from config import Settings
from core.ingest.notebook import is_notebook, notebook_to_text
from core.models import FileBundle, SourceFile
from core.paths import safe_relative_path

ARCHIVE_EXTENSIONS = {".zip", ".tar", ".tar.gz", ".tgz", ".gz"}
TEXT_EXTENSIONS = {
    ".py", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".java", ".js", ".ts",
    ".kt", ".go", ".rs", ".pas", ".cs", ".php", ".rb", ".sql", ".txt", ".md",
    ".json", ".xml", ".yml", ".yaml", ".csv", ".html", ".css", ".sh", ".bat",
    ".cfg", ".ini", ".toml", ".properties", ".in", ".out", ".ipynb",
}
SKIP_NAMES = {".git", ".idea", "__pycache__", "node_modules", ".venv", "venv", ".DS_Store"}


class IngestError(ValueError):
    pass


def _safe_name(name: str, source: str) -> str:
    try:
        return safe_relative_path(name)
    except ValueError as exc:
        raise IngestError(f"Небезопасный путь ({source}): {name[:80]!r}: {exc}") from exc


def _safe_member_name(name: str) -> str:
    return _safe_name(name, "архив")


def _safe_upload_name(name: str) -> str:
    return _safe_name(name, "загрузка")


def _should_skip(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return any(p in SKIP_NAMES for p in parts) or name.startswith(".")


def _is_mostly_printable(text: str) -> bool:
    if not text:
        return True
    printable = sum(1 for ch in text if ch.isprintable() or ch in "\n\r\t")
    return printable / len(text) >= 0.85


def _decode_text(data: bytes) -> tuple[str, str]:
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass

    for encoding in ("cp1251", "koi8-r"):
        try:
            candidate = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if _is_mostly_printable(candidate):
            return candidate, encoding

    result = from_bytes(data).best()
    if result is not None:
        return str(result), result.encoding or "utf-8"
    raise UnicodeDecodeError("utf-8", data, 0, 1, "не удалось определить кодировку")


def _decode_file(data: bytes, path: str) -> SourceFile:
    size = len(data)
    if b"\x00" in data[:8192]:
        return SourceFile(path=path, size=size, is_binary=True)

    if is_notebook(path):
        text = notebook_to_text(data)
        if text is not None:
            return SourceFile(path=path, size=size, content=text, encoding="utf-8")

    try:
        content, encoding = _decode_text(data)
    except UnicodeDecodeError:
        return SourceFile(path=path, size=size, is_binary=True)

    ext = Path(path).suffix.lower()
    if ext not in TEXT_EXTENSIONS and not _is_mostly_printable(content):
        return SourceFile(path=path, size=size, is_binary=True)

    return SourceFile(path=path, size=size, content=content, encoding=encoding)


def _add_file(bundle: FileBundle, settings: Settings, path: str, data: bytes) -> None:
    if _should_skip(path):
        return
    if len(bundle.files) >= settings.max_files:
        raise IngestError(f"Превышено максимальное количество файлов: {settings.max_files}")
    if data and len(data) > settings.max_file_bytes:
        data = data[: settings.max_file_bytes]
        source = _decode_file(data, path)
        source.is_truncated = True
        bundle.files.append(source)
        return
    bundle.files.append(_decode_file(data, path))


def _read_archive_member(archive, settings: Settings, bundle: FileBundle) -> None:
    members = archive.getmembers()
    if len(members) > settings.max_archive_entries:
        raise IngestError(
            f"В архиве слишком много записей: {len(members)} "
            f"(лимит {settings.max_archive_entries})"
        )

    archive_total = 0
    for member in members:
        if not member.isfile():
            continue
        name = _safe_member_name(member.name)
        size = max(0, member.size)
        # Служебные файлы тоже учитываем в общем бюджете: иначе архив из
        # множества пропускаемых файлов обходит лимит на распаковку.
        archive_total += size
        if archive_total > settings.max_total_bytes:
            raise IngestError(
                f"Превышен общий лимит размера файлов: {settings.max_total_bytes} байт"
            )
        if _should_skip(name):
            continue
        if size > settings.max_file_bytes:
            raise IngestError(
                f"Файл '{name}' ({size} байт) превышает лимит {settings.max_file_bytes} байт"
            )
        handle = archive.extractfile(member)
        if handle is None:
            continue
        _add_file(bundle, settings, name, handle.read())


def load_archive(data: bytes, filename: str, settings: Settings) -> FileBundle:
    bundle = FileBundle()
    lower = filename.lower()
    try:
        if lower.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                infos = zf.infolist()
                if len(infos) > settings.max_archive_entries:
                    raise IngestError(
                        f"В архиве слишком много записей: {len(infos)} "
                        f"(лимит {settings.max_archive_entries})"
                    )
                archive_total = 0
                for info in infos:
                    if info.is_dir():
                        continue
                    name = _safe_member_name(info.filename)
                    size = max(0, info.file_size)
                    archive_total += size
                    if archive_total > settings.max_total_bytes:
                        raise IngestError(
                            f"Превышен общий лимит размера файлов: "
                            f"{settings.max_total_bytes} байт"
                        )
                    if _should_skip(name):
                        continue
                    if size > settings.max_file_bytes:
                        raise IngestError(
                            f"Файл '{name}' ({size} байт) превышает лимит "
                            f"{settings.max_file_bytes} байт"
                        )
                    _add_file(bundle, settings, name, zf.read(info))
        elif lower.endswith((".tar", ".tar.gz", ".tgz")):
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
                _read_archive_member(tf, settings, bundle)
        else:
            raise IngestError(f"Неподдерживаемый формат архива: {filename}")
    except (zipfile.BadZipFile, tarfile.TarError) as exc:
        raise IngestError(f"Повреждённый архив '{filename}': {exc}") from exc

    if not bundle.files:
        raise IngestError("Архив не содержит пригодных файлов")
    return bundle


def load_files(items: list[tuple[str, bytes]], settings: Settings) -> FileBundle:
    if not items:
        raise IngestError("Не передано ни одного файла")
    bundle = FileBundle()
    total_bytes = 0
    for raw_name, data in items:
        name = _safe_upload_name(raw_name)
        if len(data) > settings.max_file_bytes:
            raise IngestError(
                f"Файл '{name}' ({len(data)} байт) превышает лимит {settings.max_file_bytes} байт"
            )
        # Учитываем и пропускаемые файлы: они всё равно занимают память при разборе.
        total_bytes += len(data)
        if total_bytes > settings.max_total_bytes:
            raise IngestError(f"Превышен общий лимит размера файлов: {settings.max_total_bytes} байт")
        _add_file(bundle, settings, name, data)

    if not bundle.files:
        raise IngestError("Среди переданных файлов нет пригодных (возможно, все бинарные)")
    return bundle


def is_archive_name(filename: str) -> bool:
    lower = filename.lower()
    return any(lower.endswith(ext) for ext in (".zip", ".tar", ".tar.gz", ".tgz"))


def load_upload(filename: str, data: bytes, settings: Settings) -> FileBundle:
    if is_archive_name(filename):
        return load_archive(data, filename, settings)
    return load_files([(filename, data)], settings)


def load_submission(items: list[tuple[str, bytes]], settings: Settings) -> FileBundle:
    if len(items) == 1:
        name, data = items[0]
        if is_archive_name(name):
            return load_archive(data, name, settings)
    return load_files(items, settings)
