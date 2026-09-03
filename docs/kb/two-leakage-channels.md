---
id: two-leakage-channels
title: Two leakage channels, two different fixes
type: concept
description: The central model: tool leakage is containable, parametric leakage is only measurable.
tags: [core, design]
links: [containment-vs-measurement, prompt-is-not-containment, verdict-never-reports-unearned-clean]
source: DESIGN.md
---
Asking a model to reason as of a past date fails in two independent ways. They
look identical from outside and need different fixes. Do not conflate them.

**Tool leakage.** A tool returns a document published after the as-of date. This
is a data plumbing problem. Intercept the tool call, drop anything stamped later
than as_of, done. Solvable.

**Parametric leakage.** The model's weights already encode post-as-of facts, so
it answers correctly with zero tool access. Filtering the context does nothing.
You cannot subtract knowledge from weights.

The only honest responses to the second are picking a model whose training
predates your as-of date, and measuring the leakage instead of pretending it is
gone. Anything that filters tool output and then calls itself a sandbox has
locked one door and left the other open.

Everything in this repo sorts into one of these two jobs. When adding a feature,
know which one it serves.
