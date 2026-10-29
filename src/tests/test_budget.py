from __future__ import annotations

from config import Settings
from llm.budget import (
    MIN_REFERENCE_CHARS,
    PROMPT_OVERHEAD_CHARS,
    TRUNCATION_MARKER,
    fit_reference,
    prompt_char_budget,
    truncate,
)


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_prompt_budget_scales_with_num_ctx():
    small = prompt_char_budget(make_settings(llm_num_ctx=8192))
    large = prompt_char_budget(make_settings(llm_num_ctx=16384))
    assert large > small
    assert small == int(8192 * 3.0) - PROMPT_OVERHEAD_CHARS


def test_truncate_keeps_short_text():
    assert truncate("hello", 100) == "hello"


def test_truncate_marks_cut_text():
    result = truncate("a" * 1000, 100)
    assert len(result) == 100
    assert result.endswith(TRUNCATION_MARKER)


def test_fit_reference_respects_budget():
    settings = make_settings(llm_num_ctx=8192)
    reference = "r" * 100_000
    reserved = 5_000
    result = fit_reference(reference, reserved, settings)
    assert len(result) <= prompt_char_budget(settings) - reserved
    assert result.endswith(TRUNCATION_MARKER)


def test_fit_reference_never_empty_on_huge_reserved():
    settings = make_settings(llm_num_ctx=8192)
    result = fit_reference("r" * 100, 10_000_000, settings)
    assert 0 < len(result) <= MIN_REFERENCE_CHARS
