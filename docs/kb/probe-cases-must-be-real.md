---
id: probe-cases-must-be-real
title: Probe cases must be real world facts
type: decision
description: Unlike tool fixtures, probe cases test actual training data so they cannot be invented.
tags: [probe, fixtures]
links: [fictional-fixture-scenario, add-a-probe-case]
source: src/chronoguard/data/probe_cases.json
---
Tool fixtures are fictional. Probe cases must not be.

The probe measures what is in a model's weights, so it needs facts that were
actually published and actually trained on. An invented fact tests nothing: the
model will fail it whatever its cutoff.

Requirements for a good case:

- A specific, checkable answer: a name, a number, a date.
- A defensible `knowable_from` instant, the moment it became public.
- Distinctive answer strings. Single letters and very common words match by
  accident. See [[claim-and-answer-matching]].
- Non-sports, non-vertical. The corpus stays generic.

Facts spanning a wide date range are the most useful, since one corpus then
serves many as-of dates.
