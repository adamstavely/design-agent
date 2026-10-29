"""The agent's file and shell tools over a backend: `ls`, `read_file`, `write_file`, `edit_file`,
`glob`, `grep` and `execute`.

`read_file` numbers lines (`cat -n` style) and pages with `offset`/`limit`; an image comes back as
an image block the model can see. `execute` returns the combined output and a status line with
the exit code, cut to a size limit.
"""

from __future__ import annotations

import mimetypes
import posixpath
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from design_agent.kit.deep.backends import (
    DEFAULT_READ_LIMIT,
    MAX_OUTPUT_BYTES,
    BackendProtocol,
    supports_execution,
)
from design_agent.kit.messages import ToolMessage
from design_agent.kit.middleware import AgentMiddleware
from design_agent.kit.tools import StructuredTool, ToolRuntime

IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_LINE_CHARS = 2_000

LS = "List a directory's files and folders. `path` is absolute."
READ = (
    "Read a file. Text comes back with line numbers; use `offset` (0-based line) and `limit` to page "
    f"through long files (default {DEFAULT_READ_LIMIT} lines). Images come back as images you can see."
)
WRITE = "Create a new file with `content`. It fails if the file exists: read it and use edit_file instead."
EDIT = (
    "Replace `old_string` with `new_string` in a file. `old_string` must match exactly and be unique "
    "unless `replace_all` is true. Read the file first."
)
GLOB = "Find files by glob pattern (`**/*.html`) under `path` (default `/`)."
GREP = "Search file contents for a regular expression under `path`; `glob` filters file names."
EXECUTE = (
    "Run a shell command in the sandbox and return its output and exit code (long output is cut). "
    "Commands run from the sandbox's working directory; use absolute paths."
)


def _numbered(text: str, start: int) -> str:
    lines = text.splitlines()
    out = []
    for index, line in enumerate(lines, start):
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + " [line cut]"
        out.append(f"{index:6d}\t{line}")
    return "\n".join(out)


def format_execute(output: str, exit_code: int | None, *, truncated: bool) -> str:
    parts = [output]
    if exit_code is not None:
        status = "succeeded" if exit_code == 0 else "failed"
        parts.append(f"\n[Command {status} with exit code {exit_code}]")
    if truncated:
        parts.append("\n[Output was truncated due to size limits]")
    return "".join(parts)


class FilesystemMiddleware(AgentMiddleware[Any, Any, Any]):
    def __init__(
        self, *, backend: BackendProtocol, max_output_bytes: int = MAX_OUTPUT_BYTES
    ) -> None:
        super().__init__()
        self.backend = backend
        self.max_output_bytes = max_output_bytes
        self.tools = self._build()

    def _build(self) -> list[StructuredTool]:
        backend = self.backend
        limit_bytes = self.max_output_bytes

        async def ls(path: str = "/") -> str:
            result = await backend.als(path)
            if result.error:
                return f"Error: {result.error}"
            return "\n".join(e["path"] for e in result.entries or []) or f"{path} is empty."

        async def read_file(
            file_path: str, runtime: ToolRuntime, offset: int = 0, limit: int = DEFAULT_READ_LIMIT
        ) -> ToolMessage | str:
            suffix = posixpath.splitext(file_path)[1].lower()
            if suffix in IMAGE_TYPES or suffix == ".pdf":
                downloaded = await backend.adownload_files([file_path])
                item = downloaded[0] if downloaded else None
                if item is None or item.content is None:
                    return f"Error: {item.error if item else 'file_not_found'}: {file_path}"
                import base64

                mime = mimetypes.guess_type("f" + suffix)[0] or "application/octet-stream"
                kind = "image" if suffix in IMAGE_TYPES else "file"
                return ToolMessage(
                    content=[
                        {
                            "type": kind,
                            "base64": base64.b64encode(item.content).decode(),
                            "mime_type": mime,
                        }
                    ],
                    tool_call_id=str(runtime.tool_call_id or ""),
                    name="read_file",
                    additional_kwargs={"read_file_path": file_path, "read_file_media_type": mime},
                )
            result = await backend.aread(file_path, offset, limit)
            if result.error or result.file_data is None:
                return f"Error: {result.error or 'file_not_found'}: {file_path}"
            if result.file_data["encoding"] != "utf-8":
                return f"{file_path} is a binary file; inspect it with a command instead."
            content = result.file_data["content"]
            if not content:
                return (
                    "System reminder: File exists but has empty contents"
                    if offset == 0
                    else f"Error: offset {offset} is past the end of {file_path} ({result.total_lines} lines)"
                )
            body = _numbered(content, offset + 1)
            if result.total_lines and result.end_line and result.end_line < result.total_lines:
                body += f"\n\n[Lines {offset + 1}-{result.end_line} of {result.total_lines}; read on with offset={result.end_line}]"
            return body

        async def write_file(file_path: str, content: str) -> str:
            result = await backend.awrite(file_path, content)
            return f"Error: {result.error}" if result.error else f"Updated file {result.path}"

        async def edit_file(
            file_path: str, old_string: str, new_string: str, replace_all: bool = False
        ) -> str:
            result = await backend.aedit(file_path, old_string, new_string, replace_all)
            if result.error:
                return f"Error: {result.error}"
            return f"Successfully replaced {result.occurrences} instance(s) of the string in '{result.path}'"

        async def glob(pattern: str, path: str = "/") -> str:
            result = await backend.aglob(pattern, path)
            if result.error:
                return f"Error: {result.error}"
            paths = [m["path"] for m in result.matches or []]
            text = "\n".join(paths) if paths else "No files found."
            return text + (
                "\n[More matches were cut; narrow the pattern]" if result.truncated else ""
            )

        async def grep(
            pattern: str,
            path: str = "/",
            glob: str | None = None,
            output_mode: Literal["files_with_matches", "content", "count"] = "files_with_matches",
        ) -> str:
            result = await backend.agrep(pattern, path, glob)
            if result.error:
                return f"Error: {result.error}"
            matches = result.matches or []
            if not matches:
                return f"No matches for {pattern!r}."
            if output_mode == "content":
                text = "\n".join(f"{m['path']}:{m['line']}: {m['text']}" for m in matches)
            elif output_mode == "count":
                counts: dict[str, int] = {}
                for m in matches:
                    counts[m["path"]] = counts.get(m["path"], 0) + 1
                text = "\n".join(f"{p}: {n}" for p, n in counts.items())
            else:
                text = "\n".join(dict.fromkeys(m["path"] for m in matches))
            return text + ("\n[More matches were cut]" if result.truncated else "")

        async def execute(command: str, timeout: int | None = None) -> str:
            if not supports_execution(backend):
                return "Error: this agent's backend cannot run commands."
            response = await backend.aexecute(command, timeout=timeout)  # type: ignore[attr-defined]
            output = response.output or ""
            truncated = response.truncated
            if len(output.encode()) > limit_bytes:
                output = output.encode()[:limit_bytes].decode("utf-8", errors="ignore")
                truncated = True
            return format_execute(output, response.exit_code, truncated=truncated)

        specs: list[tuple[Callable[..., Awaitable[Any]], str]] = [
            (ls, LS),
            (read_file, READ),
            (write_file, WRITE),
            (edit_file, EDIT),
            (glob, GLOB),
            (grep, GREP),
            (execute, EXECUTE),
        ]
        return [
            StructuredTool.from_function(coroutine=f, name=f.__name__, description=d)
            for f, d in specs
        ]
