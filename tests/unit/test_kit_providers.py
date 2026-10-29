"""The provider adapters on the wire, offline: what each sends and how its reply is read.

Each SDK client gets a mock transport, so these run the real `anthropic` and `openai` request code
and response parsing (streaming, for Anthropic) without a key or a network.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic
import httpx2
import openai

from design_agent.kit.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from design_agent.kit.models.anthropic import ChatAnthropic
from design_agent.kit.models.openai import ChatOpenAI
from design_agent.kit.tools import tool


@tool
def make_card(title: str, width: int = 1200) -> str:
    """Make a card."""
    return title * width


def _sse(events: list[dict[str, Any]]) -> bytes:
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


def _anthropic_reply() -> bytes:
    usage = {
        "input_tokens": 10,
        "output_tokens": 0,
        "cache_read_input_tokens": 900,
        "cache_creation_input_tokens": 90,
    }
    return _sse(
        [
            {
                "type": "message_start",
                "message": {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-opus-5-5",
                    "content": [],
                    "stop_reason": None,
                    "usage": usage,
                },
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Making it."},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_2",
                    "name": "make_card",
                    "input": {},
                },
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '{"title": "Launch"}'},
            },
            {"type": "content_block_stop", "index": 1},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 20},
            },
            {"type": "message_stop"},
        ]
    )


async def test_anthropic_sends_one_valid_request_and_reads_the_tool_call() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(
            200, content=_anthropic_reply(), headers={"content-type": "text/event-stream"}
        )

    model = ChatAnthropic(model="claude-opus-5-5", effort="high", thinking={"type": "adaptive"})
    model._client = anthropic.AsyncAnthropic(
        api_key="test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    history = [
        SystemMessage(content="You design."),
        HumanMessage(content="Make a card."),
        AIMessage(
            content=[
                {"type": "thinking", "thinking": "plan", "signature": "sig"},
                {"type": "text", "text": "Checking."},
            ],
            tool_calls=[{"name": "ls", "args": {"path": "/"}, "id": "toolu_1"}],
        ),
        ToolMessage(content="/workspace/", tool_call_id="toolu_1"),
        HumanMessage(content="Also make it blue."),
    ]
    reply = await model.bind_tools(
        [make_card], cache_control={"type": "ephemeral", "ttl": "1h"}
    ).ainvoke(history)

    (body,) = sent
    assert body["system"] == [{"type": "text", "text": "You design."}]
    assert body["output_config"] == {"effort": "high"}
    assert body["thinking"] == {"type": "adaptive"}
    assert body["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert body["tools"][0]["name"] == "make_card"
    assert body["tools"][0]["input_schema"]["required"] == ["title"]
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "assistant", "user"], "same-role turns merge"
    assistant = body["messages"][1]["content"]
    assert [b["type"] for b in assistant] == ["thinking", "text", "tool_use"]
    follow_up = body["messages"][2]["content"]
    assert follow_up[0] == {
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": "/workspace/",
    }
    assert follow_up[1] == {"type": "text", "text": "Also make it blue."}

    assert reply.tool_calls == [
        {"name": "make_card", "args": {"title": "Launch"}, "id": "toolu_2", "type": "tool_call"}
    ]
    assert reply.text == "Making it."
    assert reply.usage_metadata is not None
    assert reply.usage_metadata["input_tokens"] == 1000
    assert reply.usage_metadata["input_token_details"]["cache_read"] == 900
    assert reply.response_metadata["stop_reason"] == "tool_use"


async def test_openai_responses_sends_instructions_tools_and_tool_results() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "resp_1",
                "object": "response",
                "created_at": 0,
                "model": "gpt-5.5",
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "id": "fc_1",
                        "call_id": "call_2",
                        "name": "make_card",
                        "arguments": '{"title": "Launch"}',
                        "status": "completed",
                    }
                ],
                "parallel_tool_calls": True,
                "tool_choice": "auto",
                "tools": [],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 5,
                    "total_tokens": 105,
                    "input_tokens_details": {"cached_tokens": 40},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    model = ChatOpenAI(model="gpt-5.5", reasoning={"effort": "high"})
    model._client = openai.AsyncOpenAI(
        api_key="test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    reply = await model.bind_tools([make_card]).ainvoke(
        [
            SystemMessage(content="You design."),
            HumanMessage(content="Make a card."),
            AIMessage(content="", tool_calls=[{"name": "ls", "args": {}, "id": "call_1"}]),
            ToolMessage(
                content=[
                    {"type": "text", "text": "a frame"},
                    {"type": "image", "base64": "iVBORw0KGgo=", "mime_type": "image/png"},
                ],
                tool_call_id="call_1",
            ),
        ]
    )

    (body,) = sent
    assert body["instructions"] == "You design."
    assert body["reasoning"] == {"effort": "high"}
    assert body["store"] is False
    assert body["tools"][0]["name"] == "make_card"
    kinds = [item.get("type") or item.get("role") for item in body["input"]]
    assert kinds == ["user", "function_call", "function_call_output", "user"]
    assert body["input"][2] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "a frame",
    }
    assert body["input"][3]["content"][-1]["type"] == "input_image", "a tool's image follows it"

    assert reply.tool_calls[0]["name"] == "make_card"
    assert reply.tool_calls[0]["args"] == {"title": "Launch"}
    assert reply.usage_metadata is not None
    assert reply.usage_metadata["input_token_details"] == {"cache_read": 40}
