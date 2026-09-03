"""Tests for the Ollama client. Offline, httpx is monkeypatched."""

from __future__ import annotations

from typing import Any

import pytest

from chronoguard import ollama as ollama_module
from chronoguard.ollama import (
    ChatMessage,
    ChatResponse,
    ModelInfo,
    OllamaClient,
    OllamaTimeout,
    OllamaUnavailable,
    default_host,
    normalize_host,
)

TAGS = {
    "models": [
        {"name": "gemma3:4b", "size": 3338801804, "details": {"family": "gemma3", "parameter_size": "4.3B"}},
        {"name": "qwen3:8b", "size": 5200000000, "details": {"family": "qwen3", "parameter_size": "8.2B"}},
    ]
}


class FakeResponse:
    def __init__(self, payload: Any, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error

    def raise_for_status(self) -> None:
        if self.error:
            raise self.error

    def json(self) -> Any:
        return self.payload


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> OllamaClient:
    """A client wired to canned responses, with the requests recorded."""
    calls: list[tuple[str, dict[str, Any] | None]] = []
    shows = {
        "gemma3:4b": {"capabilities": ["completion", "vision"]},
        "qwen3:8b": {"capabilities": ["completion", "tools", "thinking"]},
    }

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        calls.append((url, None))
        return FakeResponse(TAGS)

    def fake_post(url: str, json: dict[str, Any], **kwargs: Any) -> FakeResponse:
        calls.append((url, json))
        if url.endswith("/api/show"):
            return FakeResponse(shows.get(json["model"], {}))
        return FakeResponse(
            {"model": json["model"], "message": {"role": "assistant", "content": "hi"}, "done": True}
        )

    monkeypatch.setattr(ollama_module.httpx, "get", fake_get)
    monkeypatch.setattr(ollama_module.httpx, "post", fake_post)
    instance = OllamaClient(host="localhost:11434")
    instance.calls = calls  # type: ignore[attr-defined]
    return instance


class TestHost:
    @pytest.mark.parametrize(
        "given,expected",
        [
            ("http://localhost:11434", "http://localhost:11434"),
            ("localhost:11434", "http://localhost:11434"),
            ("http://localhost:11434/", "http://localhost:11434"),
            ("  127.0.0.1:11434 ", "http://127.0.0.1:11434"),
            ("https://ollama.internal", "https://ollama.internal"),
        ],
    )
    def test_normalize(self, given: str, expected: str) -> None:
        assert normalize_host(given) == expected

    def test_env_var_is_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OLLAMA_HOST", "box.local:9999")
        assert default_host() == "http://box.local:9999"

    def test_default_when_env_is_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        assert default_host() == "http://localhost:11434"

    def test_empty_env_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OLLAMA_HOST", "")
        assert default_host() == "http://localhost:11434"


class TestModelDiscovery:
    def test_models_come_from_the_api_not_a_hardcoded_list(self, client: OllamaClient) -> None:
        assert [m.name for m in client.list_models()] == ["gemma3:4b", "qwen3:8b"]
        assert client.calls[0][0].endswith("/api/tags")

    def test_details_are_parsed(self, client: OllamaClient) -> None:
        model = client.list_models()[0]
        assert model.family == "gemma3"
        assert model.parameter_size == "4.3B"
        assert "4.3B" in str(model)

    def test_nameless_entries_are_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            ollama_module.httpx, "get", lambda *a, **k: FakeResponse({"models": [{"size": 1}]})
        )
        assert OllamaClient().list_models() == []

    def test_model_key_is_accepted_instead_of_name(self) -> None:
        assert ModelInfo.from_tag({"model": "llama3.2:1b"}).name == "llama3.2:1b"

    def test_empty_server(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ollama_module.httpx, "get", lambda *a, **k: FakeResponse({}))
        client = OllamaClient()
        assert client.list_models() == []
        with pytest.raises(OllamaUnavailable, match="No models installed"):
            client.pick_model()


class TestCapabilities:
    def test_tool_support_is_read_from_the_server(self, client: OllamaClient) -> None:
        assert client.supports_tools("qwen3:8b") is True
        assert client.supports_tools("gemma3:4b") is False

    def test_capabilities_are_cached(self, client: OllamaClient) -> None:
        client.capabilities("qwen3:8b")
        client.capabilities("qwen3:8b")
        shows = [c for c in client.calls if c[0].endswith("/api/show")]
        assert len(shows) == 1

    def test_older_servers_fall_back_to_sniffing_the_template(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            ollama_module.httpx,
            "post",
            lambda url, json, **k: FakeResponse({"template": "{{ if .ToolCalls }}...{{ end }}"}),
        )
        assert OllamaClient().supports_tools("old-model") is True

    def test_template_without_tools_reads_as_no_tools(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            ollama_module.httpx,
            "post",
            lambda url, json, **k: FakeResponse({"template": "{{ .Prompt }}"}),
        )
        assert OllamaClient().supports_tools("old-model") is False

    def test_an_unreachable_show_is_not_fatal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            ollama_module.httpx,
            "post",
            lambda *a, **k: FakeResponse(None, error=ollama_module.httpx.HTTPError("boom")),
        )
        assert OllamaClient().capabilities("whatever") == []


