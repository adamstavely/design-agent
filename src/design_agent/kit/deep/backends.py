"""Where the agent's file tools and shell commands act: a sandbox, a host folder, or a mix.

`BackendProtocol` is the file interface (list, read, write, edit, glob, grep, delete, upload,
download); `SandboxBackendProtocol` adds `execute`. Paths are absolute POSIX paths as the agent
sees them.

- `BaseSandbox`: a sandbox reached by `execute` plus file upload and download (the Docker runner).
  Listing, globbing and searching run small `python3` scripts inside it.
- `FilesystemBackend`: a host folder. With `virtual_mode`, `/x` means `<root_dir>/x` and nothing
  outside `root_dir` is reachable.
- `LocalShellBackend`: a host folder that also runs commands in it (development and tests only).
- `CompositeBackend`: routes path prefixes to other backends (`/skills/` read-only, the rest to
  the sandbox).
- `StateBackend`: files kept in memory, for tests.
"""

from __future__ import annotations

import abc
import asyncio
import base64
import fnmatch
import json
import os
import posixpath
import re
import shlex
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NotRequired, TypedDict

MAX_OUTPUT_BYTES = 100_000
DEFAULT_READ_LIMIT = 2_000


class FileInfo(TypedDict):
    path: str
    is_dir: NotRequired[bool]
    size: NotRequired[int]
    modified_at: NotRequired[str]


class GrepMatch(TypedDict):
    path: str
    line: int
    text: str


class FileData(TypedDict):
    content: str
    encoding: str


@dataclass
class FileDownloadResponse:
    path: str
    content: bytes | None = None
    error: str | None = None


@dataclass
class FileUploadResponse:
    path: str
    error: str | None = None


@dataclass
class ReadResult:
    error: str | None = None
    file_data: FileData | None = None
    total_lines: int | None = None
    start_line: int | None = None
    end_line: int | None = None


@dataclass
class WriteResult:
    error: str | None = None
    path: str | None = None


@dataclass
class EditResult:
    error: str | None = None
    path: str | None = None
    occurrences: int | None = None


@dataclass
class DeleteResult:
    error: str | None = None
    path: str | None = None


@dataclass
class LsResult:
    error: str | None = None
    entries: list[FileInfo] | None = None


@dataclass
class GrepResult:
    error: str | None = None
    matches: list[GrepMatch] | None = None
    truncated: bool = False


@dataclass
class GlobResult:
    error: str | None = None
    matches: list[FileInfo] | None = None
    truncated: bool = False


@dataclass
class ExecuteResponse:
    output: str
    exit_code: int | None = None
    truncated: bool = False


def _decode(raw: bytes) -> tuple[str, str]:
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return base64.b64encode(raw).decode("ascii"), "base64"


def _replace(
    content: str, old: str, new: str, replace_all: bool
) -> tuple[str | None, int, str | None]:
    count = content.count(old)
    if not old or count == 0:
        return None, 0, "string_not_found"
    if count > 1 and not replace_all:
        return (
            None,
            count,
            f"the string appears {count} times; give more context or set replace_all",
        )
    return (
        content.replace(old, new) if replace_all else content.replace(old, new, 1),
        count if replace_all else 1,
        None,
    )


