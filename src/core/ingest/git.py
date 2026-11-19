"""Разбор каталога ``.git`` из архива посылки → текстовый отчёт по истории.

TaskCheckerAI пропускает ``.git`` как служебный каталог (см. ``loaders.SKIP_NAMES``),
поэтому история репозитория не попадает в контекст модели. Для заданий про Git это
критично: без неё не видно веток, коммитов и ``.gitignore``. Модуль собирает из
файлов ``.git`` короткий текстовый отчёт (HEAD, ветки, ссылки, коммиты по
loose-объектам, при их отсутствии — reflog), который затем добавляется к файлам
проекта как обычный файл ``git-history.txt``.

Разбор идёт только по уже распакованным в память файлам ``.git``; сам репозиторий
не открывается и код из него не исполняется. Упакованные объекты
(``.git/objects/pack/...``) пропускаются: их сжатое содержимое бесполезно без
распаковки pack-файла, а при отсутствии loose-объектов используется reflog.
"""
from __future__ import annotations

import re
import zlib
from datetime import datetime, timezone
from pathlib import PurePosixPath

GIT_DIR_NAME = ".git"
# Имя файла с отчётом по истории ``.git`` (уходит в контекст как обычный файл).
GIT_REPORT_NAME = "git-history.txt"
# Отчёт в символах: он уходит в контекст модели вместе с файлами проекта.
MAX_REPORT_CHARS = 20_000
# Сколько коммитов максимум разбирать (защита от огромной истории).
MAX_COMMITS = 300


def is_git_path(name: str) -> bool:
    """Путь лежит внутри каталога ``.git``."""
    return GIT_DIR_NAME in PurePosixPath(name).parts


def is_git_pack(name: str) -> bool:
    """Путь — упакованный объект (``.git/objects/pack/...``), для отчёта бесполезен."""
    parts = PurePosixPath(name).parts
    return "objects" in parts and "pack" in parts


def _find_git_dir(members: dict[str, bytes]) -> tuple[str, str] | None:
    """Найти каталог ``.git`` в файлах архива; вернуть ``(prefix, gitdir)``.

    ``prefix`` — путь до корня проекта (пусто, если ``.git`` в корне), ``gitdir`` —
    путь до самого ``.git`` (``prefix + ".git"``). При нескольких ``.git`` берётся
    самый верхний (с кратчайшим префиксом).
    """
    best: tuple[int, str] | None = None
    for name in members:
        parts = name.split("/")
        if GIT_DIR_NAME not in parts:
            continue
        index = parts.index(GIT_DIR_NAME)
        prefix = "/".join(parts[:index])
        if best is None or len(prefix) < len(best[1]):
            best = (index, prefix)
    if best is None:
        return None
    prefix = best[1]
    gitdir = f"{prefix}/{GIT_DIR_NAME}" if prefix else GIT_DIR_NAME
    return prefix, gitdir


def _git_files(members: dict[str, bytes], gitdir: str) -> dict[str, bytes]:
    """Файлы внутри ``.git`` с путями относительно самого каталога."""
    prefix = gitdir + "/"
    return {name[len(prefix):]: data for name, data in members.items() if name.startswith(prefix)}


def _fmt_time(timestamp: str, tz: str) -> str:
    """Время коммита ``2026-11-01 12:00`` по смещению ``+0300``."""
    try:
        seconds = int(timestamp)
    except (TypeError, ValueError):
        return ""
    offset = 0
    match = re.match(r"^([+-])(\d{2})(\d{2})$", tz or "")
    if match:
        sign = 1 if match.group(1) == "+" else -1
        offset = sign * (int(match.group(2)) * 3600 + int(match.group(3)) * 60)
    moment = datetime.fromtimestamp(seconds + offset, tz=timezone.utc)
    return moment.strftime("%Y-%m-%d %H:%M")


def _parse_person(line: str) -> tuple[str, str]:
    """Разобрать ``Имя <email> 1699999999 +0300`` -> ``(имя, время)``."""
    match = re.match(r"^(.*?)\s*<([^>]*)>\s*(\d+)?\s*([+-]\d{4})?$", line.strip())
    if not match:
        return line.strip(), ""
    name = match.group(1).strip() or match.group(2)
    return name, _fmt_time(match.group(3) or "", match.group(4) or "")


def _parse_commit(data: bytes) -> dict | None:
    """Разобрать loose-объект коммита (``.git/objects/xx/yyyy``)."""
    try:
        raw = zlib.decompress(data)
    except zlib.error:
        return None
    header, _, body = raw.partition(b"\x00")
    if not header.startswith(b"commit "):
        return None
    lines = body.decode("utf-8", "replace").split("\n")
    parents: list[str] = []
    author = ""
    index = 0
    while index < len(lines) and lines[index]:
        line = lines[index]
        if line.startswith("parent "):
            parents.append(line[7:].strip())
        elif line.startswith("author "):
            author = line[7:]
        index += 1
    message = "\n".join(lines[index + 1:]).strip()
    name, when = _parse_person(author)
    return {"parents": parents, "author": name, "time": when, "message": message}


