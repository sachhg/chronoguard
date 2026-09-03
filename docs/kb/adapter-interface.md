---
id: adapter-interface
title: Adapters map a tool's own shape onto evidence records
type: contract
description: The three adapter kinds and when each applies.
tags: [interception, api]
links: [evidence-record-contract, guarded-tool-contract, add-a-guarded-tool]
source: src/chronoguard/interception.py
---
An adapter is anything with `to_records(raw) -> list[EvidenceRecord]`. This is
what lets one filter serve arbitrary tools: search APIs, vector stores and SQL
rows all name their fields differently, so each tool brings a small mapping and
the filtering stays in one place.

Three ways to supply one:

- `MappingAdapter(...)` for dict-shaped output. Handles multiple content fields
  joined in order, fallback id and date fields, a `results_key` for wrapper
  dicts, and puts every leftover field in `metadata`.
- `RecordAdapter()` (the default) for tools already returning records.
- Any plain `raw -> list[EvidenceRecord]` callable, wrapped automatically.

`resolve_adapter` accepts all three plus None. If you write a new adapter, give
it `to_records` and it will be accepted by the protocol check.
