"""Command line interface for ChronoGuard."""

from __future__ import annotations

import json
from typing import Annotated, Optional

import typer

from chronoguard import __version__
from chronoguard.agent import AgentConfig, run_agent
from chronoguard.fixtures import FIXTURE_AS_OF, build_fixture_toolset
from chronoguard.guard import GuardPolicy, TemporalGuard
from chronoguard.interception import AuditLog
from chronoguard.ollama import OllamaClient, OllamaUnavailable

app = typer.Typer(
    name="chronoguard",
    help=(
        "Run LLM agents as if it were a past date, and measure how well the "
        "blinding holds.\n\n"
        "ChronoGuard filters tool results by publication date (tool leakage) "
        "and probes the model for facts it already knows (parametric leakage)."
    ),
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def root(
    _version: Annotated[
        Optional[bool],
        typer.Option(
            "--version",
            help="Print the ChronoGuard version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = None,
) -> None:
    """ChronoGuard: point-in-time leakage guard for LLM agents."""


@app.command()
def version() -> None:
    """Print the installed ChronoGuard version."""
    typer.echo(__version__)


@app.command()
def models(
    host: Annotated[Optional[str], typer.Option(help="Ollama host. Defaults to OLLAMA_HOST.")] = None,
) -> None:
    """List locally installed Ollama models and whether they can call tools."""
    client = OllamaClient(host=host)
    try:
        installed = client.list_models()
    except OllamaUnavailable as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        typer.echo("Start one with `ollama serve`.", err=True)
        raise typer.Exit(code=1) from exc

    if not installed:
        typer.echo(f"No models installed on {client.host}. Try `ollama pull gemma3:4b`.")
        raise typer.Exit(code=1)

    typer.echo(f"{len(installed)} model(s) on {client.host}:\n")
    for model in installed:
        mode = "native tools" if client.supports_tools(model.name) else "react fallback"
        size = model.parameter_size or "?"
        typer.echo(f"  {model.name:<32} {size:>8}  {mode}")


@app.command()
def run(
    task: Annotated[str, typer.Argument(help="What to ask the agent.")],
    as_of: Annotated[
        str, typer.Option("--as-of", help="The instant to simulate. Needs a timezone offset.")
    ] = FIXTURE_AS_OF,
    model: Annotated[
        Optional[str], typer.Option(help="Ollama model. Discovered at runtime if unset.")
    ] = None,
    mode: Annotated[str, typer.Option(help="auto, native, or react.")] = "auto",
    max_steps: Annotated[int, typer.Option(help="Cap on loop iterations.")] = 6,
    policy: Annotated[
        str, typer.Option(help="strict drops post-as-of evidence, warn keeps and flags it.")
    ] = "strict",
    host: Annotated[Optional[str], typer.Option(help="Ollama host.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit the run as JSON.")] = False,
) -> None:
    """Run one agent task against the fixture corpora, guarded at --as-of.

    The tools here are the packaged fixtures, so this works offline apart from
    the model itself. Point it at your own guarded tools from Python for real
    work.
    """
    try:
        guard = TemporalGuard(as_of, policy=GuardPolicy(policy))
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    tools = build_fixture_toolset(guard, AuditLog())
    config = AgentConfig(
        task=task, as_of=guard.as_of, model=model, mode=mode, max_steps=max_steps
    )

    try:
        result = run_agent(config, tools, client=OllamaClient(host=host))
    except OllamaUnavailable as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        typer.echo("Start one with `ollama serve`.", err=True)
        raise typer.Exit(code=1) from exc

    if as_json:
        typer.echo(result.model_dump_json(indent=2))
        return

    typer.echo(f"model      {result.model} [{result.mode}]")
    typer.echo(f"as of      {guard.as_of.isoformat()} (policy: {guard.policy.value})")
    typer.echo(f"tool calls {len(result.tool_calls)}")
    for step in result.tool_calls:
        typer.echo(f"           {step.tool}({json.dumps(step.arguments)}) "
                   f"kept {step.kept_count}, filtered {step.filtered_count}")
    typer.echo(f"evidence   {len(result.evidence)} record(s) reached the agent")
    typer.echo(f"filtered   {result.audit.filtered_count} record(s) withheld")
    typer.echo(f"verdicts   {result.audit.counts}")
    typer.echo("\nanswer:")
    typer.echo(result.final_answer or "(no answer)")
    typer.echo("\nsources the agent was given:")
    for record in result.evidence:
        stamp = record.published_at.date().isoformat() if record.published_at else "undated"
        typer.echo(f"  {stamp}  {record.source_id}")


def main() -> None:
    """Console-script entrypoint."""
    app()


if __name__ == "__main__":  # pragma: no cover - exercised via `python -m chronoguard`
    main()
