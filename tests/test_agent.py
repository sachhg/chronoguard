"""Tests for the agent loop.

Offline: a scripted client stands in for Ollama so both modes can be driven
turn by turn, including the ugly outputs small models actually produce.
"""

from __future__ import annotations

from typing import Any

import pytest

from chronoguard.agent import (
    AgentConfig,
    AgentRunner,
    extract_json_object,
    format_evidence,
    run_agent,
    tool_schema,
)
from chronoguard.evidence import EvidenceRecord
from chronoguard.guard import TemporalGuard
from chronoguard.fixtures import FIXTURE_AS_OF, POST_AS_OF_CANARIES, build_fixture_toolset
from chronoguard.interception import AuditLog
from chronoguard.ollama import ChatResponse

TASK = "When will Meridian ship and what will it cost?"


class ScriptedClient:
    """Replays a queue of canned replies and records what it was sent."""

    def __init__(self, replies: list[dict[str, Any]], *, tools: bool = False) -> None:
        self.replies = list(replies)
        self.tools = tools
        self.requests: list[dict[str, Any]] = []

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
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
    }


def action(name: str, **arguments: Any) -> dict[str, Any]:
    import json

    return text(json.dumps({"tool": name, "arguments": arguments}))


def answer(value: str) -> dict[str, Any]:
    import json

    return text(json.dumps({"answer": value}))


@pytest.fixture
def guard() -> TemporalGuard:
    return TemporalGuard(FIXTURE_AS_OF)


@pytest.fixture
def config() -> AgentConfig:
    return AgentConfig(task=TASK, as_of=FIXTURE_AS_OF, model="scripted-model")


@pytest.fixture
def tools(guard: TemporalGuard) -> dict[str, Any]:
    return build_fixture_toolset(guard, AuditLog())


class TestExtractJsonObject:
    def test_bare_object(self) -> None:
        assert extract_json_object('{"answer": "hi"}') == {"answer": "hi"}

    def test_inside_a_code_fence(self) -> None:
        blob = 'Sure!\n```json\n{"tool": "web_search", "arguments": {"query": "x"}}\n```\n'
        assert extract_json_object(blob)["tool"] == "web_search"

    def test_surrounded_by_prose(self) -> None:
        assert extract_json_object('I will search. {"tool": "s"} Hope that helps.') == {"tool": "s"}

    def test_nested_objects(self) -> None:
        parsed = extract_json_object('{"tool": "s", "arguments": {"filters": {"a": 1}}}')
        assert parsed["arguments"]["filters"] == {"a": 1}

    def test_braces_inside_strings_do_not_confuse_it(self) -> None:
        assert extract_json_object('{"answer": "use {this} format"}') == {"answer": "use {this} format"}

    def test_escaped_quotes(self) -> None:
        assert extract_json_object(r'{"answer": "he said \"hi\""}') == {"answer": 'he said "hi"'}

    def test_first_valid_object_wins_over_earlier_junk(self) -> None:
        assert extract_json_object('{not json} then {"answer": "ok"}') == {"answer": "ok"}

    @pytest.mark.parametrize("blob", ["", "no json here", "{", "{unclosed: ", "[1, 2, 3]"])
    def test_nothing_parseable(self, blob: str) -> None:
        assert extract_json_object(blob) is None


class TestToolSchema:
    def test_schema_describes_the_real_tool_through_the_wrapper(self, tools: dict[str, Any]) -> None:
        schema = tool_schema(tools["web_search"], "web_search")["function"]
        assert schema["name"] == "web_search"
        assert set(schema["parameters"]["properties"]) == {"query", "limit"}
        assert schema["parameters"]["required"] == ["query"]

    def test_types_are_mapped(self, tools: dict[str, Any]) -> None:
        properties = tool_schema(tools["document_store"], "document_store")["function"]["parameters"][
            "properties"
        ]
        assert properties["query"]["type"] == "string"
        assert properties["k"]["type"] == "integer"

    def test_description_comes_from_the_docstring(self, tools: dict[str, Any]) -> None:
        assert "Search the web" in tool_schema(tools["web_search"], "web_search")["function"]["description"]

    def test_varargs_are_skipped(self) -> None:
        def messy(query: str, *args: Any, **kwargs: Any) -> list:
            """Messy tool."""
            return []

        assert list(tool_schema(messy)["function"]["parameters"]["properties"]) == ["query"]


class TestFormatEvidence:
    def test_empty_says_no_results_without_mentioning_filtering(self) -> None:
        rendered = format_evidence([])
        assert rendered == "(no results)"
        assert "filter" not in rendered.lower()

    def test_records_are_numbered_and_dated(self) -> None:
        record = EvidenceRecord.from_source("body text", "src-1", published_at="2023-05-09T00:00:00Z")
        rendered = format_evidence([record])
        assert "[1] src-1 (published 2023-05-09)" in rendered
        assert "body text" in rendered

    def test_long_content_is_truncated(self) -> None:
        record = EvidenceRecord.from_source("x" * 5000, "src-1", published_at="2023-05-09T00:00:00Z")
        assert len(format_evidence([record], max_chars=100)) < 300

    def test_undated_records_are_labelled(self) -> None:
        rendered = format_evidence([EvidenceRecord.from_source("body", "src-1")])
        assert "undated" in rendered