class TestPickModel:
    def test_prefers_a_tool_capable_model(self, client: OllamaClient) -> None:
        assert client.pick_model(prefer_tools=True) == "qwen3:8b"

    def test_falls_back_when_nothing_supports_tools(self, client: OllamaClient) -> None:
        client._capabilities = {"gemma3:4b": ["completion"], "qwen3:8b": ["completion"]}
        assert client.pick_model(prefer_tools=True) == "gemma3:4b"

    def test_is_deterministic(self, client: OllamaClient) -> None:
        assert client.pick_model() == client.pick_model() == "gemma3:4b"


class TestChat:
    def test_payload_shape(self, client: OllamaClient) -> None:
        client.chat("gemma3:4b", [ChatMessage(role="user", content="hi")], temperature=0.3)
        url, body = client.calls[-1]
        assert url.endswith("/api/chat")
        assert body["stream"] is False
        assert body["messages"] == [{"role": "user", "content": "hi"}]
        assert body["options"]["temperature"] == 0.3
        assert "tools" not in body

    def test_tools_are_forwarded_when_given(self, client: OllamaClient) -> None:
        client.chat("qwen3:8b", [{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
        assert client.calls[-1][1]["tools"] == [{"type": "function"}]

    def test_tool_messages_carry_their_name(self, client: OllamaClient) -> None:
        client.chat("x", [ChatMessage(role="tool", content="obs", tool_name="web_search")])
        assert client.calls[-1][1]["messages"][0]["tool_name"] == "web_search"

    def test_response_is_parsed(self, client: OllamaClient) -> None:
        response = client.chat("gemma3:4b", [{"role": "user", "content": "hi"}])
        assert response.content == "hi"
        assert response.tool_calls == []

    def test_tool_calls_are_surfaced(self) -> None:
        response = ChatResponse.model_validate(
            {
                "model": "m",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "web_search", "arguments": {"query": "x"}}}],
                },
            }
        )
        assert response.tool_calls[0]["function"]["name"] == "web_search"

    def test_unknown_response_fields_are_ignored(self) -> None:
        assert ChatResponse.model_validate({"model": "m", "total_duration": 1, "eval_count": 2}).done


class TestErrors:
    def test_http_errors_become_ollama_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            ollama_module.httpx,
            "get",
            lambda *a, **k: FakeResponse(None, error=ollama_module.httpx.ConnectError("refused")),
        )
        with pytest.raises(OllamaUnavailable, match="refused"):
            OllamaClient().list_models()

    def test_is_available_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*a: Any, **k: Any) -> None:
            raise ollama_module.httpx.ConnectError("refused")

        monkeypatch.setattr(ollama_module.httpx, "get", boom)
        assert OllamaClient().is_available() is False

    def test_repr_names_the_host(self) -> None:
        assert "11434" in repr(OllamaClient(host="localhost:11434"))


class TestTimeouts:
    """A slow model is not an absent server, and the advice differs."""

    def test_a_read_timeout_is_its_own_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            ollama_module.httpx,
            "post",
            lambda *a, **k: FakeResponse(None, error=ollama_module.httpx.ReadTimeout("timed out")),
        )
        with pytest.raises(OllamaTimeout):
            OllamaClient(host="localhost:11434").chat("m", [{"role": "user", "content": "hi"}])

    def test_a_timeout_still_counts_as_unavailable_for_old_handlers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            ollama_module.httpx,
            "post",
            lambda *a, **k: FakeResponse(None, error=ollama_module.httpx.ReadTimeout("timed out")),
        )
        with pytest.raises(OllamaUnavailable):
            OllamaClient().chat("m", [{"role": "user", "content": "hi"}])

    def test_the_message_names_the_model_and_says_what_to_do(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            ollama_module.httpx,
            "post",
            lambda *a, **k: FakeResponse(None, error=ollama_module.httpx.ReadTimeout("timed out")),
        )
        with pytest.raises(OllamaTimeout, match="qwen3:4b"):
            OllamaClient(timeout=5).chat("qwen3:4b", [{"role": "user", "content": "hi"}])

    def test_the_message_does_not_suggest_starting_the_server(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            ollama_module.httpx,
            "post",
            lambda *a, **k: FakeResponse(None, error=ollama_module.httpx.ReadTimeout("timed out")),
        )
        try:
            OllamaClient().chat("m", [{"role": "user", "content": "hi"}])
        except OllamaTimeout as exc:
            assert "ollama serve" not in str(exc)
            assert "slow" in str(exc)

    def test_a_get_timeout_is_also_caught(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            ollama_module.httpx,
            "get",
            lambda *a, **k: FakeResponse(None, error=ollama_module.httpx.ReadTimeout("timed out")),
        )
        with pytest.raises(OllamaTimeout):
            OllamaClient().list_models()

    def test_a_connection_error_is_not_a_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            ollama_module.httpx,
            "get",
            lambda *a, **k: FakeResponse(None, error=ollama_module.httpx.ConnectError("refused")),
        )
        with pytest.raises(OllamaUnavailable) as caught:
            OllamaClient().list_models()
        assert not isinstance(caught.value, OllamaTimeout)

    def test_the_default_timeout_allows_for_reasoning_models(self) -> None:
        assert OllamaClient().timeout >= 300
