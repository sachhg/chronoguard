"""Shared test doubles."""

from __future__ import annotations

import json
from typing import Any

from chronoguard.ollama import ChatResponse


class ScriptedClient:
    """Replays a queue of canned replies and records what it was sent.

    Stands in for OllamaClient so both agent modes can be driven turn by turn,
    including the ugly output small models actually produce.
    """

    def __init__(self, replies: list[dict[str, Any]], *, tools: bool = False) -> None:
        self.replies = list(replies)
        self.tools = tools
        self.requests: list[dict[str, Any]] = []
        self.host = "http://scripted"

    def supports_tools(self, model: str) -> bool:
        return self.tools

    def pick_model(self, *, prefer_tools: bool = False) -> str:
        return "scripted-model"

    def chat(self, model: str, messages: Any, *, tools: Any = None, **kwargs: Any) -> ChatResponse:
        self.requests.append({"model": model, "messages": list(messages), "tools": tools})
        if not self.replies:
            return ChatResponse.model_validate({"message": {"role": "assistant", "content": "done"}})
        return ChatResponse.model_validate({"message": self.replies.pop(0)})


def text(content: str) -> dict[str, Any]:
    return {"role": "assistant", "content": content}


def call(name: str, **arguments: Any) -> dict[str, Any]:
    """A native tool call reply."""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
    }


def action(name: str, **arguments: Any) -> dict[str, Any]:
    """A ReAct tool call reply."""
    return text(json.dumps({"tool": name, "arguments": arguments}))


def answer(value: str) -> dict[str, Any]:
    """A ReAct final answer reply."""
    return text(json.dumps({"answer": value}))
