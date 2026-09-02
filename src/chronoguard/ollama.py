"""Thin client for a local Ollama server.

Just enough to list what's installed, ask whether a model can do native tool
calling, and hold a chat. No streaming, no embeddings, no pulls.

Models are discovered at runtime through `/api/tags`. Nothing here hardcodes a
model name, because which models you have installed is your business and any
list baked in here would be wrong by next month.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ChatMessage",
    "ChatResponse",
    "ModelInfo",
    "OllamaClient",
    "OllamaUnavailable",
    "default_host",
]

DEFAULT_HOST = "http://localhost:11434"


class OllamaUnavailable(RuntimeError):
    """The server isn't reachable, or a request to it failed."""


def default_host() -> str:
    """Where to look for Ollama. Honours OLLAMA_HOST, scheme optional."""
    return normalize_host(os.environ.get("OLLAMA_HOST") or DEFAULT_HOST)


def normalize_host(host: str) -> str:
    """`localhost:11434` and `http://localhost:11434/` both work."""
    host = host.strip().rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    return host


class ModelInfo(BaseModel):
    """One installed model, as reported by `/api/tags`."""

    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    name: str
    family: str | None = None
    parameter_size: str | None = None
    size_bytes: int | None = None

    @classmethod
    def from_tag(cls, payload: dict[str, Any]) -> ModelInfo:
        details = payload.get("details") or {}
        return cls(
            name=payload.get("name") or payload.get("model") or "",
            family=details.get("family"),
            parameter_size=details.get("parameter_size"),
            size_bytes=payload.get("size"),
        )

    def __str__(self) -> str:
        bits = [self.name]
        if self.parameter_size:
            bits.append(f"({self.parameter_size})")
        return " ".join(bits)


class ChatMessage(BaseModel):
    """One turn in a chat."""

    model_config = ConfigDict(extra="allow")

    role: str
    content: str = ""
    tool_calls: list[dict[str, Any]] | None = None
    tool_name: str | None = None

    def to_payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            out["tool_calls"] = self.tool_calls
        if self.tool_name:
            out["tool_name"] = self.tool_name
        return out


class ChatResponse(BaseModel):
    """What `/api/chat` gave back."""

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


class OllamaClient:
    """Talks to a local Ollama server over HTTP.

    Args:
        host: Base URL. Defaults to OLLAMA_HOST, then localhost:11434.
        timeout: Seconds per request. Small models on cold start are slow, so
            this is generous by default.
    """

    def __init__(self, host: str | None = None, timeout: float = 180.0) -> None:
        self.host = normalize_host(host) if host else default_host()
        self.timeout = timeout
        self._capabilities: dict[str, list[str]] = {}

    def __repr__(self) -> str:
        return f"OllamaClient(host={self.host!r})"

    def is_available(self) -> bool:
        """Cheap reachability check. Never raises, so tests can skip on it."""
        try:
            httpx.get(f"{self.host}/api/tags", timeout=2.0).raise_for_status()
        except Exception:
            return False
        return True

    def list_models(self) -> list[ModelInfo]:
        """Everything installed locally, discovered at runtime."""
        payload = self._get("/api/tags")
        models = [ModelInfo.from_tag(m) for m in payload.get("models") or []]
        return [m for m in models if m.name]

    def model_names(self) -> list[str]:
        return [m.name for m in self.list_models()]

    def show(self, model: str) -> dict[str, Any]:
        """Model metadata from `/api/show`."""
        return self._post("/api/show", {"model": model})

    def capabilities(self, model: str) -> list[str]:
        """What the model can do, cached per client.

        Ollama reports this directly on newer servers. On older ones we fall
        back to sniffing the chat template for tool support.
        """
        if model in self._capabilities:
            return self._capabilities[model]
        try:
            info = self.show(model)
        except OllamaUnavailable:
            self._capabilities[model] = []
            return []
        caps = [str(c) for c in (info.get("capabilities") or [])]
        if not caps:
            template = str(info.get("template") or "")
            caps = ["completion"] + (["tools"] if ".ToolCalls" in template or ".Tools" in template else [])
        self._capabilities[model] = caps
        return caps

    def supports_tools(self, model: str) -> bool:
        """Whether this model can do native tool calling."""
        return "tools" in self.capabilities(model)

    def pick_model(self, *, prefer_tools: bool = False) -> str:
        """Pick an installed model. Prefers a tool-capable one when asked.

        Deterministic so a run is reproducible: alphabetical inside each group.
        """
        names = sorted(self.model_names())
        if not names:
            raise OllamaUnavailable(
                f"No models installed on {self.host}. Try `ollama pull <model>`."
            )
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
        """One non-streaming `/api/chat` round trip."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.to_payload() if isinstance(m, ChatMessage) else m for m in messages],
            "stream": False,
            "options": {"temperature": temperature, **(options or {})},
        }
        if tools:
            payload["tools"] = tools
        return ChatResponse.model_validate(self._post("/api/chat", payload))

    def _get(self, path: str) -> dict[str, Any]:
        try:
            response = httpx.get(f"{self.host}{path}", timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(f"GET {path} on {self.host} failed: {exc}") from exc

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = httpx.post(f"{self.host}{path}", json=body, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(f"POST {path} on {self.host} failed: {exc}") from exc
