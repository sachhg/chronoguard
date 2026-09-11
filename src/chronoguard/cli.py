"""Command line interface for ChronoGuard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Optional

import typer
from pydantic import ValidationError

from chronoguard._version import __version__
from chronoguard.agent import AgentConfig, run_agent
from chronoguard.fixtures import FIXTURE_AS_OF, build_fixture_toolset
from chronoguard.guard import GuardPolicy, TemporalGuard
from chronoguard.interception import AuditLog, MappingAdapter
from chronoguard.backends import (
    BackendTimeout,
    BackendUnavailable,
    ChatBackend,
    OpenAICompatClient,
)
from chronoguard.ollama import OllamaClient, OllamaUnavailable
from chronoguard.preflight import inspect_corpus, load_rows
from chronoguard.probe import LeakageProbe, load_model_cutoffs, load_probe_cases
from chronoguard.report import RISK_ORDER, ScenarioConfig, risk_at_least, run_scenario

#: What the shell sees. Documented so a CI job can branch on them.
EXIT_OK = 0
EXIT_INFRASTRUCTURE = 1
"""No Ollama server, no models installed."""
EXIT_BAD_ARGUMENT = 2
"""An as_of with no offset, an unreadable corpus, an unknown policy."""
EXIT_THRESHOLD = 3
"""The run completed and --fail-on said this result is not acceptable."""

#: Backends the CLI can build. The library takes any ChatBackend.
BACKENDS = ("ollama", "openai-compat")


def _resolve(name: str, host: str | None, base_url: str | None) -> ChatBackend:
    """Build the requested backend.

    Passing --base-url selects openai-compat on its own, because naming an
    OpenAI API root and then getting an Ollama client is never what anyone
    meant.
    """
    if base_url and name == "ollama":
        name = "openai-compat"
    if name == "ollama":
        return OllamaClient(host=host)
    if name == "openai-compat":
        return OpenAICompatClient(base_url or host)
    raise typer.BadParameter(f"--backend must be one of {', '.join(BACKENDS)}, got {name!r}")


#: --fail-on values for `check`, mapped to which severities trip them.
_CHECK_SEVERITIES = {
    "never": lambda severity: False,
    "error": lambda severity: severity == "error",
    "warning": lambda severity: severity in ("error", "warning"),
}

app = typer.Typer(
    name="chronoguard",
    help=(
        "Run LLM agents as if it were a past date, and measure how well the "
        "blinding holds.\n\n"
        "ChronoGuard filters tool results by publication date (tool leakage) "
        "and probes the model for facts it already knows (parametric leakage).\n\n"
        "Exit codes: 0 fine, 1 no Ollama, 2 bad argument, 3 --fail-on threshold hit."
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
def check(
    corpus: Annotated[str, typer.Argument(help="JSON or JSONL file holding your records.")],
    as_of: Annotated[
        str, typer.Option("--as-of", help="The instant to simulate. Needs a timezone offset.")
    ] = FIXTURE_AS_OF,
    published_key: Annotated[
        Optional[str], typer.Option(help="Field holding the publication timestamp.")
    ] = None,
    updated_key: Annotated[
        Optional[str], typer.Option(help="Field holding the last-revision timestamp.")
    ] = None,
    source_key: Annotated[Optional[str], typer.Option(help="Field holding a stable id.")] = None,
    content_key: Annotated[Optional[str], typer.Option(help="Field holding the text.")] = None,
    results_key: Annotated[
        Optional[str], typer.Option(help="Key to dig into when the file is a wrapper object.")
    ] = None,
    policy: Annotated[str, typer.Option(help="strict or warn.")] = "strict",
    revisions: Annotated[str, typer.Option(help="reject or ignore.")] = "reject",
    allow_undated: Annotated[
        bool, typer.Option("--allow-undated", help="Admit records with no usable date.")
    ] = False,
    fail_on: Annotated[
        str,
        typer.Option(
            help="Exit 3 when a finding reaches this severity: error, warning, or never."
        ),
    ] = "never",
    as_json: Annotated[bool, typer.Option("--json", help="Emit the report as JSON.")] = False,
) -> None:
    """Show what the guard would do to a corpus, without running a model.

    No Ollama, no network. Point it at your data before you spend a model call,
    because the two states that waste a run (everything dropped, nothing
    dropped) both look fine from a distance.
    """
    if fail_on not in _CHECK_SEVERITIES:
        typer.secho(
            f"--fail-on must be one of {', '.join(_CHECK_SEVERITIES)}, got {fail_on!r}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=EXIT_BAD_ARGUMENT)

    try:
        guard = TemporalGuard(
            as_of,
            policy=GuardPolicy(policy),
            allow_undated=allow_undated,
            revisions=revisions,
        )
        rows = load_rows(corpus, results_key=results_key)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=EXIT_BAD_ARGUMENT) from exc

    adapter = MappingAdapter(
        results_key=None,
        **{
            key: value
            for key, value in (
                ("content_key", content_key),
                ("source_key", source_key),
                ("published_key", published_key),
                ("updated_key", updated_key),
            )
            if value
        },
    )

    report = inspect_corpus(rows, as_of, adapter=adapter, guard=guard, source=corpus)

    if as_json:
        typer.echo(json.dumps(report.summary(), indent=2))
    else:
        typer.echo(report.render())
        if report.errors:
            typer.echo("")
            typer.secho("This corpus would not produce a meaningful run.", fg=typer.colors.RED)

    breached = [f for f in report.findings if _CHECK_SEVERITIES[fail_on](f.severity)]
    if breached:
        typer.secho(
            f"\n--fail-on {fail_on}: {len(breached)} finding(s) at or above that severity.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=EXIT_THRESHOLD)


@app.command()
def models(
    host: Annotated[Optional[str], typer.Option(help="Ollama host. Defaults to OLLAMA_HOST.")] = None,
    backend: Annotated[
        str, typer.Option(help="ollama or openai-compat.")
    ] = "ollama",
    base_url: Annotated[
        Optional[str],
        typer.Option(help="Root of an OpenAI-compatible API. Implies --backend openai-compat."),
    ] = None,
) -> None:
    """List the models a backend is offering, and whether they can call tools."""
    try:
        client = _resolve(backend, host, base_url)
        installed = client.list_models()
    except BackendUnavailable as exc:
        raise _die(exc) from exc

    if not installed:
        typer.echo(f"No models offered by {client.host}. Try `ollama pull gemma3:4b`.")
        raise typer.Exit(code=EXIT_INFRASTRUCTURE)

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
    backend: Annotated[
        str, typer.Option(help="ollama or openai-compat.")
    ] = "ollama",
    base_url: Annotated[
        Optional[str],
        typer.Option(help="Root of an OpenAI-compatible API. Implies --backend openai-compat."),
    ] = None,
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
        raise typer.Exit(code=EXIT_BAD_ARGUMENT) from exc

    tools = build_fixture_toolset(guard, AuditLog())
    config = AgentConfig(
        task=task, as_of=guard.as_of, model=model, mode=mode, max_steps=max_steps
    )

    try:
        result = run_agent(config, tools, client=_resolve(backend, host, base_url))
    except BackendUnavailable as exc:
        raise _die(exc) from exc

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
    backend: Annotated[
        str, typer.Option(help="ollama or openai-compat.")
    ] = "ollama",
    base_url: Annotated[
        Optional[str],
        typer.Option(help="Root of an OpenAI-compatible API. Implies --backend openai-compat."),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit the report as JSON.")] = False,
) -> None:
    """Measure what a model already knows about the future, with no tools at all.

    This is the half filtering cannot fix. A correct answer here came from the
    weights, not from anything you handed it.
    """
    try:
        client = _resolve(backend, host, base_url)
        chosen = model or client.pick_model()
        report = LeakageProbe(
            client,
            cases=load_probe_cases(cases) if cases else None,
            cutoffs=load_model_cutoffs(cutoffs) if cutoffs else None,
            judge_model=judge,
        ).run(chosen, as_of, max_future_cases=max_future, max_control_cases=max_control)
    except BackendUnavailable as exc:
        raise _die(exc) from exc
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=EXIT_BAD_ARGUMENT) from exc

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


@app.command()
def report(
    task: Annotated[str, typer.Argument(help="What to ask the agent.")],
    as_of: Annotated[
        str, typer.Option("--as-of", help="The instant to simulate. Needs a timezone offset.")
    ] = FIXTURE_AS_OF,
    model: Annotated[
        Optional[str], typer.Option(help="Ollama model. Discovered at runtime if unset.")
    ] = None,
    judge: Annotated[
        Optional[str], typer.Option(help="Model for claim classification. Reuses --model if unset.")
    ] = None,
    mode: Annotated[str, typer.Option(help="auto, native, or react.")] = "auto",
    policy: Annotated[
        str, typer.Option(help="strict drops post-as-of evidence, warn keeps and flags it.")
    ] = "strict",
    max_steps: Annotated[int, typer.Option(help="Cap on agent loop iterations.")] = 6,
    max_claims: Annotated[int, typer.Option(help="Cap on claims classified.")] = 8,
    max_future: Annotated[Optional[int], typer.Option(help="Cap on probe questions.")] = None,
    max_control: Annotated[Optional[int], typer.Option(help="Cap on control questions.")] = None,
    skip_probe: Annotated[
        bool, typer.Option("--skip-probe", help="Skip the parametric leakage probe.")
    ] = False,
    skip_claims: Annotated[
        bool, typer.Option("--skip-claims", help="Skip claim classification.")
    ] = False,
    json_out: Annotated[
        Optional[str], typer.Option("--json-out", help="Also write the JSON summary to this path.")
    ] = None,
    fail_on: Annotated[
        str,
        typer.Option(
            help=(
                "Exit 3 when the headline risk reaches this level: "
                "low, unknown, elevated, high, or never."
            )
        ),
    ] = "never",
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the JSON summary instead of the text report.")
    ] = False,
    host: Annotated[Optional[str], typer.Option(help="Ollama host.")] = None,
    backend: Annotated[
        str, typer.Option(help="ollama or openai-compat.")
    ] = "ollama",
    base_url: Annotated[
        Optional[str],
        typer.Option(help="Root of an OpenAI-compatible API. Implies --backend openai-compat."),
    ] = None,
) -> None:
    """Run a full scenario: agent run, leakage probe, claim classification.

    Prints a human-readable report and, with --json-out, writes the machine
    summary alongside it.
    """
    if fail_on != "never" and fail_on not in RISK_ORDER:
        typer.secho(
            f"--fail-on must be never or one of {', '.join(RISK_ORDER)}, got {fail_on!r}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=EXIT_BAD_ARGUMENT)

    try:
        config = ScenarioConfig(
            task=task,
            as_of=as_of,
            model=model,
            judge_model=judge,
            mode=mode,
            policy=GuardPolicy(policy),
            max_steps=max_steps,
            probe=not skip_probe,
            classify=not skip_claims,
            max_claims=max_claims,
            max_future_cases=max_future,
            max_control_cases=max_control,
        )
    except (ValueError, ValidationError) as exc:
        typer.secho(_first_error(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=EXIT_BAD_ARGUMENT) from exc

    try:
        result = run_scenario(config, client=_resolve(backend, host, base_url))
    except BackendUnavailable as exc:
        raise _die(exc) from exc

    summary = result.summary()
    if json_out:
        Path(json_out).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    if as_json:
        typer.echo(json.dumps(summary, indent=2))
    else:
        typer.echo(result.render())
        if json_out:
            typer.echo(f"\nJSON summary written to {json_out}")

    if fail_on != "never" and risk_at_least(result.headline_risk, fail_on):
        typer.secho(
            f"\n--fail-on {fail_on}: this run came back {result.headline_risk}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=EXIT_THRESHOLD)


def _die(exc: BackendUnavailable) -> typer.Exit:
    """Report a backend failure with advice that matches what actually went wrong.

    A timeout means the server is up and the model is slow, so telling anyone to
    start a server is wrong. Everything else gets start-up advice, specific to
    Ollama when that is what failed and generic when it is some other backend.
    """
    typer.secho(str(exc), fg=typer.colors.RED, err=True)
    if not isinstance(exc, BackendTimeout):
        if isinstance(exc, OllamaUnavailable):
            typer.echo("Start one with `ollama serve`.", err=True)
        else:
            typer.echo("Check the server is running and --base-url is right.", err=True)
    return typer.Exit(code=EXIT_INFRASTRUCTURE)


def _first_error(exc: Exception) -> str:
    """Pydantic wraps our as_of message in a validation error, dig it back out."""
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        if errors:
            return str(errors[0].get("msg", exc))
    return str(exc)


def main() -> None:
    """Console-script entrypoint."""
    app()


if __name__ == "__main__":  # pragma: no cover - exercised via `python -m chronoguard`
    main()
