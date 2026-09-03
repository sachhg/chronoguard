---
id: evidence-record-contract
title: EvidenceRecord is the only shape the guard understands
type: contract
description: Fields, the two constructors, and when to use which.
tags: [evidence, api]
links: [adapter-interface, naive-datetimes-are-not-instants, undated-records-rejected-by-default]
source: src/chronoguard/evidence.py
---
Every tool output becomes `EvidenceRecord` before anything else looks at it, so
filtering logic only has to understand one shape.

Fields: `content`, `source_id`, `published_at` (aware or None), `retrieved_at`,
`metadata` dict, `published_at_raw`.

Two constructors, and the choice matters:

- `EvidenceRecord(...)` is strict. A naive or unparseable `published_at` raises.
  Use it when you control the data and want bad dates to fail loudly.
- `EvidenceRecord.from_source(...)` is lenient and never raises on a bad
  timestamp. It leaves `published_at` as None and stashes the original in
  `published_at_raw` so the guard can reject the record and still say what it
  choked on. Use it for real tool output.

`retrieved_at` is never filtered on. You are running the backtest today, so
everything was retrieved after as_of. It is there for audit trails.
