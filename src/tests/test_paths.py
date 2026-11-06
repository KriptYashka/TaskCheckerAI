from __future__ import annotations

import io
import tarfile
import zipfile

import pytest

from config import Settings
from core.ingest.loaders import IngestError, load_archive, load_files
from core.models import FileBundle, SourceFile
from core.paths import safe_relative_path
from core.sandbox.runner import SandboxError, _materialize


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def make_tar(name: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.mark.parametrize(
    "name",
    [
        "../escape.txt",
        "sub/../../escape.txt",
        "..\\escape.txt",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "C:\\Windows\\win.ini",
        "\\\\server\\share\\evil.py",
        "//server/share/evil.py",
        "bad\x00name.py",
        "bad\nname.py",
    ],
)
def test_unsafe_upload_paths_rejected(name):
    with pytest.raises(IngestError):
        load_files([(name, b"print(1)")], make_settings())


def test_safe_paths_are_normalized():
    assert safe_relative_path("ivanov\\main.py") == "ivanov/main.py"
    assert safe_relative_path("./a/./b.py") == "a/b.py"
    bundle = load_files([("ivanov\\main.py", b"print(1)")], make_settings())
    assert bundle.files[0].path == "ivanov/main.py"


@pytest.mark.parametrize(
    "name",
    [
        "sub/../../evil.py",
        "C:/evil.py",
        "\\\\server\\share\\evil.py",
        "/etc/passwd",
    ],
)
def test_zip_slip_and_windows_paths_rejected(name):
    data = make_zip({name: b"print(1)"})
    with pytest.raises(IngestError):
        load_archive(data, "evil.zip", make_settings())


def test_tar_slip_rejected():
    data = make_tar("../evil.py", b"print(1)")
    with pytest.raises(IngestError):
        load_archive(data, "evil.tar", make_settings())


def test_skipped_archive_files_count_toward_total_limit():
    settings = make_settings(max_total_bytes=1_000, max_file_bytes=10_000)
    data = make_zip({"__pycache__/huge.pyc": b"x" * 2_000, "main.py": b"print(1)"})
    with pytest.raises(IngestError):
        load_archive(data, "project.zip", settings)


def test_archive_entry_count_limit():
    settings = make_settings(max_archive_entries=2)
    data = make_zip({"a.py": b"a", "b.py": b"b", "c.py": b"c"})
    with pytest.raises(IngestError):
        load_archive(data, "project.zip", settings)


def test_materialize_rejects_unsafe_bundle_path(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    bundle = FileBundle(
        files=[SourceFile(path="../escape.txt", size=4, content="pwned")]
    )
    with pytest.raises(SandboxError):
        _materialize(bundle, root)
    assert not (tmp_path / "escape.txt").exists()


def test_materialize_writes_inside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    bundle = FileBundle(
        files=[SourceFile(path="sub/main.py", size=8, content="print(1)")]
    )
    mapping = _materialize(bundle, root)
    assert mapping["sub/main.py"] == str((root / "sub" / "main.py").resolve())
    assert (root / "sub" / "main.py").read_text(encoding="utf-8") == "print(1)"
