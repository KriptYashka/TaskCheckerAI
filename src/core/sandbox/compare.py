from __future__ import annotations

import re


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _try_float(value: str) -> float | None:
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


def _tokens_equal(a: str, b: str, tolerance: float = 1e-6) -> bool:
    fa, fb = _try_float(a), _try_float(b)
    if fa is not None and fb is not None:
        return abs(fa - fb) <= tolerance * max(1.0, abs(fa), abs(fb))
    return a == b


def outputs_match(actual: str, expected: str, tolerance: float = 1e-6) -> bool:
    na, ne = normalize(actual), normalize(expected)
    if na == ne:
        return True

    ta = re.split(r"\s+", na.strip()) if na.strip() else []
    te = re.split(r"\s+", ne.strip()) if ne.strip() else []
    if len(ta) != len(te):
        return False
    return all(_tokens_equal(x, y, tolerance) for x, y in zip(ta, te))
