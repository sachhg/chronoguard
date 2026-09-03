---
id: probe-has-a-control-group
title: The probe scores controls, not just future facts
type: decision
description: Zero leakage from a model that can't answer anything is not evidence of blinding.
tags: [probe, scoring]
links: [probe-does-not-ask-model-to-pretend, verdict-never-reports-unearned-clean, low-leakage-can-mean-weak-recall]
source: src/chronoguard/probe.py
---
Cases whose answers were already knowable at the as-of date run as a control
group alongside the probe cases.

Without them, two very different situations produce the same number. A model
that is genuinely blinded scores zero on future facts. A model that is simply
bad at questions also scores zero. You cannot tell them apart from the leakage
score alone.

So `risk_level` returns `inconclusive`, not `low`, when the leakage score is
zero and the control score is under 50%. The report says so in words too.

One corpus serves both roles: `knowable_from >= as_of` makes a case a probe,
earlier makes it a control. Nothing extra to maintain.
