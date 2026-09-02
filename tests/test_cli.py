"""Smoke tests for the CLI entrypoint and package metadata."""

from __future__ import annotations

from typer.testing import CliRunner

import chronoguard
from chronoguard.cli import app

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
