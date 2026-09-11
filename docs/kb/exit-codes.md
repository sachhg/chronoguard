---
id: exit-codes
title: Exit codes, and why --fail-on defaults to never
type: contract
description: What the shell sees from each command, and how a CI job gates on a run.
tags: [cli, ci]
links: [check-is-the-no-model-path, scenario-summary-schema, verdict-never-reports-unearned-clean]
source: src/chronoguard/cli.py
---
| Code | Constant | Means |
| --- | --- | --- |
| 0 | `EXIT_OK` | The command ran and nothing tripped a threshold. |
| 1 | `EXIT_INFRASTRUCTURE` | No Ollama server, or none of the models needed. |
| 2 | `EXIT_BAD_ARGUMENT` | An as-of with no offset, an unreadable corpus, an unknown policy. |
| 3 | `EXIT_THRESHOLD` | The command completed and `--fail-on` says the result is unacceptable. |

2 and 3 are deliberately different. A misconfigured run and a run that came back
badly are not the same event, and a CI job usually wants to shout about the
first and gate on the second.

## --fail-on

`chronoguard report --fail-on low|unknown|elevated|high|never` exits 3 when the
headline risk reaches that level or worse, comparing against `RISK_ORDER` in
`report.py`. `unknown` sits above `low` in that order, so gating on `low` also
catches a run that could not measure something, which is the point of
[[verdict-never-reports-unearned-clean]].

`chronoguard check --fail-on error|warning|never` exits 3 when any finding
reaches that severity. An unmapped revision field is a warning rather than an
error, so `warning` is the stricter setting.

Both default to `never`. Adding an exit code to a command that previously always
returned 0 would break every script already calling it, and a gate is a decision
about your project rather than something a tool should assume.

Output comes first in both cases. The text report or JSON summary is printed, and
`--json-out` is written, before the threshold is checked, because the run that
failed CI is exactly the one whose output someone needs to read.
