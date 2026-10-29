from __future__ import annotations

from core.sandbox.compare import normalize, outputs_match


def test_normalize_trailing_spaces_and_crlf():
    assert normalize("a  \r\nb\r\n\r\n") == "a\nb"


def test_exact_match_after_normalize():
    assert outputs_match("42\n", "42")


def test_numeric_tolerance():
    assert outputs_match("3.14159265", "3.14159266")
    assert not outputs_match("3.14", "3.15")


def test_multiline_token_match():
    assert outputs_match("1 2 3\n4 5", "1   2 3\n4 5")


def test_mismatch():
    assert not outputs_match("1 2", "1 2 3")
