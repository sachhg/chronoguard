"""Smoke tests for the CLI entrypoint and package metadata."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

import chronoguard
from chronoguard import cli
from chronoguard.cli import app
from chronoguard.fixtures import POST_AS_OF_CANARIES
from chronoguard.ollama import ModelInfo, OllamaTimeout, OllamaUnavailable
from helpers import CannedProbeClient, ScenarioClient, ScriptedClient, action, answer

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


class TestProbeCommand:
    def _wire(self, monkeypatch: pytest.MonkeyPatch, answers: dict[str, str]) -> None:
        client = CannedProbeClient(answers)
        client.pick_model = lambda **kw: "scripted-model"  # type: ignore[method-assign]
        monkeypatch.setattr(cli, "OllamaClient", lambda **kw: client)

    def test_reports_leakage_and_names_the_cases(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._wire(monkeypatch, {"Nobel Peace Prize": "Narges Mohammadi"})
        result = runner.invoke(app, ["probe", "--max-future", "3", "--max-control", "2"])

        assert result.exit_code == 0, result.output
        assert "leakage" in result.output
        assert "nobel-peace-2023" in result.output
        assert "zero evidence in context" in result.output

    def test_cutoff_risk_is_explained_before_the_scores(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._wire(monkeypatch, {})
        result = runner.invoke(app, ["probe", "--model", "gemma3:4b", "--max-future", "1", "--max-control", "1"])
        assert "trained on data up to" in result.output
        assert "Filtering cannot undo that" in result.output

    def test_json_output_carries_the_scores(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._wire(monkeypatch, {"Nobel Peace Prize": "Narges Mohammadi"})
        result = runner.invoke(
            app, ["probe", "--json", "--max-future", "3", "--max-control", "2"]
        )
        payload = json.loads(result.output)
        assert payload["risk_level"] in ("high", "elevated", "low", "inconclusive")
        assert 0.0 <= payload["leakage_score"] <= 1.0
        assert payload["cutoff_risk"]["level"] in ("high", "low", "unknown")
        assert payload["outcomes"]

    def test_a_naive_as_of_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._wire(monkeypatch, {})
        result = runner.invoke(app, ["probe", "--as-of", "2023-06-01"])
        assert result.exit_code == 2
        assert "explicit offset" in result.output

    def test_unreachable_server_exits_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(**kw: object) -> None:
            raise OllamaUnavailable("connection refused")

        monkeypatch.setattr(cli, "OllamaClient", boom)
        result = runner.invoke(app, ["probe"])
        assert result.exit_code == 1
        assert "ollama serve" in result.output

    def test_a_custom_case_file_is_used(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        path = tmp_path / "cases.json"
        path.write_text(
            json.dumps(
                {
                    "cases": [
                        {
                            "id": "only-mine",
                            "question": "Who won?",
                            "answer": "Nobody",
                            "knowable_from": "2024-01-01T00:00:00Z",
                        }
                    ]
                }
            )
        )
        self._wire(monkeypatch, {})
        result = runner.invoke(app, ["probe", "--cases", str(path)])
        assert "only-mine" in result.output
        assert "nobel-peace-2023" not in result.output


class TestReportCommand:
    def _wire(self, monkeypatch: pytest.MonkeyPatch) -> ScenarioClient:
        client = ScenarioClient(
            [action("web_search", query="meridian price"), answer("Summer, no price yet.")],
            probe_answers={"Nobel Peace Prize": "Narges Mohammadi"},
            claims=["Halden confirmed a summer window.", "No price has been announced."],
        )
        monkeypatch.setattr(cli, "OllamaClient", lambda **kw: client)
        return client

    def test_prints_the_full_text_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._wire(monkeypatch)
        result = runner.invoke(
            app, ["report", "when does meridian ship?", "--max-future", "2", "--max-control", "1"]
        )
        assert result.exit_code == 0, result.output
        for heading in ("RISK:", "TOOL LEAKAGE", "PARAMETRIC LEAKAGE", "CLAIMS IN THE ANSWER"):
            assert heading in result.output

    def test_output_never_carries_post_as_of_content(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._wire(monkeypatch)
        result = runner.invoke(app, ["report", "meridian price october ferrous", "--skip-probe"])
        assert [c for c in POST_AS_OF_CANARIES if c in result.output] == []

    def test_json_flag_emits_only_the_summary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._wire(monkeypatch)
        result = runner.invoke(
            app, ["report", "task", "--json", "--max-future", "2", "--max-control", "1"]
        )
        payload = json.loads(result.output)
        assert payload["headline"]["risk"] in ("high", "elevated", "low", "unknown")
        assert payload["tool_leakage"]["records_filtered"] >= 0
        assert payload["parametric_leakage"]["leakage_score"] >= 0
        assert "flagged" in payload["claims"]

    def test_json_out_writes_the_file_alongside_the_text_report(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        self._wire(monkeypatch)
        target = tmp_path / "summary.json"
        result = runner.invoke(
            app, ["report", "task", "--json-out", str(target), "--skip-probe", "--skip-claims"]
        )
        assert result.exit_code == 0
        assert "ChronoGuard report" in result.output
        assert str(target) in result.output
        assert json.loads(target.read_text())["task"] == "task"

    def test_stages_can_be_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = self._wire(monkeypatch)
        result = runner.invoke(app, ["report", "task", "--skip-probe", "--skip-claims"])
        assert "probe skipped" in result.output
        assert "step skipped" in result.output
        assert set(client.stages) == {"agent"}

    def test_a_naive_as_of_is_rejected_with_advice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._wire(monkeypatch)
        result = runner.invoke(app, ["report", "task", "--as-of", "2023-06-01"])
        assert result.exit_code == 2
        assert "timezone" in result.output.lower() or "offset" in result.output.lower()

    def test_unreachable_server_exits_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(**kw: object) -> None:
            raise OllamaUnavailable("connection refused")

        monkeypatch.setattr(cli, "OllamaClient", boom)
        result = runner.invoke(app, ["report", "task"])
        assert result.exit_code == 1
        assert "ollama serve" in result.output


class TestOllamaErrorAdvice:
    """A timeout must not be answered with "start the server"."""

    @pytest.mark.parametrize("command", [["models"], ["run", "task"], ["probe"], ["report", "task"]])
    def test_a_timeout_does_not_suggest_starting_the_server(
        self, monkeypatch: pytest.MonkeyPatch, command: list[str]
    ) -> None:
        def slow(**kw: object) -> None:
            raise OllamaTimeout("qwen3:4b did not answer within 600s, the model is just slow")

        monkeypatch.setattr(cli, "OllamaClient", slow)
        result = runner.invoke(app, command)
        assert result.exit_code == 1
        assert "just slow" in result.output
        assert "ollama serve" not in result.output

    @pytest.mark.parametrize("command", [["models"], ["run", "task"], ["probe"], ["report", "task"]])
    def test_an_unreachable_server_still_says_to_start_it(
        self, monkeypatch: pytest.MonkeyPatch, command: list[str]
    ) -> None:
        def refused(**kw: object) -> None:
            raise OllamaUnavailable("connection refused")

        monkeypatch.setattr(cli, "OllamaClient", refused)
        result = runner.invoke(app, command)
        assert result.exit_code == 1
        assert "ollama serve" in result.output
