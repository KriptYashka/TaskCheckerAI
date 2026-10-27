from __future__ import annotations

import io
import zipfile

import pytest

from config import Settings
from core.ingest.loaders import IngestError, load_archive, load_files
from core.ingest.context import build_context, split_context


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_load_single_python_file():
    settings = make_settings()
    bundle = load_files([("solution.py", "print('hello')".encode())], settings)
    assert len(bundle.files) == 1
    assert bundle.files[0].content == "print('hello')"
    assert bundle.files[0].language == "python"


def test_skip_binary_file():
    settings = make_settings()
    bundle = load_files([("prog.exe", b"MZ\x00\x00\x03\x00\x00\x00")], settings)
    assert bundle.files[0].is_binary


def test_decodes_cp1251():
    settings = make_settings()
    text = "привет мир"
    bundle = load_files([("note.txt", text.encode("cp1251"))], settings)
    assert bundle.files[0].content.strip() == text


def test_zip_loaded_and_skips_service_dirs():
    settings = make_settings()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("proj/main.py", "print(1)")
        zf.writestr("proj/__pycache__/x.pyc", "junk")
        zf.writestr("proj/README.md", "# readme")
    bundle = load_archive(buf.getvalue(), "proj.zip", settings)
    paths = {f.path for f in bundle.files}
    assert "proj/main.py" in paths
    assert "proj/README.md" in paths
    assert not any("__pycache__" in p for p in paths)


def test_zip_slip_rejected():
    settings = make_settings()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../evil.py", "print(1)")
    with pytest.raises(IngestError):
        load_archive(buf.getvalue(), "evil.zip", settings)


def test_too_many_files_rejected():
    settings = make_settings(max_files=2)
    items = [(f"f{i}.txt", b"x") for i in range(3)]
    with pytest.raises(IngestError):
        load_files(items, settings)


def test_file_size_limit_rejected():
    settings = make_settings(max_file_bytes=10)
    with pytest.raises(IngestError):
        load_files([("big.py", b"print('a very long line')")], settings)


def test_build_context_and_split():
    settings = make_settings()
    bundle = load_files(
        [("a.py", b"print('a')"), ("b.py", b"print('b')")], settings
    )
    context = build_context(bundle, max_chars=1000)
    assert "a.py" in context and "b.py" in context
    chunks = split_context(context, max_chunk_chars=10)
    assert len(chunks) > 1
