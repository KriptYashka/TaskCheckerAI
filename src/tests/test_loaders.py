from __future__ import annotations

import io
import zipfile

import pytest

import py7zr

from config import Settings
from core.ingest.loaders import IngestError, is_archive_name, load_archive, load_files, load_upload
from core.ingest.context import build_context, split_context


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def make_7z(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with py7zr.SevenZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(data, name)
    return buf.getvalue()


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


def test_7z_loaded_and_skips_service_dirs():
    settings = make_settings()
    data = make_7z(
        {
            "proj/main.py": b"print(1)",
            "proj/__pycache__/x.pyc": b"junk",
            "proj/README.md": b"# readme",
        }
    )
    bundle = load_archive(data, "proj.7z", settings)
    paths = {f.path for f in bundle.files}
    assert "proj/main.py" in paths
    assert "proj/README.md" in paths
    assert not any("__pycache__" in p for p in paths)
    assert next(f for f in bundle.files if f.path == "proj/main.py").content == "print(1)"


def test_7z_is_archive_and_load_upload():
    assert is_archive_name("Proj.7Z")
    settings = make_settings()
    bundle = load_upload("proj.7z", make_7z({"main.py": b"print(1)"}), settings)
    assert [f.path for f in bundle.files] == ["main.py"]


def test_7z_slip_rejected():
    # py7zr запрещает создавать архивы с явным «../», но пропускает путь с
    # переходом внутри — его должен отсечь наш собственный проверяльник путей.
    data = make_7z({"sub/../evil.py": b"print(1)"})
    with pytest.raises(IngestError):
        load_archive(data, "evil.7z", make_settings())


def test_7z_file_size_limit_rejected():
    settings = make_settings(max_file_bytes=10)
    data = make_7z({"big.py": b"x" * 20})
    with pytest.raises(IngestError):
        load_archive(data, "big.7z", settings)


def test_7z_total_size_limit_counts_skipped_files():
    settings = make_settings(max_total_bytes=100, max_file_bytes=1000)
    data = make_7z(
        {"__pycache__/huge.pyc": b"x" * 200, "main.py": b"print(1)"}
    )
    with pytest.raises(IngestError):
        load_archive(data, "proj.7z", settings)


def test_7z_entry_count_limit():
    settings = make_settings(max_archive_entries=2)
    data = make_7z({"a.py": b"a", "b.py": b"b", "c.py": b"c"})
    with pytest.raises(IngestError):
        load_archive(data, "proj.7z", settings)


def test_7z_corrupted_rejected():
    with pytest.raises(IngestError):
        load_archive(b"not an archive", "broken.7z", make_settings())


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
