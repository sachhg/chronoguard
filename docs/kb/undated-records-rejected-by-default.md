---
id: undated-records-rejected-by-default
title: Undated records are rejected by default
type: decision
description: No timestamp, junk timestamp or naive timestamp means rejected unless allow_undated is set.
tags: [guard, dates, defaults]
links: [boundary-rule-is-exclusive, naive-datetimes-are-not-instants, evidence-record-contract]
source: src/chronoguard/guard.py
---
A record with no usable publication timestamp cannot be shown to predate the
cutoff, so it is dropped. That covers three cases with two verdicts:

- `Verdict.UNDATED`: no timestamp at all
- `Verdict.UNPARSEABLE`: a timestamp was supplied but is junk, or is naive

They are separate verdicts so a report can distinguish "no date" from "date we
choked on", which are different problems in your corpus.

`allow_undated=True` opts out of both at once. It is off by default because the
dangerous failure mode is silently admitting undated content, and undated
fixture documents deliberately carry post-as-of facts to prove the point.

Every ambiguous case in this repo defaults to the safe answer with an explicit
opt-out. Keep it that way.
