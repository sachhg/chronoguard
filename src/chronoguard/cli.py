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
from chronoguard.probe import LeakageProbe, load_model_cutoffs, load_probe_cases

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


@app.command()
def probe(
    as_of: Annotated[
        str, typer.Option("--as-of", help="The instant to simulate. Needs a timezone offset.")
    ] = FIXTURE_AS_OF,
    model: Annotated[
        Optional[str], typer.Option(help="Ollama model. Discovered at runtime if unset.")
    ] = None,
    cases: Annotated[
        Optional[str], typer.Option(help="Custom probe case file. Defaults to the packaged set.")
    ] = None,
    cutoffs: Annotated[
        Optional[str], typer.Option(help="Custom model cutoff file.")
    ] = None,
    judge: Annotated[
        Optional[str],
        typer.Option(help="Model to use as an LLM judge for free-text answers."),
    ] = None,
    max_future: Annotated[
        Optional[int], typer.Option(help="Cap on probe questions asked.")
    ] = None,
    max_control: Annotated[
        Optional[int], typer.Option(help="Cap on control questions asked.")
    ] = None,
    host: Annotated[Optional[str], typer.Option(help="Ollama host.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit the report as JSON.")] = False,
) -> None:
    """Measure what a model already knows about the future, with no tools at all.

    This is the half filtering cannot fix. A correct answer here came from the
    weights, not from anything you handed it.
    """
    try:
        client = OllamaClient(host=host)
        chosen = model or client.pick_model()
        report = LeakageProbe(
            client,
            cases=load_probe_cases(cases) if cases else None,
            cutoffs=load_model_cutoffs(cutoffs) if cutoffs else None,
            judge_model=judge,
        ).run(chosen, as_of, max_future_cases=max_future, max_control_cases=max_control)
    except OllamaUnavailable as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        typer.echo("Start one with `ollama serve`.", err=True)
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    if as_json:
        payload = report.model_dump(mode="json")
        payload["leakage_score"] = report.leakage_score
        payload["control_score"] = report.control_score
        payload["risk_level"] = report.risk_level
        typer.echo(json.dumps(payload, indent=2))
        return

    colour = {
        "high": typer.colors.RED,
        "elevated": typer.colors.YELLOW,
        "inconclusive": typer.colors.YELLOW,
        "low": typer.colors.GREEN,
    }[report.risk_level]
    typer.secho(report.summary(), fg=colour, bold=True)
    typer.echo(f"\n{report.cutoff_risk.reason}")

    typer.echo("\nasked with no tools:")
    for item in report.outcomes:
        mark = "LEAK" if item.revealed and item.kind == "future" else ("ok  " if item.revealed else "  . ")
        typer.echo(f"  {mark} [{item.kind:<7}] {item.case_id:<22} {item.response[:56]!r}")

    if report.leaked:
        typer.echo("\nthe model produced these with zero evidence in context:")
        for item in report.leaked:
            typer.echo(f"  {item.case_id}: expected {item.expected!r} (matched by {item.method})")

    if report.risk_level == "inconclusive" and report.future_outcomes:
        typer.echo(
            "\nIt also failed most controls, so a low leakage score here means it "
            "can't answer, not that it's blinded."
        )


def main() -> None:
    """Console-script entrypoint."""
    app()


if __name__ == "__main__":  # pragma: no cover - exercised via `python -m chronoguard`
    main()
