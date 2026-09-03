---
id: probe-does-not-ask-model-to-pretend
title: The probe never asks the model to pretend
type: decision
description: Probe questions are asked straight, because pretending measures compliance not knowledge.
tags: [probe, prompting]
links: [prompt-is-not-containment, probe-has-a-control-group]
source: src/chronoguard/probe.py
---
The probe asks its questions with no as-of framing at all. Its system prompt is
"answer as directly as you can, say I DO NOT KNOW if you don't".

If you told the model to answer as of a past date, a wrong answer would be
ambiguous: it might not know, or it might be obeying. The probe is measuring
what is in the weights, so it wants the model trying its hardest.

This is the opposite of the agent prompt on purpose. The agent is being asked to
do a task under a constraint; the probe is being interrogated.