class TestReactMode:
    def test_full_loop_search_then_answer(self, config: AgentConfig, tools: dict[str, Any]) -> None:
        client = ScriptedClient([action("web_search", query="meridian"), answer("summer, no price yet")])
        run = run_agent(config, tools, client=client)

        assert run.mode == "react"
        assert run.final_answer == "summer, no price yet"
        assert run.stopped_because == "answered"
        assert [s.kind for s in run.steps] == ["tool_call", "answer"]
        assert run.used_tools

    def test_evidence_the_agent_saw_is_collected(self, config: AgentConfig, tools: dict[str, Any]) -> None:
        client = ScriptedClient([action("web_search", query="meridian price"), answer("done")])
        run = run_agent(config, tools, client=client)
        assert run.evidence
        assert all(r.published_at < run.config.as_of for r in run.evidence)

    def test_evidence_is_deduplicated_across_calls(self, config: AgentConfig, tools: dict[str, Any]) -> None:
        client = ScriptedClient(
            [action("web_search", query="meridian"), action("web_search", query="meridian"), answer("done")]
        )
        run = run_agent(config, tools, client=client)
        ids = [r.source_id for r in run.evidence]
        assert len(ids) == len(set(ids))

    def test_observation_contains_no_post_as_of_content(
        self, config: AgentConfig, tools: dict[str, Any]
    ) -> None:
        client = ScriptedClient(
            [action("web_search", query="meridian price launch october ferrous"), answer("done")]
        )
        run_agent(config, tools, client=client)
        observations = " ".join(
            m.content for m in client.requests[-1]["messages"] if m.role == "user"
        )
        assert [c for c in POST_AS_OF_CANARIES if c in observations] == []

    def test_unparseable_reply_gets_nudged_then_recovers(
        self, config: AgentConfig, tools: dict[str, Any]
    ) -> None:
        client = ScriptedClient([text("I think I should search first!"), answer("recovered")])
        run = run_agent(config, tools, client=client)
        assert [s.kind for s in run.steps] == ["nudge", "answer"]
        assert run.final_answer == "recovered"
        assert "exactly one JSON object" in client.requests[-1]["messages"][-1].content

    def test_nudges_are_capped_and_the_text_becomes_the_answer(
        self, config: AgentConfig, tools: dict[str, Any]
    ) -> None:
        config = config.model_copy(update={"max_format_retries": 1})
        client = ScriptedClient([text("rambling one"), text("rambling two")])
        run = run_agent(config, tools, client=client)
        assert run.final_answer == "rambling two"
        assert run.stopped_because == "answered"

    def test_max_steps_stops_the_loop(self, config: AgentConfig, tools: dict[str, Any]) -> None:
        config = config.model_copy(update={"max_steps": 3})
        client = ScriptedClient([action("web_search", query="a")] * 10)
        run = run_agent(config, tools, client=client)
        assert run.stopped_because == "max_steps"
        assert len(run.tool_calls) == 3

    def test_unknown_tool_is_reported_back_to_the_model(
        self, config: AgentConfig, tools: dict[str, Any]
    ) -> None:
        client = ScriptedClient([action("wikipedia", query="x"), answer("ok")])
        run = run_agent(config, tools, client=client)
        assert any(s.kind == "error" for s in run.steps)
        observation = client.requests[-1]["messages"][-1].content
        assert "no tool named wikipedia" in observation
        assert "web_search" in observation

    def test_bad_arguments_are_reported_not_raised(
        self, config: AgentConfig, tools: dict[str, Any]
    ) -> None:
        client = ScriptedClient([action("web_search", nonsense=1), answer("ok")])
        run = run_agent(config, tools, client=client)
        assert any(s.kind == "error" for s in run.steps)
        assert run.final_answer == "ok"

    def test_string_arguments_are_coerced_to_a_query(
        self, config: AgentConfig, tools: dict[str, Any]
    ) -> None:
        client = ScriptedClient([text('{"tool": "web_search", "arguments": "meridian"}'), answer("ok")])
        run = run_agent(config, tools, client=client)
        assert run.tool_calls[0].arguments == {"query": "meridian"}

    def test_the_system_prompt_lists_the_tools_and_the_date(
        self, config: AgentConfig, tools: dict[str, Any]
    ) -> None:
        client = ScriptedClient([answer("ok")])
        run_agent(config, tools, client=client)
        system = client.requests[0]["messages"][0].content
        assert "2023-06-01" in system
        assert "web_search" in system and "document_store" in system

    def test_no_tools_are_advertised_natively_in_react_mode(
        self, config: AgentConfig, tools: dict[str, Any]
    ) -> None:
        client = ScriptedClient([answer("ok")])
        run_agent(config, tools, client=client)
        assert client.requests[0]["tools"] is None


