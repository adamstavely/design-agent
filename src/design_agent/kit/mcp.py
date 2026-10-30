"""MCP servers' tools as agent tools, through the FastMCP client.

`MCPAdapter(transport)` lists a server's tools (`async with MCPAdapter(t) as a: await
a.list_tools()`); each becomes a tool whose schema is the server's input schema. A call opens a session on the same transport, runs the tool and turns its
result into content blocks (text, images, files); a result the server marks as an error comes back
as an error tool message with the server's own text. Structured content rides along as the
message's artifact.
"""

from __future__ import annotations

from typing import Any

from design_agent.kit.messages import ToolMessage
from design_agent.kit.tools import StructuredTool, ToolRuntime


def _block(item: Any) -> dict[str, Any] | None:
    kind = getattr(item, "type", None)
    if kind == "text":
        return {"type": "text", "text": item.text}
    if kind in ("image", "audio"):
        return {"type": kind, "base64": item.data, "mime_type": item.mimeType}
    if kind == "resource":
        resource = item.resource
        text = getattr(resource, "text", None)
        if text is not None:
            return {"type": "text", "text": text}
        blob = getattr(resource, "blob", None)
        if blob is not None:
            return {
                "type": "file",
                "base64": blob,
                "mime_type": getattr(resource, "mimeType", None) or "application/octet-stream",
            }
    if kind == "resource_link":
        return {
            "type": "text",
            "text": f"[resource] {getattr(item, 'name', '')} {getattr(item, 'uri', '')}".strip(),
        }
    return None


def _content(result: Any) -> list[dict[str, Any]]:
    blocks = [
        b for b in (_block(i) for i in getattr(result, "content", None) or []) if b is not None
    ]
    return blocks or [{"type": "text", "text": "(no output)"}]


def _tool(transport: Any, spec: Any) -> StructuredTool:
    name = spec.name

    async def call(runtime: ToolRuntime | None = None, **arguments: Any) -> Any:
        from fastmcp import Client

        async with Client(transport) as client:
            result = await client.call_tool(name, arguments, raise_on_error=False)
        content = _content(result)
        structured = getattr(result, "structured_content", None)
        artifact = {"structured_content": structured} if structured is not None else None
        if getattr(result, "is_error", False):
            return ToolMessage(
                content=content,
                tool_call_id=str(getattr(runtime, "tool_call_id", "") or ""),
                name=name,
                status="error",
                artifact=artifact,
            )
        return content, artifact

    schema = dict(spec.inputSchema or {"type": "object", "properties": {}})
    meta = {
        "mcp": {
            "tool": {
                "annotations": spec.annotations.model_dump(exclude_none=True)
                if spec.annotations
                else {}
            }
        }
    }
    return StructuredTool(
        name=name,
        description=spec.description or "",
        args_schema=schema,
        coroutine=call,
        runtime_param="runtime",
        response_format="content_and_artifact",
        metadata=meta,
    )


class MCPAdapter:
    """A server's tools as agent tools. The transport is anything `fastmcp.Client` accepts."""

    def __init__(self, transport: Any) -> None:
        self.transport = transport
        self._client: Any = None

    async def __aenter__(self) -> MCPAdapter:
        from fastmcp import Client

        self._client = Client(self.transport)
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None:
            await self._client.__aexit__(*exc)
            self._client = None

    async def list_tools(self) -> list[StructuredTool]:
        if self._client is None:
            raise RuntimeError("use MCPAdapter as an async context manager")
        return [_tool(self.transport, spec) for spec in await self._client.list_tools()]


async def load_mcp_tools(transport: Any) -> list[StructuredTool]:
    async with MCPAdapter(transport) as adapter:
        return await adapter.list_tools()
