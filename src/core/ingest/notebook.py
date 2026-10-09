from __future__ import annotations

import json
import re

MAX_CELL_CHARS = 4000
MAX_OUTPUT_CHARS = 2000
_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def is_notebook(name: str) -> bool:
    return name.lower().endswith(".ipynb")


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def _to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "".join(str(item) for item in value)
    return str(value)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... (фрагмент сокращён)"


def _cell_source(cell: dict) -> str:
    if "source" in cell:
        return _to_text(cell.get("source"))
    return _to_text(cell.get("input"))


def _cell_outputs(cell: dict) -> str:
    parts: list[str] = []
    for output in cell.get("outputs") or []:
        output_type = output.get("output_type")
        if output_type == "stream":
            parts.append(_to_text(output.get("text")))
        elif output_type in ("execute_result", "display_data"):
            data = output.get("data") or {}
            if "text/plain" in data:
                parts.append(_to_text(data["text/plain"]))
        elif output_type == "error":
            parts.append(f"{output.get('ename', 'Error')}: {output.get('evalue', '')}")
            traceback = output.get("traceback") or []
            if traceback:
                parts.append(_strip_ansi("\n".join(traceback[-3:])))
    text = "\n".join(part for part in parts if part and part.strip())
    return _truncate(_strip_ansi(text), MAX_OUTPUT_CHARS)


def _iter_cells(notebook: dict):
    cells = notebook.get("cells")
    if cells is not None:
        yield from cells
        return
    for worksheet in notebook.get("worksheets") or []:
        yield from worksheet.get("cells") or []


def notebook_to_text(data: bytes) -> str | None:
    try:
        notebook = json.loads(data.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(notebook, dict):
        return None

    parts: list[str] = []
    for index, cell in enumerate(_iter_cells(notebook), start=1):
        if not isinstance(cell, dict):
            continue
        cell_type = cell.get("cell_type")
        source = _cell_source(cell)
        if cell_type == "markdown":
            if source.strip():
                parts.append(f"[ЯЧЕЙКА {index}: текст]\n{_truncate(source, MAX_CELL_CHARS)}")
        elif cell_type in ("code", "raw"):
            if source.strip():
                parts.append(f"[ЯЧЕЙКА {index}: код]\n{_truncate(source, MAX_CELL_CHARS)}")
            outputs = _cell_outputs(cell)
            if outputs.strip():
                parts.append(f"[ЯЧЕЙКА {index}: вывод]\n{outputs}")

    if not parts:
        return None
    return "\n\n".join(parts)