class BackendProtocol(abc.ABC):  # noqa: B024 - every method has a default
    """The file interface. Async forms run the sync ones on a worker thread."""

    def ls(self, path: str) -> LsResult:
        raise NotImplementedError

    def read(self, file_path: str, offset: int = 0, limit: int = DEFAULT_READ_LIMIT) -> ReadResult:
        responses = self.download_files([file_path])
        response = (
            responses[0] if responses else FileDownloadResponse(file_path, error="file_not_found")
        )
        if response.error or response.content is None:
            return ReadResult(error=response.error or "file_not_found")
        text, encoding = _decode(response.content)
        if encoding == "base64":
            return ReadResult(file_data={"content": text, "encoding": encoding})
        lines = text.splitlines(keepends=True)
        chunk = lines[offset : offset + limit] if limit else lines[offset:]
        return ReadResult(
            file_data={"content": "".join(chunk), "encoding": encoding},
            total_lines=len(lines),
            start_line=offset + 1,
            end_line=offset + len(chunk),
        )

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        raise NotImplementedError

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        raise NotImplementedError

    def write(self, file_path: str, content: str) -> WriteResult:
        existing = self.download_files([file_path])
        if existing and existing[0].content is not None:
            return WriteResult(
                error=f"Cannot write to {file_path} because it already exists. Read and then make an edit, or write to a new path."
            )
        uploaded = self.upload_files([(file_path, content.encode("utf-8"))])
        if uploaded and uploaded[0].error:
            return WriteResult(error=uploaded[0].error)
        return WriteResult(path=file_path)

    def edit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        current = self.download_files([file_path])
        if not current or current[0].content is None:
            return EditResult(error=f"file_not_found: {file_path}")
        text, encoding = _decode(current[0].content)
        if encoding != "utf-8":
            return EditResult(error=f"{file_path} is not a text file")
        updated, count, error = _replace(text, old_string, new_string, replace_all)
        if error is not None or updated is None:
            return EditResult(error=f"{error}: {file_path}")
        uploaded = self.upload_files([(file_path, updated.encode("utf-8"))])
        if uploaded and uploaded[0].error:
            return EditResult(error=uploaded[0].error)
        return EditResult(path=file_path, occurrences=count)

    def delete(self, file_path: str) -> DeleteResult:
        return DeleteResult(error=f"delete is not supported here: {file_path}")

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        raise NotImplementedError

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        raise NotImplementedError

    async def als(self, path: str) -> LsResult:
        return await asyncio.to_thread(self.ls, path)

    async def aread(
        self, file_path: str, offset: int = 0, limit: int = DEFAULT_READ_LIMIT
    ) -> ReadResult:
        return await asyncio.to_thread(self.read, file_path, offset, limit)

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        return await asyncio.to_thread(self.grep, pattern, path, glob)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return await asyncio.to_thread(self.glob, pattern, path)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return await asyncio.to_thread(self.write, file_path, content)

    async def aedit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        return await asyncio.to_thread(self.edit, file_path, old_string, new_string, replace_all)

    async def adelete(self, file_path: str) -> DeleteResult:
        return await asyncio.to_thread(self.delete, file_path)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return await asyncio.to_thread(self.upload_files, files)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return await asyncio.to_thread(self.download_files, paths)


class SandboxBackendProtocol(BackendProtocol):
    @property
    def id(self) -> str:
        return type(self).__name__

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise NotImplementedError

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return await asyncio.to_thread(lambda: self.execute(command, timeout=timeout))


def supports_execution(backend: Any) -> bool:
    return isinstance(backend, SandboxBackendProtocol) or callable(
        getattr(backend, "execute", None)
    )


# -- a sandbox reached by commands --------------------------------------------------------------

_LS = r"""
import json, os, sys, datetime
p = sys.argv[1]
out = []
try:
    for e in sorted(os.scandir(p), key=lambda e: e.name):
        s = e.stat(follow_symlinks=False)
        d = e.is_dir()
        out.append({"path": os.path.join(p, e.name) + ("/" if d else ""), "is_dir": d, "size": s.st_size,
                    "modified_at": datetime.datetime.fromtimestamp(s.st_mtime, datetime.timezone.utc).isoformat()})
except OSError as e:
    print(json.dumps({"error": e.strerror or "unreadable"})); sys.exit(0)
print(json.dumps({"entries": out}))
"""

_GLOB = r"""
import glob, json, os, sys, datetime
root, pattern = sys.argv[1], sys.argv[2]
out = []
for p in sorted(glob.glob(os.path.join(root, pattern), recursive=True))[:1001]:
    try:
        s = os.stat(p)
    except OSError:
        continue
    d = os.path.isdir(p)
    out.append({"path": p + ("/" if d else ""), "is_dir": d, "size": s.st_size,
                "modified_at": datetime.datetime.fromtimestamp(s.st_mtime, datetime.timezone.utc).isoformat()})
print(json.dumps({"matches": out[:1000], "truncated": len(out) > 1000}))
"""

