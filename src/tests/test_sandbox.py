from __future__ import annotations

import sys

from config import Settings
from core.models import ExecutionStatus, FileBundle, SourceFile
from core.sandbox.runner import (
    DockerSandbox,
    LocalSubprocessSandbox,
    NoSandbox,
    _run_process_capped,
    create_runner,
)


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_sandbox_disabled_returns_off():
    runner = create_runner(make_settings(sandbox_enabled=False))
    assert isinstance(runner, NoSandbox)
    assert runner.name == "off"


def test_docker_unavailable_fails_closed(monkeypatch):
    monkeypatch.setattr(DockerSandbox, "is_available", staticmethod(lambda: False))
    runner = create_runner(
        make_settings(sandbox_backend="docker", sandbox_allow_local=False)
    )
    assert isinstance(runner, NoSandbox)
    result = runner.run(FileBundle(), "main.py", None, None)
    assert result.status == ExecutionStatus.SKIPPED
    assert "локальный запуск запрещён" in result.detail.lower()


def test_docker_unavailable_can_fall_back_to_explicit_local(monkeypatch):
    monkeypatch.setattr(DockerSandbox, "is_available", staticmethod(lambda: False))
    runner = create_runner(
        make_settings(sandbox_backend="docker", sandbox_allow_local=True)
    )
    assert isinstance(runner, LocalSubprocessSandbox)
    assert runner.name == "local"


def test_local_backend_requires_opt_in():
    runner = create_runner(
        make_settings(sandbox_backend="local", sandbox_allow_local=False)
    )
    assert isinstance(runner, NoSandbox)


def test_local_backend_with_opt_in():
    runner = create_runner(make_settings(sandbox_backend="local", sandbox_allow_local=True))
    assert isinstance(runner, LocalSubprocessSandbox)


def test_docker_is_preferred_when_available(monkeypatch):
    monkeypatch.setattr(DockerSandbox, "is_available", staticmethod(lambda: True))
    runner = create_runner(
        make_settings(sandbox_backend="docker", sandbox_allow_local=False)
    )
    assert isinstance(runner, DockerSandbox)
    assert runner.name == "docker"


def test_output_is_capped_while_reading():
    result = _run_process_capped(
        [sys.executable, "-c", "print('x' * 100000)"],
        input_data="",
        timeout=10,
        output_limit=1_000,
    )
    assert result.returncode == 0
    assert len(result.stdout.encode("utf-8")) <= 1_000
    assert result.output_truncated


def test_timeout_kills_process():
    result = _run_process_capped(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        input_data="",
        timeout=0.2,
        output_limit=1_000,
    )
    assert result.timed_out
    assert result.returncode is None


def test_local_sandbox_does_not_leak_server_environment(monkeypatch):
    monkeypatch.setenv("TSK_SECRET_MARKER", "leaked")
    settings = make_settings(
        sandbox_backend="local", sandbox_allow_local=True, sandbox_timeout_sec=10
    )
    code = "import os\nprint(os.environ.get('TSK_SECRET_MARKER', 'missing'))"
    bundle = FileBundle(
        files=[SourceFile(path="main.py", size=len(code), content=code)]
    )
    result = LocalSubprocessSandbox(settings).run(bundle, "main.py", None, "missing")
    assert result.status == ExecutionStatus.OK
    assert result.stdout.strip() == "missing"
