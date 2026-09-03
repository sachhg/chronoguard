"""Worked example: reasoning about a policy decision as of a past date.

Run it:

    ollama serve &
    python examples/policy_change/run.py
    python examples/policy_change/run.py --model gemma3:4b --json-out out.json

What this shows that the packaged fixtures don't: wiring up *your own* corpus and
*your own* tool shape. The archive in archive.py returns a status wrapper around
`items` keyed on `ref` and `published`, which ChronoGuard has never seen. All it
takes is a MappingAdapter describing the shape.

The scenario: Ashcombe Borough Council is considering a city centre access levy.
As of 2024-03-01 the committee has deferred its decision, a draft has floated
4.50 pounds a day, and nothing is approved. The council later approves it at 7.50
pounds from 1 October 2024, but that is the future and the agent must not know it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from archive import AS_OF, CANARIES, CouncilArchive  # noqa: E402

from chronoguard import AuditLog, GuardedTool, TemporalGuard  # noqa: E402
from chronoguard.ollama import OllamaClient, OllamaUnavailable  # noqa: E402
from chronoguard.report import ScenarioConfig, run_scenario  # noqa: E402

TASK = (
    "Has Ashcombe council approved the city centre access levy? "
    "If so, at what daily charge and from what date? "
    "Search the council archive before answering, and cite the references you used."
)


def build_tools(guard: TemporalGuard, audit: AuditLog) -> dict[str, GuardedTool]:
    """Wrap the archive so the agent can only ever see pre-as-of minutes."""
    archive = CouncilArchive()
    return {
        "council_archive": GuardedTool(
            archive.search,
            guard,
            archive.adapter,
            name="council_archive",
            audit=audit,
        )
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=None, help="Ollama model, discovered if unset")
    parser.add_argument("--host", default=None, help="Ollama host")
    parser.add_argument("--json-out", default=None, help="Write the JSON summary here")
    parser.add_argument("--skip-probe", action="store_true")
    args = parser.parse_args()

    guard = TemporalGuard(AS_OF)
    audit = AuditLog()
    tools = build_tools(guard, audit)

    # Sanity check before spending a model call: the guard has to be doing work,
    # or the run proves nothing. See docs/kb/test-that-the-raw-tool-leaks-first.md.
    raw = str(CouncilArchive().search("access levy approved charge", limit=99))
    leaks_unguarded = [c for c in CANARIES if c in raw]
    print(f"unguarded archive leaks: {leaks_unguarded}")
    if not leaks_unguarded:
        print("the corpus stopped leaking, this run would prove nothing")
        return 1

    try:
        report = run_scenario(
            ScenarioConfig(
                task=TASK,
                as_of=AS_OF,
                model=args.model,
                probe=not args.skip_probe,
                max_steps=5,
                max_claims=6,
                max_future_cases=4,
                max_control_cases=3,
            ),
            tools,
            client=OllamaClient(host=args.host),
        )
    except OllamaUnavailable as exc:
        print(f"\n{exc}\nStart one with `ollama serve`.", file=sys.stderr)
        return 1

    print("\n" + report.render())

    leaked = [c for c in CANARIES if c in report.agent.final_answer or c in report.agent.evidence_text]
    print("\n" + "=" * 70)
    print(f"post-as-of strings reaching the agent: {leaked or 'none'}")
    print(f"headline verdict: {report.headline_risk}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report.summary(), indent=2) + "\n", encoding="utf-8")
        print(f"JSON summary written to {args.json_out}")

    return 0 if not leaked else 2


if __name__ == "__main__":
    raise SystemExit(main())
