"""Smoke tests for the CLI entrypoint and package metadata."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

import chronoguard
from chronoguard import cli
from chronoguard.cli import app
from chronoguard.fixtures import POST_AS_OF_CANARIES
from chronoguard.ollama import ModelInfo, OllamaUnavailable
from helpers import ScriptedClient, action, answer

runner = CliRunner()


def test_package_exposes_version() -> None:
    assert isinstance(chronoguard.__version__, str)
    assert chronoguard.__version__


def test_help_exits_zero_and_lists_usage() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Usage: chronoguard" in result.output


def test_version_command_prints_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == chronoguard.__version__


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == chronoguard.__version__


def test_bare_invocation_shows_help_not_traceback() -> None:
    result = runner.invoke(app, [])
    assert "Usage: chronoguard" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


class FakeModelsClient:
    """Stands in for OllamaClient in `chronoguard models`."""

    def __init__(self, models: list[str], tools: set[str] | None = None, error: Exception | None = None) -> None:
        self._models = models
        self._tools = tools or set()
        self._error = error
        self.host = "http://fake:11434"

    def list_models(self) -> list[ModelInfo]:
        if self._error:
            raise self._error
        return [ModelInfo(name=n, parameter_size="4.3B") for n in self._models]

    def supports_tools(self, name: str) -> bool:
        return name in self._tools


class TestModelsCommand:
    def test_lists_discovered_models_and_their_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            cli, "OllamaClient", lambda **kw: FakeModelsClient(["gemma3:4b", "qwen3:8b"], {"qwen3:8b"})
        )
        result = runner.invoke(app, ["models"])
        assert result.exit_code == 0
        assert "gemma3:4b" in result.output and "react fallback" in result.output
        assert "qwen3:8b" in result.output and "native tools" in result.output

    def test_unreachable_server_exits_nonzero_with_advice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            cli,
            "OllamaClient",
            lambda **kw: FakeModelsClient([], error=OllamaUnavailable("connection refused")),
        )
        result = runner.invoke(app, ["models"])
        assert result.exit_code == 1
        assert "ollama serve" in result.output

    def test_no_models_installed_exits_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli, "OllamaClient", lambda **kw: FakeModelsClient([]))
        result = runner.invoke(app, ["models"])
        assert result.exit_code == 1
        assert "ollama pull" in result.output


class TestRunCommand:
    def _wire(self, monkeypatch: pytest.MonkeyPatch, replies: list) -> None:
        monkeypatch.setattr(cli, "OllamaClient", lambda **kw: ScriptedClient(replies))

    def test_happy_path_reports_the_answer_and_the_counts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._wire(monkeypatch, [action("web_search", query="meridian price"), answer("summer, no price yet")])
        result = runner.invoke(app, ["run", "when does meridian ship?", "--max-steps", "3"])

        assert result.exit_code == 0, result.output
        assert "summer, no price yet" in result.output
        assert "record(s) withheld" in result.output
        assert "scripted-model" in result.output

    def test_output_never_carries_post_as_of_content(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._wire(monkeypatch, [action("web_search", query="meridian price october ferrous"), answer("ok")])
        result = runner.invoke(app, ["run", "when does meridian ship?"])
        assert [c for c in POST_AS_OF_CANARIES if c in result.output] == []

    def test_json_output_is_machine_readable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._wire(monkeypatch, [action("web_search", query="meridian"), answer("done")])
        result = runner.invoke(app, ["run", "task", "--json"])
        payload = json.loads(result.output)
        assert payload["final_answer"] == "done"
        assert payload["audit"]["calls"]

    def test_a_naive_as_of_is_rejected_with_advice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._wire(monkeypatch, [answer("x")])
        result = runner.invoke(app, ["run", "task", "--as-of", "2023-06-01"])
        assert result.exit_code == 2
        assert "explicit offset" in result.output

    def test_unreachable_server_exits_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(**kw: object) -> None:
            raise OllamaUnavailable("connection refused")

        monkeypatch.setattr(cli, "OllamaClient", boom)
        result = runner.invoke(app, ["run", "task"])
        assert result.exit_code == 1
        assert "ollama serve" in result.output

    def test_warn_policy_is_selectable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._wire(monkeypatch, [action("web_search", query="meridian price"), answer("done")])
        result = runner.invoke(app, ["run", "task", "--policy", "warn"])
        assert result.exit_code == 0
        assert "policy: warn" in result.output
