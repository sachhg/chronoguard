"""Chat backends.

ChronoGuard needs four things from a model server: list what's installed, say
whether a model can call tools natively, pick one, and hold a chat. That's the
whole surface the probe, the claim classifier and the agent loop use, which is
why being tied to a single server was never a design decision so much as an
accident of writing the Ollama client first.

`ChatBackend` is that surface as a Protocol. `OllamaClient` satisfies it and
stays the default everywhere. `OpenAICompatClient` covers anything speaking the
OpenAI chat-completions API, which is vLLM, LM Studio, llama.cpp's server,
text-generation-webui, OpenRouter and the hosted APIs, with no provider-specific
code.

Local-first still holds. Most of that list is a local server, and the hosted
option exists because sometimes the model you need to test isn't one you can run.

This module owns the wire types (`ChatMessage`, `ChatResponse`, `ModelInfo`) and
the exceptions so that `ollama.py` and any other backend can share them without
importing each other. `ollama.py` re-exports them, so existing imports keep
working.

Deliberately not here: streaming, embeddings, retries, provider SDKs. A backend
is a thin translation layer and nothing else.
"""

from __future__ import annotations

import os
from typing import Any, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BackendTimeout",
    "BackendUnavailable",
    "ChatBackend",
    "ChatMessage",
    "ChatResponse",
    "ModelInfo",
    "OpenAICompatClient",
    "normalize_host",
]


class BackendUnavailable(RuntimeError):
    """The server isn't reachable, or a request to it failed."""


class BackendTimeout(BackendUnavailable):
    """The server is reachable but answered too slowly.

    Separate from `BackendUnavailable` because the advice differs. "Start the
    server" is wrong when it's up and a reasoning model is simply taking a long
    time.
    """


def normalize_host(host: str) -> str:
    """`localhost:11434` and `http://localhost:11434/` both work."""
    host = host.strip().rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    return host


class ModelInfo(BaseModel):
    """One model the server is offering."""

    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    name: str
    family: str | None = None
    parameter_size: str | None = None
    size_bytes: int | None = None

    @classmethod
    def from_tag(cls, payload: dict[str, Any]) -> ModelInfo:
        """Read Ollama's `/api/tags` shape."""
        details = payload.get("details") or {}
        return cls(
            name=payload.get("name") or payload.get("model") or "",
            family=details.get("family"),
            parameter_size=details.get("parameter_size"),
            size_bytes=payload.get("size"),
        )

    @classmethod
    def from_openai(cls, payload: dict[str, Any]) -> ModelInfo:
        """Read the OpenAI `/models` shape, which carries an id and little else."""
        return cls(name=str(payload.get("id") or ""), family=payload.get("owned_by"))

    def __str__(self) -> str:
        bits = [self.name]
        if self.parameter_size:
            bits.append(f"({self.parameter_size})")
        return " ".join(bits)


class ChatMessage(BaseModel):
    """One turn in a chat.

    `tool_name` and `tool_call_id` both identify which call a tool result answers.
    Ollama wants the name, the OpenAI API wants the id, and the agent loop sets
    whichever the model gave it, so each backend reads the field it needs.
    """

    model_config = ConfigDict(extra="allow")

    role: str
    content: str = ""
    tool_calls: list[dict[str, Any]] | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """Ollama's message shape."""
        out: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            out["tool_calls"] = self.tool_calls
        if self.tool_name:
            out["tool_name"] = self.tool_name
        return out

    def to_openai_payload(self) -> dict[str, Any]:
        """The OpenAI message shape.

        A tool result needs `tool_call_id` and nothing else identifies it, so a
        message with no id falls back to the name. Some servers reject the
        fallback, but dropping the field outright gets the turn rejected
        everywhere.
        """
        out: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            out["tool_calls"] = self.tool_calls
        if self.role == "tool":
            out["tool_call_id"] = self.tool_call_id or self.tool_name or ""
        return out


class ChatResponse(BaseModel):
    """One assistant turn coming back."""

    model_config = ConfigDict(extra="ignore")

    model: str = ""
    message: ChatMessage = Field(default_factory=lambda: ChatMessage(role="assistant"))
    done: bool = True

    @property
    def content(self) -> str:
        return self.message.content or ""

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        return self.message.tool_calls or []

    @classmethod
    def from_openai(cls, payload: dict[str, Any]) -> ChatResponse:
        """Flatten a chat-completions response into the same shape Ollama gives.

        Tool arguments arrive as a JSON string here rather than an object. The
        agent loop already parses both, so they're passed through untouched.
        """
        choices = payload.get("choices") or []
        raw = (choices[0].get("message") or {}) if choices else {}
        return cls(
            model=str(payload.get("model") or ""),
            message=ChatMessage(
                role=str(raw.get("role") or "assistant"),
                content=str(raw.get("content") or ""),
                tool_calls=raw.get("tool_calls") or None,
            ),
            done=bool(choices and choices[0].get("finish_reason")),
        )


