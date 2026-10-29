"""A model that answers with a fixed list of messages, for tests."""

from __future__ import annotations

from typing import Any

from design_agent.kit.messages import AIMessage, BaseMessage
from design_agent.kit.models.base import BaseChatModel, ChatGeneration, ChatResult


class FakeMessagesListChatModel(BaseChatModel):
    responses: list[BaseMessage]
    i: int = 0
    provider_name: str = "fake"
    model_name: str = "fake"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        response = self.responses[self.i]
        self.i = self.i + 1 if self.i < len(self.responses) - 1 else 0
        message = (
            response if isinstance(response, AIMessage) else AIMessage(content=response.content)
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


GenericFakeChatModel = FakeMessagesListChatModel
