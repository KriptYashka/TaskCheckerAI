from __future__ import annotations

import re
from pathlib import PureWindowsPath

MAX_PATH_LENGTH = 512
MAX_COMPONENT_LENGTH = 255
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_relative_path(name: str) -> str:
    """Нормализует и проверяет относительный путь файла.

    Запрещает абсолютные пути, Windows drive/UNC-пути, переходы ``..``,
    управляющие символы, зарезервированные имена Windows и слишком длинные
    компоненты. Возвращает путь в POSIX-нотации (``/``).
    """

    if not isinstance(name, str):
        raise ValueError("Имя файла должно быть строкой")
    if not name:
        raise ValueError("Пустое имя файла")
    if name != name.strip():
        raise ValueError("Имя файла не должно начинаться или заканчиваться пробелом")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise ValueError("Имя файла содержит управляющие символы")

    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        raise ValueError("Абсолютные и UNC-пути запрещены")
    if _DRIVE_PREFIX.match(normalized) or PureWindowsPath(name).drive:
        raise ValueError("Пути с буквой диска запрещены")

    parts: list[str] = []
    for part in normalized.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError("Переход к родительской директории запрещён")
        if part.endswith((" ", ".")):
            raise ValueError("Компонент пути не должен заканчиваться пробелом или точкой")
        if len(part) > MAX_COMPONENT_LENGTH:
            raise ValueError(f"Слишком длинный компонент пути: {part[:40]}...")
        stem = part.split(".", 1)[0].upper()
        if stem in _WINDOWS_RESERVED:
            raise ValueError(f"Зарезервированное имя файла: {part}")
        parts.append(part)

    if not parts:
        raise ValueError("Некорректное имя файла")

    result = "/".join(parts)
    if len(result) > MAX_PATH_LENGTH:
        raise ValueError(f"Слишком длинный путь: {result[:80]}...")
    return result
