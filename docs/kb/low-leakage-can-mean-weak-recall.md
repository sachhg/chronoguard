---
id: low-leakage-can-mean-weak-recall
title: A low leakage score can mean weak recall, not an early cutoff
type: pitfall
description: Small models fail recent facts for capacity reasons, which reads as blinding unless you check the cutoff signal too.
tags: [probe, scoring, interpretation]
links: [probe-has-a-control-group, cutoffs-are-a-prior-not-evidence, verdict-never-reports-unearned-clean]
source: src/chronoguard/probe.py
---

qwen3:4b scores 1/8 leakage at an as-of of 2023-06-01 with 6/6 on controls. That
looks like a well blinded model. It is not: the cutoff table puts its training
data well past that date, and its wrong answers give it away. Asked about the
2024 Nobel Prize in Physics it names the 2023 laureates, and asked about the
2024 Turing Award it names the 2018 winners.

So it is not that qwen3 has never seen 2024. It is that a 4B model recalls
specific award winners badly.

The control group catches a model that cannot answer anything, which is a
different failure. It does not catch a model that handles easy old facts and
fluffs recent ones, because controls skew older by construction.

What this means when reading a report:

- A low leakage score is evidence about *these* facts, not proof of an early
  cutoff. Two signals disagreeing (low leakage, high cutoff risk) is normal on
  small models and the report keeps both.
- This is why cutoff risk alone raises the headline to elevated. See
  [[verdict-never-reports-unearned-clean]].
- Bigger models make the probe more informative, because a miss is more likely
  to be genuine ignorance than a recall failure.
