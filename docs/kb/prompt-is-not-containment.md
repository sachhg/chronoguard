---
id: prompt-is-not-containment
title: The system prompt is not the containment mechanism
type: decision
description: The as-of line in the prompt keeps the model on task; the guard is what actually blocks the future.
tags: [agent, prompting, core]
links: [two-leakage-channels, agent-never-told-what-was-filtered]
source: src/chronoguard/agent.py
---
The agent's system prompt says "the current date is X and you know nothing after
it". That is there to keep the model on task. It is not what stops the future
getting in.

Prompts are a request. The filter is a wall. If someone proposes strengthening
the prompt as a way to improve blinding, that is the wrong lever: it improves
compliance, not containment, and a model that ignores the instruction is
unaffected.

The same distinction is why the probe deliberately does *not* use an as-of
framing. See [[probe-does-not-ask-model-to-pretend]].
