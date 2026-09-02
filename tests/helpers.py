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


class CannedProbeClient:
    """Stands in for OllamaClient in probe runs.

    Answers are keyed by a substring of the question. Anything unmatched gets
    `default`. Judge prompts are recognised by their instruction line and
    answered from `judge_replies`.
    """

    def __init__(
        self,
        answers: dict[str, str] | None = None,
        *,
        default: str = "I DO NOT KNOW",
        judge_reply: str = "NO",
    ) -> None:
        self.answers = answers or {}
        self.default = default
        self.judge_reply = judge_reply
        self.asked: list[str] = []
        self.judged: list[str] = []

    def chat(self, model: str, messages: Any, *, tools: Any = None, **kwargs: Any) -> ChatResponse:
        content = messages[-1]["content"] if isinstance(messages[-1], dict) else messages[-1].content
        if "Reply with exactly one word" in content:
            self.judged.append(content)
            return ChatResponse.model_validate({"message": {"role": "assistant", "content": self.judge_reply}})
        self.asked.append(content)
        for needle, reply in self.answers.items():
            if needle.lower() in content.lower():
                return ChatResponse.model_validate({"message": {"role": "assistant", "content": reply}})
        return ChatResponse.model_validate({"message": {"role": "assistant", "content": self.default}})


class ScriptedJudgeClient:
    """Stands in for the judge model in claim classification.

    Decomposition returns `claims` joined by newlines. Classification returns
    the first verdict whose key appears in the claim being judged, else
    `default_verdict`.
    """

    def __init__(
        self,
        claims: list[str] | None = None,
        verdicts: dict[str, str] | None = None,
        *,
        default_verdict: str = "UNSUPPORTED | - | not in the documents",
        decompose_reply: str | None = None,
    ) -> None:
        self.claims = claims or []
        self.verdicts = verdicts or {}
        self.default_verdict = default_verdict
        self.decompose_reply = decompose_reply
        self.prompts: list[str] = []

    def pick_model(self, *, prefer_tools: bool = False) -> str:
        return "scripted-judge"

    @property
    def classify_prompts(self) -> list[str]:
        return [p for p in self.prompts if "Work through these in order" in p]

    def chat(self, model: str, messages: Any, *, tools: Any = None, **kwargs: Any) -> ChatResponse:
        content = messages[-1]["content"] if isinstance(messages[-1], dict) else messages[-1].content
        self.prompts.append(content)

        if "Break the following answer into its atomic claims" in content:
            reply = self.decompose_reply if self.decompose_reply is not None else "\n".join(self.claims)
            return ChatResponse.model_validate({"message": {"role": "assistant", "content": reply}})

        # Match against the claim under judgement only. The prompt also carries
        # the evidence and few-shot examples, so matching the whole thing would
        # fire on the examples' wording.
        claim = self._claim_in(content)
        for needle, verdict in self.verdicts.items():
            if needle.lower() in claim.lower():
                return ChatResponse.model_validate({"message": {"role": "assistant", "content": verdict}})
        return ChatResponse.model_validate(
            {"message": {"role": "assistant", "content": self.default_verdict}}
        )

    @staticmethod
    def _claim_in(prompt: str) -> str:
        for line in prompt.splitlines():
            if line.startswith("Claim: "):
                return line[len("Claim: ") :]
        return prompt
