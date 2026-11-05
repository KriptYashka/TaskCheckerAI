from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from config import Settings
from core.models import ExecutionResult, ExecutionStatus, FileBundle, SourceFile
from core.paths import safe_relative_path
from core.sandbox.compare import outputs_match

PYTHON_ENTRY_CANDIDATES = (
    "main.py", "solution.py", "solve.py", "program.py", "task.py", "app.py", "run.py",
)
READ_CHUNK_SIZE = 64 * 1024


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


@dataclass
class _ProcessOutput:
    returncode: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool = False
    output_truncated: bool = False


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


def _resolve_inside(root: Path, relative: str) -> Path:
    try:
        safe = safe_relative_path(relative)
    except ValueError as exc:
        raise SandboxError(f"Небезопасный путь файла: {relative!r}") from exc

    root = root.resolve()
    target = (root / safe).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise SandboxError(f"Путь выходит за пределы песочницы: {relative!r}") from exc
    return target


def _materialize(bundle: FileBundle, root: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for source in bundle.files:
        if source.content is None or source.is_binary:
            continue
        target = _resolve_inside(root, source.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.content, encoding="utf-8", errors="replace")
        mapping[source.path] = str(target)
    return mapping


def _run_process_capped(
    cmd: list[str],
    input_data: Optional[str],
    timeout: float,
    output_limit: int,
    cwd: Optional[str] = None,
    env: Optional[dict[str, str]] = None,
) -> _ProcessOutput:
    """Запускает процесс, читая stdout/stderr с жёстким лимитом.

    ``subprocess.run`` буферизует весь вывод в память, поэтому код ученика мог
    напечатать гигабайты и уронить сервис. Здесь лишний вывод вычитывается и
    выбрасывается, а в память попадает только первые ``output_limit`` байт
    каждого потока.
    """

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
    )
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    truncated = {"stdout": False, "stderr": False}

    def read_stream(stream, sink: list[bytes], key: str) -> None:
        total = 0
        try:
            while True:
                chunk = stream.read(READ_CHUNK_SIZE)
                if not chunk:
                    break
                if total < output_limit:
                    take = min(len(chunk), output_limit - total)
                    sink.append(chunk[:take])
                    total += take
                if total >= output_limit:
                    truncated[key] = True
        finally:
            stream.close()

    def write_stdin() -> None:
        try:
            if input_data:
                proc.stdin.write(input_data.encode("utf-8"))
            proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass

    threads = [
        threading.Thread(
            target=read_stream, args=(proc.stdout, stdout_chunks, "stdout"), daemon=True
        ),
        threading.Thread(
            target=read_stream, args=(proc.stderr, stderr_chunks, "stderr"), daemon=True
        ),
        threading.Thread(target=write_stdin, daemon=True),
    ]
    for thread in threads:
        thread.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    for thread in threads:
        thread.join(timeout=5)

    return _ProcessOutput(
        returncode=None if timed_out else proc.returncode,
        stdout=b"".join(stdout_chunks).decode("utf-8", errors="replace"),
        stderr=b"".join(stderr_chunks).decode("utf-8", errors="replace"),
        timed_out=timed_out,
        output_truncated=truncated["stdout"] or truncated["stderr"],
    )


def _output_note(truncated: bool) -> str:
    return "вывод обрезан по лимиту" if truncated else ""


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


