---
id: scenario-summary-schema
title: The JSON summary shape is stable and diffable
type: contract
description: Top-level keys of ScenarioReport.summary() and what consumers can rely on.
tags: [report, api]
links: [verdict-never-reports-unearned-clean, audit-log-is-the-reporting-side, exit-codes]
source: src/chronoguard/report.py
---
`ScenarioReport.summary()` returns a dict meant to be written to a file and
diffed across runs. Top-level keys:

`schema_version`, `chronoguard_version`, `generated_at`, `as_of`, `task`,
`model`, `mode`, `policy`, `headline`, `tool_leakage`, `parametric_leakage`,
`claims`, `answer`, `evidence`.

Three guarantees consumers rely on:

- `tool_leakage.records_filtered`, `parametric_leakage.leakage_score` and
  `claims.flagged` are the three headline numbers and are always present in
  shape.
- A skipped stage is `null`, never a missing key.
- `headline.reasons` is a list of human-readable strings explaining the verdict.

Add keys freely. Do not rename or remove existing ones without a version bump,
since the whole point is diffing runs over time.

`schema_version` is what to key on, and it is not the package version: a patch
release must not look like a schema change. It is bumped when a key is removed
or changes meaning, never when one is added, so consumers should ignore keys
they do not recognise. `CorpusReport.summary()` from
[[check-is-the-no-model-path]] carries its own, numbered independently.
