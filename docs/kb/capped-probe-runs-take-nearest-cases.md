---
id: capped-probe-runs-take-nearest-cases
title: Capped probe runs take the cases nearest as_of
type: decision
description: max_future_cases keeps cases closest to the as-of date, because those are where leakage shows.
tags: [probe, scoring, pitfall]
links: [probe-has-a-control-group, falsy-zero-limits]
source: src/chronoguard/probe.py
---
`_select` orders future cases nearest-to-as_of first and controls
most-recent-first.

This was a real bug once. Capping originally kept the *newest* future cases,
which sit past every plausible training cutoff, so a capped run reported 0/4
leakage where the full run found 38%. Reassuring and wrong.

Cases just after as_of are the ones likely to be inside the model's training
window, so they are where leakage actually shows up. Cases from years later tell
you about the model's cutoff, not about your as-of date.

If you change the ordering, check a capped run against a full run on the same
model before believing the number.