def _force_remove_container(name: str) -> None:
    for args in (["docker", "kill", name], ["docker", "rm", "-f", name]):
        try:
            subprocess.run(args, capture_output=True, text=True, timeout=10)
        except (subprocess.SubprocessError, OSError):
            pass


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
            try:
                _materialize(bundle, root)
            except SandboxError as exc:
                return _finalize(ExecutionStatus.ERROR, "", str(exc), None, reference)

            if os.name != "nt":
                # Контейнер работает не от владельца временной директории и
                # монтирует её read-only: даём остальным право читать файлы.
                root.chmod(0o755)
                for path in root.rglob("*"):
                    path.chmod(0o755 if path.is_dir() else 0o644)

            container_name = f"tsk-{uuid.uuid4().hex[:16]}"
            container_workdir = "/work"
            cmd = [
                "docker", "run", "--rm", "-i",
                "--name", container_name,
                "--network", "none",
                "--read-only",
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--user", s.sandbox_user,
                "--memory", f"{s.sandbox_memory_mb}m",
                "--memory-swap", f"{s.sandbox_memory_mb}m",
                "--cpus", str(s.sandbox_cpus),
                "--pids-limit", str(s.sandbox_pids_limit),
                "--ipc", "none",
                "--ulimit", "nofile=256:256",
                "--tmpfs", f"/tmp:rw,noexec,nosuid,size={s.sandbox_tmpfs_mb}m",
                "-e", "PYTHONDONTWRITEBYTECODE=1",
                "-e", "PYTHONUNBUFFERED=1",
                "-e", "HOME=/tmp",
                "-v", f"{root}:{container_workdir}:ro",
                "-w", container_workdir,
                s.sandbox_image,
                "python", entrypoint,
            ]
            try:
                proc = _run_process_capped(
                    cmd,
                    input_data=stdin_data or "",
                    timeout=s.sandbox_timeout_sec,
                    output_limit=s.sandbox_output_limit_bytes,
                )
            except OSError as exc:
                return _finalize(ExecutionStatus.ERROR, "", str(exc), None, reference)

            if proc.timed_out:
                _force_remove_container(container_name)
                return _finalize(
                    ExecutionStatus.TIMEOUT,
                    proc.stdout,
                    proc.stderr,
                    None,
                    reference,
                    detail=_join_detail(
                        f"Превышен таймаут {s.sandbox_timeout_sec} c",
                        _output_note(proc.output_truncated),
                    ),
                )

            status = ExecutionStatus.OK if proc.returncode == 0 else ExecutionStatus.FAILED
            return _finalize(
                status,
                proc.stdout,
                proc.stderr,
                proc.returncode,
                reference,
                detail=_join_detail("Docker-песочница", _output_note(proc.output_truncated)),
            )


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
        if not s.sandbox_allow_local:
            return ExecutionResult(
                status=ExecutionStatus.SKIPPED,
                detail="Локальный запуск запрещён (SANDBOX_ALLOW_LOCAL=false)",
            )
        with tempfile.TemporaryDirectory(prefix="tsk-sandbox-") as tmp:
            root = Path(tmp)
            try:
                _materialize(bundle, root)
                target = _resolve_inside(root, entrypoint)
            except SandboxError as exc:
                return _finalize(ExecutionStatus.ERROR, "", str(exc), None, reference)

            # Не передаём процессу ученика окружение сервера: там могут быть
            # API_KEY, токены и настройки LLM. Оставляем только необходимое
            # для запуска Python на текущей платформе.
            env = {
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8",
                "HOME": str(root),
                "TMP": str(root),
                "TEMP": str(root),
            }
            for key in (
                "PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
                "PATHEXT", "LANG", "LC_ALL",
            ):
                value = os.environ.get(key)
                if value:
                    env[key] = value
            try:
                proc = _run_process_capped(
                    [sys.executable, str(target)],
                    input_data=stdin_data or "",
                    timeout=s.sandbox_timeout_sec,
                    output_limit=s.sandbox_output_limit_bytes,
                    cwd=str(root),
                    env=env,
                )
            except OSError as exc:
                return _finalize(ExecutionStatus.ERROR, "", str(exc), None, reference)

            if proc.timed_out:
                return _finalize(
                    ExecutionStatus.TIMEOUT,
                    proc.stdout,
                    proc.stderr,
                    None,
                    reference,
                    detail=_join_detail(
                        f"Превышен таймаут {s.sandbox_timeout_sec} c (локальный запуск)",
                        _output_note(proc.output_truncated),
                    ),
                )

            status = ExecutionStatus.OK if proc.returncode == 0 else ExecutionStatus.FAILED
            detail = _join_detail(
                "Локальный запуск без изоляции (явно разрешён)",
                _output_note(proc.output_truncated),
            )
            return _finalize(status, proc.stdout, proc.stderr, proc.returncode, reference, detail)


def _join_detail(*parts: str) -> str:
    return "; ".join(part for part in parts if part)


def create_runner(settings: Settings) -> SandboxRunner:
    if not settings.sandbox_enabled:
        return NoSandbox(settings, "Песочница отключена (SANDBOX_ENABLED=false)")

    backend = (settings.sandbox_backend or "").strip().lower()
    if backend == "local":
        if not settings.sandbox_allow_local:
            return NoSandbox(
                settings,
                "Локальный запуск запрещён: включите SANDBOX_ALLOW_LOCAL=true",
            )
        return LocalSubprocessSandbox(settings)

    if backend != "docker":
        return NoSandbox(settings, f"Неизвестный бэкенд песочницы: {settings.sandbox_backend!r}")

    docker = DockerSandbox(settings)
    if docker.is_available():
        return docker
    if settings.sandbox_allow_local:
        return LocalSubprocessSandbox(settings)
    return NoSandbox(
        settings,
        "Docker недоступен, а локальный запуск запрещён (SANDBOX_ALLOW_LOCAL=false)",
    )


def run_solution(
    runner: SandboxRunner,
    bundle: FileBundle,
    stdin_data: Optional[str],
    reference: Optional[str],
    settings: Settings,
) -> ExecutionResult:
    if isinstance(runner, NoSandbox):
        return ExecutionResult(status=ExecutionStatus.SKIPPED, detail=runner.reason)

    entrypoint = detect_python_entrypoint(bundle)
    if entrypoint is None:
        return ExecutionResult(
            status=ExecutionStatus.SKIPPED,
            detail="Не найден Python-файл для запуска",
        )
    return runner.run(bundle, entrypoint, stdin_data, reference)


class NoSandbox:
    name = "off"

    def __init__(self, settings: Settings, reason: str = "Песочница отключена") -> None:
        self.settings = settings
        self.reason = reason

    @staticmethod
    def is_available() -> bool:
        return False

    def run(self, *args, **kwargs) -> ExecutionResult:
        return ExecutionResult(status=ExecutionStatus.SKIPPED, detail=self.reason)
