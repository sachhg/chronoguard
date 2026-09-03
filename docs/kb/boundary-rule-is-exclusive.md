---
id: boundary-rule-is-exclusive
title: The as-of boundary is exclusive
type: decision
description: published_at == as_of is rejected. Why, and how to get inclusive behaviour instead.
tags: [guard, dates]
links: [undated-records-rejected-by-default, probe-case-boundary-matches-the-guard]
source: src/chronoguard/guard.py
---
A record is allowed when `published_at < as_of`, strictly. Published at exactly
`as_of` is rejected.

The reason is day-precision corpora. Loads of sources store dates as bare days,
so "published 2023-06-01" becomes `2023-06-01T00:00:00Z`. With an inclusive
boundary and as_of at midnight on the 1st, every document published anywhere in
that day sails through, including ones written hours after the moment you are
simulating.

Losing a record published on the exact microsecond of the cutoff costs nothing.
Admitting a day of hindsight costs the experiment.

Want a whole day? Name the next midnight: `2023-06-02T00:00:00Z`. There is no
config knob for this and there should not be one; the rule is documented and
tested instead.
