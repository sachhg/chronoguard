---
id: guarded-tool-contract
title: GuardedTool wraps a callable and keeps its signature
type: contract
description: What wrapping changes, what it preserves, and what not to wrap.
tags: [interception, api, agent]
links: [adapter-interface, audit-log-is-the-reporting-side, add-a-guarded-tool]
source: src/chronoguard/interception.py
---
`GuardedTool` runs the real tool, adapts its output, filters it, logs the
decision, and returns the survivors. Call it exactly like the function it wraps.

What it preserves, via `functools.update_wrapper`: `__name__`, `__doc__` and the
signature. This is not cosmetic. `tool_schema` builds native tool-calling
definitions by introspecting the wrapper, so losing the signature would produce
`(*args, **kwargs)` schemas and break native mode.

What it returns: `list[EvidenceRecord]` by default, or whatever a `render`
callable returns. `render` receives the whole `FilterResult`, so it can see what
was dropped. What the agent sees is only what render returns.

Errors from the wrapped tool propagate untouched and log nothing. A call that
never returned evidence did not filter any.

Only wrap tools that return evidence. A calculator has nothing to filter and
wrapping it hands the agent an empty list.
