from __future__ import annotations

from core.models import FileBundle, SourceFile

PRIORITY_NAMES = (
    "main.py", "solution.py", "solve.py", "solution.cpp", "main.cpp", "main.java",
    "Main.java", "main.c", "program.py", "task.py", "app.py",
)


def _priority(name: str) -> tuple[int, int, str]:
    base = name.rsplit("/", 1)[-1]
    if base in PRIORITY_NAMES:
        rank = 0
    elif base.endswith(".py"):
        rank = 1
    elif base.rsplit(".", 1)[-1].lower() in {"cpp", "cc", "cxx", "c", "java", "cs", "pas", "kt"}:
        rank = 2
    else:
        rank = 3
    return rank, len(name), name


def build_file_tree(bundle: FileBundle) -> str:
    lines: list[str] = []
    included = {f.path for f in bundle.files if f.content is not None and not f.is_binary}
    for f in sorted(bundle.files, key=lambda x: x.path):
        mark = "" if f.path in included else "  (без содержимого)"
        kind = " [бинарный]" if f.is_binary else ""
        lines.append(f"- {f.path} ({f.size} b){kind}{mark}")
    return "\n".join(lines)


def build_context(bundle: FileBundle, max_chars: int) -> str:
    header = f"Структура проекта:\n{build_file_tree(bundle)}\n\n"
    budget = max_chars - len(header)
    parts: list[str] = [header]

    text_files = [
        f for f in bundle.files
        if f.content is not None and not f.is_binary
    ]
    for f in sorted(text_files, key=lambda x: _priority(x.path)):
        block = f"\n===== ФАЙЛ: {f.path} =====\n{f.content}\n"
        if budget - len(block) < 0:
            parts.append(
                f"\n===== ФАЙЛ: {f.path} — пропущен из-за лимита контекста =====\n"
            )
            continue
        parts.append(block)
        budget -= len(block)

    return "".join(parts)


def split_context(context: str, max_chunk_chars: int) -> list[str]:
    if len(context) <= max_chunk_chars:
        return [context]

    lines = context.splitlines(keepends=True)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        if current_len + len(line) > max_chunk_chars and current:
            chunks.append("".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += len(line)
    if current:
        chunks.append("".join(current))
    return chunks