_GREP = r"""
import fnmatch, json, os, re, sys
pattern, root, include = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    rx = re.compile(pattern)
except re.error:
    rx = re.compile(re.escape(pattern))
out = []
paths = [root] if os.path.isfile(root) else (os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs)
for p in paths:
    if include and not fnmatch.fnmatch(os.path.basename(p), include):
        continue
    try:
        with open(p, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if rx.search(line):
                    out.append({"path": p, "line": n, "text": line.rstrip("\n")[:500]})
                    if len(out) > 500:
                        break
    except (OSError, UnicodeDecodeError):
        continue
    if len(out) > 500:
        break
print(json.dumps({"matches": out[:500], "truncated": len(out) > 500}))
"""


class BaseSandbox(SandboxBackendProtocol):
    """A sandbox with `execute`, `upload_files` and `download_files`; the rest runs through them."""

    def _script(self, script: str, *args: str) -> dict[str, Any]:
        encoded = base64.b64encode(script.encode()).decode()
        quoted = " ".join(shlex.quote(a) for a in args)
        command = f"python3 -c \"import base64; exec(base64.b64decode('{encoded}'))\" {quoted}"
        response = self.execute(command)
        lines = (response.output or "").strip().splitlines()
        try:
            return dict(json.loads(lines[-1])) if lines else {"error": "no output"}
        except json.JSONDecodeError:
            return {"error": (response.output or "")[-300:] or f"exit code {response.exit_code}"}

    def ls(self, path: str) -> LsResult:
        data = self._script(_LS, path)
        return LsResult(error=data.get("error"), entries=data.get("entries"))

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        data = self._script(_GLOB, path or "/", pattern.lstrip("/") if path else pattern)
        return GlobResult(
            error=data.get("error"),
            matches=data.get("matches"),
            truncated=bool(data.get("truncated")),
        )

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        data = self._script(_GREP, pattern, path or "/", glob or "")
        return GrepResult(
            error=data.get("error"),
            matches=data.get("matches"),
            truncated=bool(data.get("truncated")),
        )

    def delete(self, file_path: str) -> DeleteResult:
        response = self.execute(f"rm -f -- {shlex.quote(file_path)}")
        if response.exit_code not in (0, None):
            return DeleteResult(error=response.output.strip()[-300:] or "delete failed")
        return DeleteResult(path=file_path)


# -- host folders -------------------------------------------------------------------------------


def _info(path: Path, shown: str) -> FileInfo:
    stat = path.stat()
    is_dir = path.is_dir()
    return {
        "path": shown + ("/" if is_dir and not shown.endswith("/") else ""),
        "is_dir": is_dir,
        "size": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
    }


