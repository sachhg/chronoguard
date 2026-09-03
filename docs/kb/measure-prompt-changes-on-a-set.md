---
id: measure-prompt-changes-on-a-set
title: Judge prompt changes need a set, not an anecdote
type: pitfall
description: Two prompt "fixes" driven by a single misclassified claim both made the judge worse overall.
tags: [prompting, claims, testing]
links: [tune-a-judge-prompt, ordered-procedure-beats-a-menu, judge-asked-observable-question]
source: src/chronoguard/claims.py
---

The example run once flagged "There is no information available regarding a
daily charge" as a leak. A claim that the evidence *lacks* something is the
opposite of leakage, so this looked worth fixing.

Two attempts, both driven by that one claim:

- Adding absence wording to the BENIGN signal list. Fixtures went 6/6 to 5/6 and
  the claim was still wrong. Extra instruction text diluted the rule.
- Rewriting step 1 as a binary "does this assert a fact" gate. Fixed the absence
  claims, broke the hedge claim, and on a wider set produced two false leaks.

Measured on eleven claims covering hedges, absences, negations that *are*
grounded, and real leaks, the original prompt won on every metric: 10/11
correct, zero false leaks, zero missed leaks. Both "improvements" were worse.

So: never tune a judge prompt against one observed mistake. Build a set that
includes the failure mode you are chasing plus the cases that already work,
score before and after, and weight false leaks heavily since those inflate the
headline verdict and fill the flagged list with noise.

The judge is imperfect at 4B and that is fine. It is a measurement instrument
with known error, not an oracle.
