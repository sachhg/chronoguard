---
id: agent-never-told-what-was-filtered
title: The agent is never told what was filtered
type: decision
description: Filter counts go to the audit log, never into the model's context.
tags: [agent, prompting]
links: [prompt-is-not-containment, audit-log-is-the-reporting-side]
source: src/chronoguard/agent.py
---
Tool observations show the surviving records and nothing else. `format_evidence`
returns `(no results)` for an empty result, not "4 documents were withheld".

Telling a model that documents were withheld for postdating its cutoff is itself
a signal: it says the future exists, that it is interesting, and roughly how
much of it there is. A model that then reaches into its weights to fill the gap
is doing exactly what the run is trying to prevent.

The counts are still collected. They live on the `AuditLog` and go to the
report. Never into a prompt.
