from __future__ import annotations

import json

from core.ingest.loaders import load_files
from core.ingest.notebook import notebook_to_text
from config import Settings


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def sample_notebook() -> bytes:
    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {},
        "cells": [
            {
                "cell_type": "markdown",
                "source": ["# Задача 1\n", "Сложите два числа"],
                "metadata": {},
            },
            {
                "cell_type": "code",
                "source": ["a = 1\n", "b = 2\n", "print(a + b)"],
                "metadata": {},
                "outputs": [{"output_type": "stream", "name": "stdout", "text": ["3\n"]}],
            },
            {
                "cell_type": "code",
                "source": "import pandas as pd",
                "metadata": {},
                "outputs": [
                    {
                        "output_type": "execute_result",
                        "data": {"text/plain": ["Empty DataFrame"]},
                    },
                    {
                        "output_type": "display_data",
                        "data": {"image/png": "iVBORw0KGgoAAAANSUhEUg"},  # картинка -> пропускается
                    },
                ],
            },
            {
                "cell_type": "code",
                "source": "raise ValueError('boom')",
                "metadata": {},
                "outputs": [
                    {
                        "output_type": "error",
                        "ename": "ValueError",
                        "evalue": "boom",
                        "traceback": ["Traceback", "  File x", "ValueError: boom"],
                    }
                ],
            },
        ],
    }
    return json.dumps(notebook).encode("utf-8")


def test_notebook_extracts_markdown_code_and_outputs():
    text = notebook_to_text(sample_notebook())
    assert text is not None
    assert "Задача 1" in text
    assert "Сложите два числа" in text
    assert "print(a + b)" in text
    assert "3" in text
    assert "Empty DataFrame" in text
    assert "ValueError: boom" in text


def test_notebook_skips_images():
    text = notebook_to_text(sample_notebook())
    assert "iVBORw0KGgo" not in text
    assert "image/png" not in text


def test_invalid_notebook_returns_none():
    assert notebook_to_text(b"not a notebook") is None


def test_loader_parses_ipynb_into_readable_text():
    settings = make_settings()
    bundle = load_files([("student.ipynb", sample_notebook())], settings)
    assert len(bundle.files) == 1
    source = bundle.files[0]
    assert not source.is_binary
    assert source.language == "notebook"
    assert "[ЯЧЕЙКА 1: текст]" in source.content
    assert "Задача 1" in source.content
