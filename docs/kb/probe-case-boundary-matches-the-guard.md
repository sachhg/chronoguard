---
id: probe-case-boundary-matches-the-guard
title: Probe cases use the same exclusive boundary as the guard
type: contract
description: knowable_from >= as_of makes a case a probe, matching published_at >= as_of being a violation.
tags: [probe, dates]
links: [boundary-rule-is-exclusive, probe-has-a-control-group]
source: src/chronoguard/probe.py
---
`ProbeCase.kind_for(as_of)` returns `future` when `knowable_from >= as_of`, and
`control` otherwise.

That is deliberately the same rule as the guard's: a document published at
exactly as_of is a violation, and a fact knowable at exactly as_of is a probe
case rather than a control. One boundary convention across the whole project
means you never have to remember which side a component sits on.

If you change one, change both, and update the tests that pin the boundary
instant in each.