@runtime_checkable
class ChatBackend(Protocol):
    """What ChronoGuard needs from a model server.

    Anything implementing this can be handed to `run_agent`, `LeakageProbe`,
    `ClaimClassifier` or `run_scenario` in place of `OllamaClient`.
    """

    host: str

    def is_available(self) -> bool:
        """Cheap reachability check. Must never raise, so tests can skip on it."""
        ...

    def list_models(self) -> list[ModelInfo]: ...

    def model_names(self) -> list[str]: ...

    def supports_tools(self, model: str) -> bool: ...

    def pick_model(self, *, prefer_tools: bool = False) -> str: ...

    def chat(
        self,
        model: str,
        messages: list[ChatMessage] | list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        options: dict[str, Any] | None = None,
    ) -> ChatResponse: ...


class OpenAICompatClient:
    """Talks to any server speaking the OpenAI chat-completions API.

    Args:
        base_url: Root of the API, with or without a trailing `/v1`. Defaults to
            `OPENAI_BASE_URL`, then a local vLLM-style port.
        api_key: Bearer token. Defaults to `OPENAI_API_KEY`. Local servers
            usually need none, and no header is sent when there's no key.
        timeout: Seconds per request. Generous, same reasoning as the Ollama
            client: cold starts are slow and reasoning models are slower.
        tool_models: Which models to report as tool-capable. `True` (the
            default) for all of them, `False` for none, or an explicit set of
            names. There's no capability endpoint in this API, so this is a
            declaration rather than something we can discover.
    """

    DEFAULT_BASE_URL = "http://localhost:8000/v1"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        api_key: str | None = None,
        timeout: float = 600.0,
        tool_models: bool | set[str] | frozenset[str] = True,
    ) -> None:
        root = base_url or os.environ.get("OPENAI_BASE_URL") or self.DEFAULT_BASE_URL
        self.base_url = _with_version(normalize_host(root))
        self.api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY")
        self.timeout = timeout
        self.tool_models = tool_models

    @property
    def host(self) -> str:
        """Where this backend is pointed, for error messages and reports."""
        return self.base_url

    def __repr__(self) -> str:
        return f"OpenAICompatClient(base_url={self.base_url!r})"

    def is_available(self) -> bool:
        try:
            httpx.get(
                f"{self.base_url}/models", headers=self._headers(), timeout=2.0
            ).raise_for_status()
        except Exception:
            return False
        return True

    def list_models(self) -> list[ModelInfo]:
        payload = self._get("/models")
        models = [ModelInfo.from_openai(m) for m in payload.get("data") or []]
        return [m for m in models if m.name]

    def model_names(self) -> list[str]:
        return [m.name for m in self.list_models()]

    def supports_tools(self, model: str) -> bool:
        """Whether to give this model real tool definitions.

        This API has no capability endpoint, so there is nothing to ask. The
        default assumes yes, which is right for essentially every current
        server; narrow it with `tool_models` when it isn't.
        """
        if isinstance(self.tool_models, bool):
            return self.tool_models
        return model in self.tool_models

    def pick_model(self, *, prefer_tools: bool = False) -> str:
        """Deterministic so a run is reproducible: alphabetical inside each group."""
        names = sorted(self.model_names())
        if not names:
            raise BackendUnavailable(f"No models offered by {self.base_url}.")
        if prefer_tools:
            for name in names:
                if self.supports_tools(name):
                    return name
        return names[0]

    def chat(
        self,
        model: str,
        messages: list[ChatMessage] | list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        options: dict[str, Any] | None = None,
    ) -> ChatResponse:
        """One non-streaming `/chat/completions` round trip."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                m.to_openai_payload() if isinstance(m, ChatMessage) else dict(m)
                for m in messages
            ],
            "stream": False,
            "temperature": temperature,
            **(options or {}),
        }
        if tools:
            payload["tools"] = tools
        return ChatResponse.from_openai(self._post("/chat/completions", payload))

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _get(self, path: str) -> dict[str, Any]:
        try:
            response = httpx.get(
                f"{self.base_url}{path}", headers=self._headers(), timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as exc:
            raise self._timeout(path) from exc
        except httpx.HTTPError as exc:
            raise BackendUnavailable(f"GET {path} on {self.base_url} failed: {exc}") from exc

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = httpx.post(
                f"{self.base_url}{path}",
                json=body,
                headers=self._headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as exc:
            raise self._timeout(path, body.get("model")) from exc
        except httpx.HTTPError as exc:
            raise BackendUnavailable(f"POST {path} on {self.base_url} failed: {exc}") from exc

    def _timeout(self, path: str, model: str | None = None) -> BackendTimeout:
        who = f"{model} " if model else ""
        return BackendTimeout(
            f"{who}on {self.base_url} did not answer {path} within {self.timeout:g}s. "
            "The server is up, the model is just slow. Raise the timeout, cap the work "
            "with --max-future / --max-control / --max-claims, or pick a smaller model."
        )


def _with_version(root: str) -> str:
    """Accept a base URL with or without the `/v1` suffix.

    People copy these from vendor docs either way, and a 404 on /models is a
    miserable way to find out which one this expected.
    """
    return root if root.rstrip("/").endswith("/v1") else f"{root}/v1"
