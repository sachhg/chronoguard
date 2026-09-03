---
id: verdict-never-reports-unearned-clean
title: The verdict never reports a clean bill it hasn't earned
type: decision
description: Two rules stop a spotless-looking run from reading as low risk.
tags: [report, core]
links: [two-leakage-channels, probe-has-a-control-group, scenario-summary-schema]
source: src/chronoguard/report.py
---
`headline_risk` takes the worst signal across all three measurements and carries
its reasons with it. Two rules exist specifically to stop it overclaiming:

- A run on a model whose training cutoff postdates as_of is `elevated`, never
  `low`, even when the filter held and every claim was grounded. The model
  demonstrably read past the moment being simulated.
- A run whose probe controls failed is `unknown`, never `low`. See
  [[probe-has-a-control-group]].

Skipping either measurement also drops the verdict to `unknown` rather than
letting absence of evidence read as evidence of absence.

If you add a signal, decide what it does to the headline and whether it can ever
push the verdict *down*. Nothing currently can, and that is deliberate.
