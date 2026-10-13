from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Protocol

from config import Settings
from core.models import ExecutionResult, ExecutionStatus, FileBundle, SourceFile
from core.sandbox.compare import outputs_match

PYTHON_ENTRY_CANDIDATES = (
    "main.py", "solution.py", "solve.py", "program.py", "task.py", "app.py", "run.py",
)


class SandboxError(RuntimeError):
    pass


class SandboxRunner(Protocol):
    name: str

    def is_available(self) -> bool: ...

    def run(
        self,
        bundle: FileBundle,
        entrypoint: str,
        stdin_data: Optional[str],
        reference: Optional[str],
    ) -> ExecutionResult: ...


def detect_python_entrypoint(bundle: FileBundle) -> Optional[str]:
    python_files = [
        f.path for f in bundle.files
        if f.content is not None and not f.is_binary and f.path.lower().endswith(".py")
    ]
    if not python_files:
        return None
    for candidate in PYTHON_ENTRY_CANDIDATES:
        for path in python_files:
            if path.rsplit("/", 1)[-1] == candidate:
                return path
    top_level = [p for p in python_files if "/" not in p]
    return sorted(top_level or python_files, key=lambda p: (len(p), p))[0]


def _materialize(bundle: FileBundle, root: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for source in bundle.files:
        if source.content is None or source.is_binary:
            continue
        target = root / source.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.content, encoding="utf-8", errors="replace")
        mapping[source.path] = str(target)
    return mapping


def _finalize(
    status: ExecutionStatus,
    stdout: str,
    stderr: str,
    exit_code: Optional[int],
    reference: Optional[str],
    detail: str = "",
) -> ExecutionResult:
    matches = None
    if reference is not None and status == ExecutionStatus.OK:
        matches = outputs_match(stdout, reference)
    return ExecutionResult(
        status=status,
        exit_code=exit_code,
        stdout=stdout[-8000:],
        stderr=stderr[-4000:],
        matches_reference=matches,
        detail=detail,
    )


class DockerSandbox:
    name = "docker"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @staticmethod
    def is_available() -> bool:
        if shutil.which("docker") is None:
            return False
        try:
            proc = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.SubprocessError, OSError):
            return False
        return proc.returncode == 0

    def run(
        self,
        bundle: FileBundle,
        entrypoint: str,
        stdin_data: Optional[str],
        reference: Optional[str],
    ) -> ExecutionResult:
        s = self.settings
        with tempfile.TemporaryDirectory(prefix="tsk-sandbox-") as tmp:
            root = Path(tmp)
            _materialize(bundle, root)
            container_workdir = "/work"
            cmd = [
                "docker", "run", "--rm", "-i",
                "--network", "none",
                "--memory", f"{s.sandbox_memory_mb}m",
                "--cpus", "1",
                "--pids-limit", "128",
                "-v", f"{root}:{container_workdir}:rw",
                "-w", container_workdir,
                s.sandbox_image,
                "python", entrypoint,
            ]
            try:
                proc = subprocess.run(
                    cmd,
                    input=stdin_data or "",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=s.sandbox_timeout_sec,
                )
            except subprocess.TimeoutExpired as exc:
                out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
                err = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
                return _finalize(
                    ExecutionStatus.TIMEOUT, out, err, None, reference,
                    detail=f"Превышен таймаут {s.sandbox_timeout_sec} c",
                )
            except OSError as exc:
                return _finalize(ExecutionStatus.ERROR, "", str(exc), None, reference)

            status = ExecutionStatus.OK if proc.returncode == 0 else ExecutionStatus.FAILED
            return _finalize(status, proc.stdout, proc.stderr, proc.returncode, reference)


class LocalSubprocessSandbox:
    name = "local"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @staticmethod
    def is_available() -> bool:
        return True

    def run(
        self,
        bundle: FileBundle,
        entrypoint: str,
        stdin_data: Optional[str],
        reference: Optional[str],
    ) -> ExecutionResult:
        s = self.settings
        with tempfile.TemporaryDirectory(prefix="tsk-sandbox-") as tmp:
            root = Path(tmp)
            _materialize(bundle, root)
            target = root / entrypoint
            try:
                proc = subprocess.run(
                    [sys.executable, str(target)],
                    input=stdin_data or "",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=str(root),
                    timeout=s.sandbox_timeout_sec,
                )
            except subprocess.TimeoutExpired as exc:
                out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
                err = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
                return _finalize(
                    ExecutionStatus.TIMEOUT, out, err, None, reference,
                    detail=f"Превышен таймаут {s.sandbox_timeout_sec} c (локальный запуск)",
                )
            except OSError as exc:
                return _finalize(ExecutionStatus.ERROR, "", str(exc), None, reference)

            status = ExecutionStatus.OK if proc.returncode == 0 else ExecutionStatus.FAILED
            detail = "Локальный запуск без изоляции (Docker недоступен)"
            return _finalize(status, proc.stdout, proc.stderr, proc.returncode, reference, detail)


def create_runner(settings: Settings) -> SandboxRunner:
    if not settings.sandbox_enabled:
        return NoSandbox(settings)
    if settings.sandbox_backend == "local":
        return LocalSubprocessSandbox(settings)
    docker = DockerSandbox(settings)
    if docker.is_available():
        return docker
    return LocalSubprocessSandbox(settings)


def run_solution(
    runner: SandboxRunner,
    bundle: FileBundle,
    stdin_data: Optional[str],
    reference: Optional[str],
    settings: Settings,
) -> ExecutionResult:
    if isinstance(runner, NoSandbox):
        return ExecutionResult(status=ExecutionStatus.SKIPPED, detail="Песочница отключена")

    entrypoint = detect_python_entrypoint(bundle)
    if entrypoint is None:
        return ExecutionResult(
            status=ExecutionStatus.SKIPPED,
            detail="Не найден Python-файл для запуска",
        )
    return runner.run(bundle, entrypoint, stdin_data, reference)


class NoSandbox:
    name = "off"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @staticmethod
    def is_available() -> bool:
        return False

    @staticmethod
    def run(*args, **kwargs) -> ExecutionResult:
        return ExecutionResult(status=ExecutionStatus.SKIPPED, detail="Песочница отключена")