class TestNativeMode:
    def test_full_loop_tool_call_then_answer(self, config: AgentConfig, tools: dict[str, Any]) -> None:
        client = ScriptedClient([call("web_search", query="meridian"), text("summer, no price")], tools=True)
        run = run_agent(config, tools, client=client)

        assert run.mode == "native"
        assert run.final_answer == "summer, no price"
        assert [s.kind for s in run.steps] == ["tool_call", "answer"]

    def test_tool_definitions_are_sent(self, config: AgentConfig, tools: dict[str, Any]) -> None:
        client = ScriptedClient([text("answered")], tools=True)
        run_agent(config, tools, client=client)
        names = [t["function"]["name"] for t in client.requests[0]["tools"]]
        assert sorted(names) == ["document_store", "web_search"]

    def test_observations_go_back_as_tool_messages(
        self, config: AgentConfig, tools: dict[str, Any]
    ) -> None:
        client = ScriptedClient([call("web_search", query="meridian"), text("done")], tools=True)
        run_agent(config, tools, client=client)
        tool_messages = [m for m in client.requests[-1]["messages"] if m.role == "tool"]
        assert tool_messages
        assert tool_messages[0].tool_name == "web_search"
        assert [c for c in POST_AS_OF_CANARIES if c in tool_messages[0].content] == []

    def test_several_calls_in_one_turn(self, config: AgentConfig, tools: dict[str, Any]) -> None:
        both = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "web_search", "arguments": {"query": "meridian"}}},
                {"function": {"name": "document_store", "arguments": {"query": "meridian"}}},
            ],
        }
        client = ScriptedClient([both, text("done")], tools=True)
        run = run_agent(config, tools, client=client)
        assert [s.tool for s in run.tool_calls] == ["web_search", "document_store"]

    def test_stringified_arguments_are_parsed(self, config: AgentConfig, tools: dict[str, Any]) -> None:
        reply = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "web_search", "arguments": '{"query": "meridian"}'}}],
        }
        client = ScriptedClient([reply, text("done")], tools=True)
        run = run_agent(config, tools, client=client)
        assert run.tool_calls[0].arguments == {"query": "meridian"}

    def test_max_steps_applies_here_too(self, config: AgentConfig, tools: dict[str, Any]) -> None:
        config = config.model_copy(update={"max_steps": 2})
        client = ScriptedClient([call("web_search", query="a")] * 5, tools=True)
        run = run_agent(config, tools, client=client)
        assert run.stopped_because == "max_steps"
        assert len(run.tool_calls) == 2


class TestModeSelection:
    def test_auto_picks_native_when_the_model_supports_tools(
        self, config: AgentConfig, tools: dict[str, Any]
    ) -> None:
        assert run_agent(config, tools, client=ScriptedClient([text("x")], tools=True)).mode == "native"

    def test_auto_falls_back_to_react(self, config: AgentConfig, tools: dict[str, Any]) -> None:
        assert run_agent(config, tools, client=ScriptedClient([answer("x")], tools=False)).mode == "react"

    def test_mode_can_be_forced(self, config: AgentConfig, tools: dict[str, Any]) -> None:
        config = config.model_copy(update={"mode": "react"})
        run = run_agent(config, tools, client=ScriptedClient([answer("x")], tools=True))
        assert run.mode == "react"

    def test_model_is_discovered_when_not_configured(self, tools: dict[str, Any]) -> None:
        config = AgentConfig(task=TASK, as_of=FIXTURE_AS_OF)
        run = run_agent(config, tools, client=ScriptedClient([answer("x")]))
        assert run.model == "scripted-model"


class TestSafetyChecks:
    def test_a_guard_disagreeing_with_the_config_is_refused(self, tools: dict[str, Any]) -> None:
        config = AgentConfig(task=TASK, as_of="2024-01-01T00:00:00Z", model="m")
        with pytest.raises(ValueError, match="guarded at"):
            run_agent(config, tools, client=ScriptedClient([answer("x")]))

    def test_no_tools_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least one tool"):
            AgentRunner({})

    def test_the_run_exposes_the_shared_audit_log(self, config: AgentConfig, tools: dict[str, Any]) -> None:
        client = ScriptedClient([action("web_search", query="meridian price"), answer("done")])
        run = run_agent(config, tools, client=client)
        assert run.audit is tools["web_search"].audit
        assert run.audit.filtered_count > 0
        assert run.audit.counts["future"] > 0

    def test_summary_mentions_the_essentials(self, config: AgentConfig, tools: dict[str, Any]) -> None:
        client = ScriptedClient([action("web_search", query="meridian"), answer("done")])
        summary = run_agent(config, tools, client=client).summary()
        assert "scripted-model" in summary and "react" in summary and "filtered" in summary

    def test_a_run_serializes_to_json(self, config: AgentConfig, tools: dict[str, Any]) -> None:
        client = ScriptedClient([action("web_search", query="meridian"), answer("done")])
        blob = run_agent(config, tools, client=client).model_dump_json()
        assert "final_answer" in blob
