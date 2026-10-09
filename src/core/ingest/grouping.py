from __future__ import annotations

from core.ingest.loaders import ARCHIVE_EXTENSIONS

_ARCHIVE_SUFFIXES = tuple(sorted(ARCHIVE_EXTENSIONS, key=len, reverse=True))


def student_key(filename: str) -> str:
    name = (filename or "unnamed").replace("\\", "/")

    if "/" in name:
        return name.split("/", 1)[0]

    lower = name.lower()
    for suffix in _ARCHIVE_SUFFIXES:
        if lower.endswith(suffix):
            return name[: len(name) - len(suffix)]

    if "__" in name:
        return name.split("__", 1)[0]

    if "." in name:
        return name.rsplit(".", 1)[0]

    return name


def group_submissions(
    items: list[tuple[str, bytes]],
) -> dict[str, list[tuple[str, bytes]]]:
    groups: dict[str, list[tuple[str, bytes]]] = {}
    for name, data in items:
        groups.setdefault(student_key(name), []).append((name, data))
    return groups
