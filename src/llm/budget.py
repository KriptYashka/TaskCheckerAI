from __future__ import annotations

from config import Settings

CHARS_PER_TOKEN = 3.0
PROMPT_OVERHEAD_CHARS = 4_000
MIN_REFERENCE_CHARS = 1_000
TRUNCATION_MARKER = "\n...[фрагмент обрезан по лимиту контекста модели]"


def prompt_char_budget(settings: Settings) -> int:
    """Approximate number of characters that fit into the model context window.

    Leaves a reserve for the system prompt, the message template and the model
    response. Ollama rejects requests that exceed the context window, so every
    prompt must stay within this budget.
    """
    return max(2_000, int(settings.llm_num_ctx * CHARS_PER_TOKEN) - PROMPT_OVERHEAD_CHARS)


def truncate(text: str, limit: int) -> str:
    if not text:
        return ""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= len(TRUNCATION_MARKER):
        return text[:limit]
    return text[: limit - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER


def fit_reference(reference: str, reserved_chars: int, settings: Settings) -> str:
    """Trim the reference context so that the whole prompt fits the context window."""
    allowed = prompt_char_budget(settings) - reserved_chars
    if allowed < MIN_REFERENCE_CHARS:
        allowed = MIN_REFERENCE_CHARS
    return truncate(reference, allowed)
