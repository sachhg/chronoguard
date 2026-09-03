---
id: module-map
title: Where everything lives
type: map
description: One line per module and data file, in dependency order.
tags: [architecture, orientation]
links: [containment-vs-measurement, repo-conventions, circular-import-via-package-root, typer-collapses-single-command-apps]
source: src/chronoguard/
---
Layer 1, containment:

- `_version.py` version only, so nothing imports the package root
- `evidence.py` `EvidenceRecord`, `parse_timestamp`
- `guard.py` `TemporalGuard`, `Verdict`, `Judgement`, `FilterResult`
- `interception.py` `GuardedTool`, adapters, `AuditLog`
- `fixtures/` fake web search and document store, `fixtures/data/*.json`

Layer 2, measurement:

- `ollama.py` HTTP client, runtime model discovery, capability detection
- `agent.py` the loop, native and react modes, `format_evidence`, `tool_schema`
- `probe.py` parametric leakage probe, scoring, cutoff risk
- `claims.py` claim decomposition and classification
- `report.py` `run_scenario`, the headline verdict, both output shapes
- `data/probe_cases.json`, `data/model_cutoffs.json` user-editable

Entry points:

- `cli.py` `version`, `models`, `run`, `probe`, `report`
- `__init__.py` re-exports the public surface; internals never import from it