class FilesystemBackend(BackendProtocol):
    def __init__(
        self,
        root_dir: str | Path | None = None,
        virtual_mode: bool = False,
        max_file_size_mb: int = 10,
    ) -> None:
        self.cwd = Path(root_dir or Path.cwd()).resolve()
        self.virtual_mode = virtual_mode
        self.max_bytes = max_file_size_mb * 1024 * 1024

    def _resolve(self, path: str) -> Path:
        if self.virtual_mode:
            target = (self.cwd / path.lstrip("/")).resolve()
            if target != self.cwd and self.cwd not in target.parents:
                raise PermissionError(f"path outside the root: {path}")
            return target
        candidate = Path(path)
        return candidate if candidate.is_absolute() else (self.cwd / candidate).resolve()

    def _shown(self, target: Path) -> str:
        if not self.virtual_mode:
            return str(target)
        relative = target.relative_to(self.cwd).as_posix()
        return "/" + ("" if relative == "." else relative)

    def ls(self, path: str) -> LsResult:
        try:
            target = self._resolve(path)
        except PermissionError as error:
            return LsResult(error=str(error))
        if not target.is_dir():
            return LsResult(error=f"not a directory: {path}")
        return LsResult(entries=[_info(c, self._shown(c)) for c in sorted(target.iterdir())])

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        try:
            base = self._resolve(path or "/")
        except PermissionError as error:
            return GlobResult(error=str(error))
        found = sorted(base.glob(pattern.lstrip("/")))
        return GlobResult(
            matches=[_info(p, self._shown(p)) for p in found[:1000]], truncated=len(found) > 1000
        )

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        try:
            base = self._resolve(path or "/")
        except PermissionError as error:
            return GrepResult(error=str(error))
        try:
            rx = re.compile(pattern)
        except re.error:
            rx = re.compile(re.escape(pattern))
        files = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
        matches: list[GrepMatch] = []
        for file in files:
            if glob and not fnmatch.fnmatch(file.name, glob):
                continue
            try:
                for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
                    if rx.search(line):
                        matches.append(
                            {"path": self._shown(file), "line": number, "text": line[:500]}
                        )
            except (OSError, UnicodeDecodeError):
                continue
            if len(matches) > 500:
                return GrepResult(matches=matches[:500], truncated=True)
        return GrepResult(matches=matches)

    def delete(self, file_path: str) -> DeleteResult:
        try:
            target = self._resolve(file_path)
            target.unlink()
        except (OSError, PermissionError) as error:
            return DeleteResult(error=str(error))
        return DeleteResult(path=file_path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        out: list[FileUploadResponse] = []
        for path, content in files:
            try:
                target = self._resolve(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                out.append(FileUploadResponse(path=path))
            except (OSError, PermissionError) as error:
                out.append(FileUploadResponse(path=path, error=str(error)))
        return out

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        out: list[FileDownloadResponse] = []
        for path in paths:
            try:
                target = self._resolve(path)
                if not target.is_file():
                    out.append(FileDownloadResponse(path=path, error="file_not_found"))
                    continue
                out.append(FileDownloadResponse(path=path, content=target.read_bytes()))
            except (OSError, PermissionError) as error:
                out.append(FileDownloadResponse(path=path, error=str(error)))
        return out


class LocalShellBackend(FilesystemBackend, SandboxBackendProtocol):
    """A host folder whose commands run on this machine, in that folder. Not isolation."""

    def __init__(
        self,
        root_dir: str | Path | None = None,
        *,
        virtual_mode: bool = False,
        timeout: int = 120,
        max_output_bytes: int = MAX_OUTPUT_BYTES,
        env: dict[str, str] | None = None,
        inherit_env: bool = False,
    ) -> None:
        super().__init__(root_dir=root_dir, virtual_mode=virtual_mode)
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes
        base = (
            dict(os.environ) if inherit_env else {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        )
        self.env = {**base, **(env or {})}

    @property
    def id(self) -> str:
        return f"local:{self.cwd}"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        try:
            done = subprocess.run(  # noqa: S602 - the agent's own shell command, by design
                command,
                shell=True,
                cwd=self.cwd,
                env=self.env,
                capture_output=True,
                timeout=timeout or self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ExecuteResponse(
                output=f"Command timed out after {timeout or self.timeout} seconds", exit_code=124
            )
        output = (done.stdout + done.stderr).decode("utf-8", errors="replace")
        truncated = len(output.encode()) > self.max_output_bytes
        if truncated:
            output = output.encode()[: self.max_output_bytes].decode("utf-8", errors="ignore")
        return ExecuteResponse(output=output, exit_code=done.returncode, truncated=truncated)


# -- routing ------------------------------------------------------------------------------------


class CompositeBackend(SandboxBackendProtocol):
    """Send each path to the backend of its longest matching route prefix, else `default`."""

    def __init__(self, default: BackendProtocol, routes: dict[str, BackendProtocol]) -> None:
        self.default = default
        self.routes = dict(sorted(routes.items(), key=lambda kv: -len(kv[0])))

    def _route(self, path: str) -> tuple[BackendProtocol, str, str]:
        for prefix, backend in self.routes.items():
            bare = prefix.rstrip("/")
            if path == bare or path.startswith(prefix):
                inner = "/" + path[len(prefix) :] if path.startswith(prefix) else "/"
                return backend, inner, bare
        return self.default, path, ""

    def _out(self, prefix: str, path: str) -> str:
        return prefix + path if prefix else path

    @property
    def id(self) -> str:
        return getattr(self.default, "id", "composite")

    def ls(self, path: str) -> LsResult:
        backend, inner, prefix = self._route(path)
        result = backend.ls(inner)
        if result.entries is not None and prefix:
            result.entries = [{**e, "path": self._out(prefix, e["path"])} for e in result.entries]
        if not prefix and result.entries is not None and path.rstrip("/") in ("", "/"):
            shown = {e["path"] for e in result.entries}
            for route in self.routes:
                if route not in shown:
                    result.entries.append({"path": route, "is_dir": True})
        return result

    def read(self, file_path: str, offset: int = 0, limit: int = DEFAULT_READ_LIMIT) -> ReadResult:
        backend, inner, _ = self._route(file_path)
        return backend.read(inner, offset, limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        backend, inner, _ = self._route(file_path)
        result = backend.write(inner, content)
        if result.path is not None:
            result.path = file_path
        return result

    def edit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        backend, inner, _ = self._route(file_path)
        result = backend.edit(inner, old_string, new_string, replace_all)
        if result.path is not None:
            result.path = file_path
        return result

    def delete(self, file_path: str) -> DeleteResult:
        backend, inner, _ = self._route(file_path)
        return backend.delete(inner)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        backend, inner, prefix = self._route(path or "/")
        result = backend.glob(pattern, inner)
        if result.matches is not None and prefix:
            result.matches = [{**m, "path": self._out(prefix, m["path"])} for m in result.matches]
        return result

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        backend, inner, prefix = self._route(path or "/")
        result = backend.grep(pattern, inner, glob)
        if result.matches is not None and prefix:
            result.matches = [{**m, "path": self._out(prefix, m["path"])} for m in result.matches]
        return result

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        out: list[FileUploadResponse] = []
        for path, content in files:
            backend, inner, _ = self._route(path)
            response = backend.upload_files([(inner, content)])[0]
            out.append(FileUploadResponse(path=path, error=response.error))
        return out

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        out: list[FileDownloadResponse] = []
        for path in paths:
            backend, inner, _ = self._route(path)
            response = backend.download_files([inner])[0]
            out.append(
                FileDownloadResponse(path=path, content=response.content, error=response.error)
            )
        return out

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        if not supports_execution(self.default):
            return ExecuteResponse(output="This backend cannot run commands.", exit_code=1)
        response: ExecuteResponse = self.default.execute(command, timeout=timeout)  # type: ignore[attr-defined]
        return response


class StateBackend(BackendProtocol):
    """Files in memory (tests)."""

    def __init__(self, *_: Any, **__: Any) -> None:
        self.files: dict[str, bytes] = {}

    def ls(self, path: str) -> LsResult:
        base = path.rstrip("/") + "/"
        children: dict[str, bool] = {}
        for name in self.files:
            if name.startswith(base):
                rest = name[len(base) :]
                head = rest.split("/", 1)[0]
                children[base + head] = "/" in rest
        return LsResult(
            entries=[
                {"path": p + ("/" if d else ""), "is_dir": d} for p, d in sorted(children.items())
            ]
        )

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        base = (path or "/").rstrip("/") + "/"
        found = [
            p
            for p in self.files
            if p.startswith(base) and fnmatch.fnmatch(p[len(base) :], pattern.lstrip("/"))
        ]
        return GlobResult(matches=[{"path": p, "is_dir": False} for p in sorted(found)])

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        rx = re.compile(re.escape(pattern))
        matches: list[GrepMatch] = []
        for name, raw in sorted(self.files.items()):
            if path and not name.startswith(path):
                continue
            if glob and not fnmatch.fnmatch(posixpath.basename(name), glob):
                continue
            for number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    matches.append({"path": name, "line": number, "text": line})
        return GrepResult(matches=matches)

    def delete(self, file_path: str) -> DeleteResult:
        self.files.pop(file_path, None)
        return DeleteResult(path=file_path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        for path, content in files:
            self.files[path] = content
        return [FileUploadResponse(path=p) for p, _ in files]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [
            FileDownloadResponse(path=p, content=self.files[p])
            if p in self.files
            else FileDownloadResponse(path=p, error="file_not_found")
            for p in paths
        ]
