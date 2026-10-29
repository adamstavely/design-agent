"""The agent runtime this project runs on: messages, tools, chat models, middleware, the agent loop,
stores and the deep-agent harness (file tools, skills, subagents).

It is self-contained and talks to model providers through their own SDKs (`anthropic`, `openai`).
Its names follow the interfaces the rest of the code was written against (`AIMessage`,
`AgentMiddleware`, `ModelRequest`, `create_deep_agent` and so on), so the agent's middleware and
tools read the same as before; nothing here depends on LangChain, LangGraph or LangSmith.
"""
