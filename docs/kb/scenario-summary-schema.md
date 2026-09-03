---
id: scenario-summary-schema
title: The JSON summary shape is stable and diffable
type: contract
description: Top-level keys of ScenarioReport.summary() and what consumers can rely on.
tags: [report, api]
links: [verdict-never-reports-unearned-clean, audit-log-is-the-reporting-side]
source: src/chronoguard/report.py
---
`ScenarioReport.summary()` returns a dict meant to be written to a file and
diffed across runs. Top-level keys:

`chronoguard_version`, `generated_at`, `as_of`, `task`, `model`, `mode`,
`policy`, `headline`, `tool_leakage`, `parametric_leakage`, `claims`, `answer`,
`evidence`.

Three guarantees consumers rely on:

- `tool_leakage.records_filtered`, `parametric_leakage.leakage_score` and
  `claims.flagged` are the three headline numbers and are always present in
  shape.
- A skipped stage is `null`, never a missing key.
- `headline.reasons` is a list of human-readable strings explaining the verdict.

Add keys freely. Do not rename or remove existing ones without a version bump,
since the whole point is diffing runs over time.
