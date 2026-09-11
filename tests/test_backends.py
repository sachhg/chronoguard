"""Tests for the backend protocol and the OpenAI-compatible client.

Offline, httpx is monkeypatched. The point of every test here is that a
different backend is indistinguishable to the rest of ChronoGuard, so most of
these assert the OpenAI client produces the same shapes the Ollama one does.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from chronoguard import backends as backends_module
from chronoguard.backends import (
    BackendTimeout,
    BackendUnavailable,
    ChatBackend,
    ChatMessage,
    ChatResponse,
    ModelInfo,
    OpenAICompatClient,
    normalize_host,
)
from chronoguard.ollama import OllamaClient, OllamaTimeout, OllamaUnavailable

MODELS = {
    "object": "list",
    "data": [
        {"id": "meta-llama/Llama-3.1-8B-Instruct", "object": "model", "owned_by": "meta"},
        {"id": "Qwen/Qwen2.5-7B-Instruct", "object": "model", "owned_by": "qwen"},
    ],
}

COMPLETION = {
    "id": "chatcmpl-1",
    "model": "Qwen/Qwen2.5-7B-Instruct",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Summer, no price yet."},
            "finish_reason": "stop",
        }
    ],
}

TOOL_COMPLETION = {
    "model": "m",
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc",
                        "type": "function",
                        "function": {
                            "name": "web_search",
                            "arguments": '{"query": "meridian pricing"}',
                        },
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }
    ],
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
def calls() -> list[dict[str, Any]]:
    return []


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, calls: list[dict[str, Any]]) -> OpenAICompatClient:
    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        calls.append({"method": "GET", "url": url, **kwargs})
        return FakeResponse(MODELS)

    def fake_post(url: str, json: dict[str, Any], **kwargs: Any) -> FakeResponse:
        calls.append({"method": "POST", "url": url, "json": json, **kwargs})
        return FakeResponse(TOOL_COMPLETION if json.get("tools") else COMPLETION)

    monkeypatch.setattr(backends_module.httpx, "get", fake_get)
    monkeypatch.setattr(backends_module.httpx, "post", fake_post)
    return OpenAICompatClient("http://localhost:8000/v1")


class TestBaseUrl:
    def test_the_v1_suffix_is_added_when_missing(self) -> None:
        assert OpenAICompatClient("http://localhost:8000").base_url == "http://localhost:8000/v1"

    def test_an_existing_v1_suffix_is_left_alone(self) -> None:
        url = "https://openrouter.ai/api/v1"
        assert OpenAICompatClient(url).base_url == url

    def test_a_trailing_slash_is_trimmed(self) -> None:
        assert OpenAICompatClient("http://localhost:8000/v1/").base_url == "http://localhost:8000/v1"

    def test_a_bare_host_gets_a_scheme(self) -> None:
        assert OpenAICompatClient("localhost:8000").base_url.startswith("http://")

    def test_it_reads_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_BASE_URL", "http://vllm.internal:8000")
        assert OpenAICompatClient().base_url == "http://vllm.internal:8000/v1"

    def test_an_explicit_url_beats_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_BASE_URL", "http://wrong:8000")
        assert "right" in OpenAICompatClient("http://right:8000").base_url

    def test_host_is_the_base_url(self) -> None:
        # Reports and error messages read `host` off whatever backend they got.
        client = OpenAICompatClient("http://localhost:8000")
        assert client.host == client.base_url

    def test_repr_names_the_url(self) -> None:
        assert "localhost:8000" in repr(OpenAICompatClient("http://localhost:8000"))


class TestAuth:
    def test_no_key_means_no_header(self, monkeypatch: pytest.MonkeyPatch, client, calls) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        client.api_key = None
        client.list_models()
        assert calls[0]["headers"] == {}

    def test_a_key_becomes_a_bearer_header(self, client, calls) -> None:
        client.api_key = "sk-test"
        client.list_models()
        assert calls[0]["headers"] == {"Authorization": "Bearer sk-test"}

    def test_it_reads_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        assert OpenAICompatClient("http://x").api_key == "sk-env"

    def test_an_explicit_key_beats_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        assert OpenAICompatClient("http://x", api_key="sk-arg").api_key == "sk-arg"


class TestListingModels:
    def test_it_reads_the_data_array(self, client) -> None:
        assert client.model_names() == [
            "meta-llama/Llama-3.1-8B-Instruct",
            "Qwen/Qwen2.5-7B-Instruct",
        ]

    def test_it_hits_the_models_endpoint(self, client, calls) -> None:
        client.list_models()
        assert calls[0]["url"] == "http://localhost:8000/v1/models"

    def test_it_returns_model_info(self, client) -> None:
        assert all(isinstance(m, ModelInfo) for m in client.list_models())

    def test_owned_by_becomes_the_family(self, client) -> None:
        assert client.list_models()[0].family == "meta"

    def test_nameless_entries_are_dropped(self, monkeypatch, client) -> None:
        monkeypatch.setattr(
            backends_module.httpx, "get", lambda url, **kw: FakeResponse({"data": [{}, {"id": "m"}]})
        )
        assert client.model_names() == ["m"]


class TestPickModel:
    def test_it_is_alphabetical_and_deterministic(self, client) -> None:
        assert client.pick_model() == "Qwen/Qwen2.5-7B-Instruct"
        assert client.pick_model() == client.pick_model()

    def test_no_models_is_a_backend_error(self, monkeypatch, client) -> None:
        monkeypatch.setattr(backends_module.httpx, "get", lambda url, **kw: FakeResponse({"data": []}))
        with pytest.raises(BackendUnavailable, match="No models"):
            client.pick_model()

    def test_prefer_tools_finds_a_declared_one(self, client) -> None:
        client.tool_models = {"meta-llama/Llama-3.1-8B-Instruct"}
        assert client.pick_model(prefer_tools=True) == "meta-llama/Llama-3.1-8B-Instruct"

    def test_prefer_tools_falls_back_when_none_qualify(self, client) -> None:
        client.tool_models = False
        assert client.pick_model(prefer_tools=True) == "Qwen/Qwen2.5-7B-Instruct"


class TestToolSupport:
    def test_it_defaults_to_yes(self, client) -> None:
        # There is no capability endpoint in this API, and essentially every
        # current server supports tools, so the default assumes it.
        assert client.supports_tools("anything") is True

    def test_it_can_be_turned_off_wholesale(self, client) -> None:
        client.tool_models = False
        assert client.supports_tools("anything") is False

    def test_an_explicit_set_is_honoured(self, client) -> None:
        client.tool_models = {"good"}
        assert client.supports_tools("good") is True
        assert client.supports_tools("bad") is False


class TestChat:
    def test_it_returns_a_chat_response(self, client) -> None:
        assert isinstance(client.chat("m", [ChatMessage(role="user", content="hi")]), ChatResponse)

    def test_the_content_is_flattened_out_of_choices(self, client) -> None:
        assert client.chat("m", [ChatMessage(role="user", content="hi")]).content == (
            "Summer, no price yet."
        )

    def test_it_hits_chat_completions(self, client, calls) -> None:
        client.chat("m", [ChatMessage(role="user", content="hi")])
        assert calls[0]["url"] == "http://localhost:8000/v1/chat/completions"

    def test_streaming_is_always_off(self, client, calls) -> None:
        client.chat("m", [ChatMessage(role="user", content="hi")])
        assert calls[0]["json"]["stream"] is False

    def test_temperature_is_top_level_not_nested_in_options(self, client, calls) -> None:
        # Ollama nests it under "options", this API does not.
        client.chat("m", [ChatMessage(role="user", content="hi")], temperature=0.7)
        assert calls[0]["json"]["temperature"] == 0.7
        assert "options" not in calls[0]["json"]

    def test_extra_options_are_merged_in(self, client, calls) -> None:
        client.chat("m", [ChatMessage(role="user", content="hi")], options={"top_p": 0.9})
        assert calls[0]["json"]["top_p"] == 0.9

    def test_plain_dict_messages_pass_through(self, client, calls) -> None:
        client.chat("m", [{"role": "user", "content": "hi"}])
        assert calls[0]["json"]["messages"] == [{"role": "user", "content": "hi"}]

    def test_tool_definitions_are_forwarded(self, client, calls) -> None:
        schema = [{"type": "function", "function": {"name": "web_search"}}]
        client.chat("m", [ChatMessage(role="user", content="hi")], tools=schema)
        assert calls[0]["json"]["tools"] == schema

    def test_tool_calls_come_back_in_the_ollama_shape(self, client) -> None:
        schema = [{"type": "function", "function": {"name": "web_search"}}]
        response = client.chat("m", [ChatMessage(role="user", content="hi")], tools=schema)
        assert response.tool_calls[0]["function"]["name"] == "web_search"

    def test_string_encoded_arguments_are_left_for_the_agent_to_parse(self, client) -> None:
        # The agent loop already handles both a dict and a JSON string, so the
        # backend passes them through rather than guessing.
        schema = [{"type": "function", "function": {"name": "web_search"}}]
        response = client.chat("m", [ChatMessage(role="user", content="hi")], tools=schema)
        assert response.tool_calls[0]["function"]["arguments"] == '{"query": "meridian pricing"}'

    def test_a_null_content_becomes_an_empty_string(self, client) -> None:
        schema = [{"type": "function", "function": {"name": "web_search"}}]
        assert client.chat("m", [ChatMessage(role="user")], tools=schema).content == ""


class TestMessageTranslation:
    def test_a_tool_result_carries_its_call_id(self) -> None:
        message = ChatMessage(role="tool", content="obs", tool_name="web_search", tool_call_id="call_1")
        assert message.to_openai_payload()["tool_call_id"] == "call_1"

    def test_it_falls_back_to_the_tool_name(self) -> None:
        # Better than omitting the field, which every server rejects.
        message = ChatMessage(role="tool", content="obs", tool_name="web_search")
        assert message.to_openai_payload()["tool_call_id"] == "web_search"

    def test_a_non_tool_message_has_no_call_id(self) -> None:
        assert "tool_call_id" not in ChatMessage(role="user", content="hi").to_openai_payload()

    def test_the_ollama_payload_uses_the_name_not_the_id(self) -> None:
        message = ChatMessage(role="tool", content="obs", tool_name="web_search", tool_call_id="call_1")
        payload = message.to_payload()
        assert payload["tool_name"] == "web_search"
        assert "tool_call_id" not in payload

    def test_assistant_tool_calls_survive_both_translations(self) -> None:
        calls = [{"id": "c1", "function": {"name": "f", "arguments": "{}"}}]
        message = ChatMessage(role="assistant", tool_calls=calls)
        assert message.to_openai_payload()["tool_calls"] == calls
        assert message.to_payload()["tool_calls"] == calls


class TestErrors:
    def test_a_refused_connection_is_a_backend_error(self, monkeypatch, client) -> None:
        def boom(url: str, **kwargs: Any) -> FakeResponse:
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(backends_module.httpx, "get", boom)
        with pytest.raises(BackendUnavailable, match="failed"):
            client.list_models()

    def test_the_message_names_the_url(self, monkeypatch, client) -> None:
        monkeypatch.setattr(
            backends_module.httpx, "get", lambda url, **kw: (_ for _ in ()).throw(httpx.ConnectError("x"))
        )
        with pytest.raises(BackendUnavailable, match="localhost:8000"):
            client.list_models()

    def test_a_slow_server_is_a_timeout_not_an_outage(self, monkeypatch, client) -> None:
        def slow(url: str, json: dict[str, Any], **kwargs: Any) -> FakeResponse:
            raise httpx.ReadTimeout("too slow")

        monkeypatch.setattr(backends_module.httpx, "post", slow)
        with pytest.raises(BackendTimeout) as caught:
            client.chat("m", [ChatMessage(role="user", content="hi")])
        assert "the model is just slow" in str(caught.value)

    def test_a_timeout_is_still_a_backend_error(self) -> None:
        assert issubclass(BackendTimeout, BackendUnavailable)

    def test_is_available_never_raises(self, monkeypatch, client) -> None:
        monkeypatch.setattr(
            backends_module.httpx, "get", lambda url, **kw: (_ for _ in ()).throw(httpx.ConnectError("x"))
        )
        assert client.is_available() is False

    def test_is_available_is_true_when_models_answers(self, client) -> None:
        assert client.is_available() is True


class TestProtocolConformance:
    """Both clients have to be interchangeable, or the abstraction is a lie."""

    def test_the_openai_client_satisfies_the_protocol(self) -> None:
        assert isinstance(OpenAICompatClient("http://x"), ChatBackend)

    def test_the_ollama_client_satisfies_the_protocol(self) -> None:
        assert isinstance(OllamaClient(), ChatBackend)

    @pytest.mark.parametrize(
        "method", ["is_available", "list_models", "model_names", "supports_tools", "pick_model", "chat"]
    )
    def test_both_clients_expose_the_same_methods(self, method: str) -> None:
        assert callable(getattr(OllamaClient(), method))
        assert callable(getattr(OpenAICompatClient("http://x"), method))

    def test_both_clients_expose_a_host(self) -> None:
        assert isinstance(OllamaClient().host, str)
        assert isinstance(OpenAICompatClient("http://x").host, str)


class TestExceptionHierarchy:
    """Callers catch BackendUnavailable, so the Ollama errors have to be ones."""

    def test_ollama_unavailable_is_a_backend_error(self) -> None:
        assert issubclass(OllamaUnavailable, BackendUnavailable)

    def test_ollama_timeout_is_both(self) -> None:
        assert issubclass(OllamaTimeout, OllamaUnavailable)
        assert issubclass(OllamaTimeout, BackendTimeout)

    def test_catching_the_generic_one_catches_the_specific(self) -> None:
        with pytest.raises(BackendUnavailable):
            raise OllamaTimeout("slow")

    def test_a_timeout_is_distinguishable_from_an_outage(self) -> None:
        # The advice differs: "start the server" is wrong for a slow model.
        assert not isinstance(OllamaUnavailable("down"), BackendTimeout)


class TestNormalizeHost:
    def test_it_adds_a_scheme(self) -> None:
        assert normalize_host("localhost:1234") == "http://localhost:1234"

    def test_it_keeps_https(self) -> None:
        assert normalize_host("https://api.example.com") == "https://api.example.com"

    def test_it_trims_a_trailing_slash(self) -> None:
        assert normalize_host("http://x/") == "http://x"


class TestEndToEndThroughTheAgentLoop:
    """The claim this phase makes: swapping the backend changes nothing else.

    Same guard, same tools, same filtering, same audit log. If this passes, the
    OpenAI-compatible path is genuinely wired through rather than merely
    importable.
    """

    @pytest.fixture
    def server(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
        """A fake OpenAI server that searches once, then answers."""
        sent: list[dict[str, Any]] = []
        replies = [
            {
                "model": "m",
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "web_search",
                                "arguments": '{"query": "meridian price ship date"}',
                            },
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
            },
            {
                "model": "m",
                "choices": [{
                    "message": {"role": "assistant", "content": "Summer, price not announced."},
                    "finish_reason": "stop",
                }],
            },
        ]

        def fake_post(url: str, json: dict[str, Any], **kwargs: Any) -> FakeResponse:
            sent.append(json)
            return FakeResponse(replies[min(len(sent) - 1, len(replies) - 1)])

        monkeypatch.setattr(backends_module.httpx, "get", lambda url, **kw: FakeResponse(MODELS))
        monkeypatch.setattr(backends_module.httpx, "post", fake_post)
        return sent

    def run(self, server):
        from chronoguard.agent import AgentConfig, run_agent
        from chronoguard.fixtures import FIXTURE_AS_OF, build_fixture_toolset
        from chronoguard.guard import TemporalGuard
        from chronoguard.interception import AuditLog

        guard = TemporalGuard(FIXTURE_AS_OF)
        audit = AuditLog()
        tools = build_fixture_toolset(guard, audit)
        result = run_agent(
            AgentConfig(task="when does meridian ship?", as_of=guard.as_of, model="m"),
            tools,
            client=OpenAICompatClient("http://localhost:8000"),
        )
        return result, audit

    def test_the_loop_completes(self, server) -> None:
        result, _ = self.run(server)
        assert result.final_answer == "Summer, price not announced."
        assert result.stopped_because == "answered"

    def test_it_picks_native_mode(self, server) -> None:
        # supports_tools defaults to True on this backend, so tool definitions
        # go out rather than the react text protocol.
        result, _ = self.run(server)
        assert result.mode == "native"
        assert server[0]["tools"]

    def test_the_tool_actually_ran(self, server) -> None:
        result, audit = self.run(server)
        assert len(result.tool_calls) == 1
        assert audit.call_count == 1

    def test_the_guard_still_filtered(self, server) -> None:
        _, audit = self.run(server)
        assert audit.filtered_count > 0, "the guard did nothing, this proves nothing"

    def test_no_post_as_of_content_reached_the_agent(self, server) -> None:
        from chronoguard.fixtures import POST_AS_OF_CANARIES

        result, _ = self.run(server)
        assert [c for c in POST_AS_OF_CANARIES if c in result.evidence_text] == []

    def test_the_tool_result_went_back_with_its_call_id(self, server) -> None:
        # The OpenAI API matches a result to its call by id. Sending tool_name
        # instead gets the turn rejected.
        self.run(server)
        tool_messages = [m for m in server[1]["messages"] if m["role"] == "tool"]
        assert tool_messages
        assert tool_messages[0]["tool_call_id"] == "call_1"