def _read_refs(git: dict[str, bytes]) -> dict[str, str]:
    """Ссылки репозитория: loose ``refs/*`` + ``packed-refs``."""
    refs: dict[str, str] = {}
    for name, data in git.items():
        if name.startswith("refs/") and not name.endswith("/"):
            value = data.decode("utf-8", "replace").strip()
            if value:
                refs[name] = value
    packed = git.get("packed-refs")
    if packed:
        for line in packed.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "^")):
                continue
            parts = line.split(" ", 1)
            if len(parts) == 2:
                refs[parts[1].strip()] = parts[0].strip()
    return refs


def _walk_commits(git: dict[str, bytes], tips: list[str]) -> list[dict]:
    """Пройти историю коммитов от вершин по loose-объектам (свежие сверху)."""
    objects: dict[str, bytes] = {}
    for name, data in git.items():
        if not name.startswith("objects/"):
            continue
        rest = name[len("objects/"):]
        if "/" not in rest:
            continue
        folder, _, obj = rest.partition("/")
        if len(folder) == 2 and len(obj) == 38:
            objects[folder + obj] = data

    commits: list[dict] = []
    seen: set[str] = set()
    queue = [tip for tip in tips if tip]
    while queue and len(commits) < MAX_COMMITS:
        sha = queue.pop(0)
        if not sha or sha in seen:
            continue
        seen.add(sha)
        parsed = _parse_commit(objects[sha]) if sha in objects else None
        if parsed is None:
            continue
        parsed["sha"] = sha
        commits.append(parsed)
        queue.extend(parsed["parents"])
    return commits


def _reflog_commits(git: dict[str, bytes]) -> list[dict]:
    """История из reflog (``.git/logs``) — запасной источник при packed-объектах."""
    entries: list[dict] = []
    for name, data in git.items():
        if not name.startswith("logs/"):
            continue
        for line in data.decode("utf-8", "replace").splitlines():
            if "\t" not in line:
                continue
            left, message = line.split("\t", 1)
            parts = left.split()
            if len(parts) < 2:
                continue
            new_sha = parts[1]
            # "<old> <new> <author> <ts> <tz>\t<message>"
            rest = parts[2:]
            person = " ".join(rest[: len(rest) - 2]) if len(rest) >= 3 else ""
            stamp = rest[-2] if len(rest) >= 2 else ""
            tz = rest[-1] if rest else ""
            name_part, _ = _parse_person(person)
            entries.append(
                {
                    "sha": new_sha,
                    "author": name_part,
                    "time": _fmt_time(stamp, tz),
                    "message": message.strip(),
                }
            )
    return entries


def git_report(members: dict[str, bytes]) -> tuple[str | None, str]:
    """Отчёт по ``.git`` из файлов архива.

    ``members`` — карта ``путь -> байты`` распакованных файлов (пути внутри
    архива). Возвращает ``(текст_отчёта, путь_каталога_git)``; текст ``None``,
    если каталога ``.git`` нет.
    """
    found = _find_git_dir(members)
    if found is None:
        return None, ""
    _prefix, gitdir = found
    git = _git_files(members, gitdir)

    head = git.get("HEAD", b"").decode("utf-8", "replace").strip()
    refs = _read_refs(git)
    if head.startswith("ref: "):
        current = head[5:].strip()
        tip = refs.get(current, "")
    else:
        current = ""
        tip = head

    branches = sorted(
        name[len("refs/heads/"):]
        for name in refs
        if name.startswith("refs/heads/")
    )
    tips = list({tip} | set(refs.values()))
    commits = _walk_commits(git, tips)

    lines = [f"Git-репозиторий: {gitdir}"]
    if current:
        lines.append(f"Текущая ветка: {current[len('refs/heads/'):]}")
    elif head:
        lines.append("HEAD отсоединён (detached)")
    if branches:
        lines.append("Ветки: " + ", ".join(branches))
    remotes = sorted(
        name[len("refs/remotes/"):]
        for name in refs
        if name.startswith("refs/remotes/")
    )
    if remotes:
        lines.append("Удалённые ветки: " + ", ".join(remotes))

    if commits:
        commits.sort(key=lambda c: c.get("time", ""), reverse=True)
        lines.append(f"Коммитов (достижимо от ссылок): {len(commits)}")
        lines.append("")
        lines.append("История коммитов (свежие сверху):")
        for commit in commits:
            subject = (commit.get("message") or "").split("\n", 1)[0]
            lines.append(f"* {commit['sha'][:10]} {commit.get('time', '')} {commit.get('author', '')}")
            if subject:
                lines.append(f"  {subject}")
    else:
        reflog = _reflog_commits(git)
        if reflog:
            lines.append(f"Коммитов (из reflog): {len(reflog)}")
            lines.append("")
            lines.append("История (из reflog, свежие сверху):")
            for entry in reversed(reflog):
                lines.append(f"* {entry['sha'][:10]} {entry.get('time', '')} {entry.get('author', '')}")
                if entry.get("message"):
                    lines.append(f"  {entry['message']}")
        else:
            lines.append("История коммитов недоступна (объекты упакованы или отсутствуют).")

    report = "\n".join(lines)
    if len(report) > MAX_REPORT_CHARS:
        report = report[:MAX_REPORT_CHARS] + "\n… (отчёт обрезан)"
    return report, gitdir
