"""Integration tests against a real local Ollama server.

Skipped, not failed, when there's no server or no models. Run them with:

    pytest -m integration

The headline assertion is the phase 3 one: after a full agent loop against a
real model, no post-as-of fixture content appears in the final answer or in the
evidence it cited. The scenario is fictional, so the model can't know any of it
from training. Anything that leaks came through a tool, which makes this a clean
test of the interception layer rather than a test of what the model happens to
remember.
"""

from __future__ import annotations

from typing import Any

import pytest

from chronoguard.agent import AgentConfig, AgentRun, run_agent
from chronoguard.fixtures import FIXTURE_AS_OF, POST_AS_OF_CANARIES, build_fixture_toolset
from chronoguard.guard import TemporalGuard
from chronoguard.interception import AuditLog
from chronoguard.ollama import OllamaClient

pytestmark = pytest.mark.integration

TASK = (
    "When will Halden Systems ship Meridian, and what will it cost per seat? "
    "Search for evidence before answering, and cite your sources."
)


def canaries_in(text: str) -> list[str]:
    return [c for c in POST_AS_OF_CANARIES if c in text]


@pytest.fixture(scope="session")
def live_run(ollama_client: OllamaClient, ollama_model: str) -> AgentRun:
    """One real agent loop, shared by the assertions below to keep runtime sane."""
    guard = TemporalGuard(FIXTURE_AS_OF)
    tools = build_fixture_toolset(guard, AuditLog())
    config = AgentConfig(task=TASK, as_of=FIXTURE_AS_OF, model=ollama_model, max_steps=5)
    return run_agent(config, tools, client=ollama_client)


class TestServer:
    def test_models_are_discovered_at_runtime(self, ollama_client: OllamaClient) -> None:
        models = ollama_client.list_models()
        assert models, "the fixture should have skipped if nothing was installed"
        assert all(m.name for m in models)

    def test_capabilities_are_readable_for_every_installed_model(
        self, ollama_client: OllamaClient
    ) -> None:
        for name in ollama_client.model_names():
            assert isinstance(ollama_client.supports_tools(name), bool)

    def test_a_plain_chat_round_trip_works(
        self, ollama_client: OllamaClient, ollama_model: str
    ) -> None:
        response = ollama_client.chat(
            ollama_model, [{"role": "user", "content": "Reply with the single word: pong"}]
        )
        assert response.content.strip()


class TestLiveAgentRun:
    def test_the_loop_finishes_with_an_answer(self, live_run: AgentRun) -> None:
        assert live_run.final_answer.strip(), live_run.summary()
        assert live_run.stopped_because in ("answered", "max_steps")

    def test_the_agent_actually_used_the_guarded_tools(self, live_run: AgentRun) -> None:
        assert live_run.used_tools, f"model never called a tool: {live_run.summary()}"
        assert live_run.audit.call_count >= 1

    def test_the_guard_did_real_work(self, live_run: AgentRun) -> None:
        # If nothing was filtered, the run proves nothing about the guard.
        assert live_run.audit.filtered_count > 0, live_run.audit.summary()
        assert live_run.audit.counts["future"] > 0

    def test_no_post_as_of_content_reaches_the_final_answer(self, live_run: AgentRun) -> None:
        leaked = canaries_in(live_run.final_answer)
        assert leaked == [], f"leaked {leaked} into the answer:\n{live_run.final_answer}"

    def test_no_post_as_of_content_reaches_the_cited_sources(self, live_run: AgentRun) -> None:
        leaked = canaries_in(live_run.evidence_text)
        assert leaked == [], f"leaked {leaked} into the evidence"

    def test_every_record_the_agent_saw_predates_the_cutoff(self, live_run: AgentRun) -> None:
        for record in live_run.evidence:
            assert record.published_at is not None
            assert record.published_at < live_run.config.as_of, record.source_id

    def test_the_agent_saw_something_worth_reasoning_over(self, live_run: AgentRun) -> None:
        assert live_run.evidence, "no evidence survived, the leak tests would be vacuous"


class TestNativeToolCalling:
    """Only runs if a tool-capable model is installed. Skips cleanly otherwise."""

    def test_native_mode_loop_leaks_nothing(
        self, ollama_client: OllamaClient, tool_capable_model: str
    ) -> None:
        guard = TemporalGuard(FIXTURE_AS_OF)
        tools = build_fixture_toolset(guard, AuditLog())
        config = AgentConfig(
            task=TASK,
            as_of=FIXTURE_AS_OF,
            model=tool_capable_model,
            mode="native",
            max_steps=5,
        )
        run = run_agent(config, tools, client=ollama_client)

        assert run.mode == "native"
        assert run.final_answer.strip()
        assert canaries_in(run.final_answer) == []
        assert canaries_in(run.evidence_text) == []
