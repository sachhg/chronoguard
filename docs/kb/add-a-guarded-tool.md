---
id: add-a-guarded-tool
title: How to guard your own tool
type: procedure
description: Wrap any callable returning evidence so an agent only sees pre-as-of results.
tags: [interception, howto]
links: [adapter-interface, guarded-tool-contract, audit-log-is-the-reporting-side]
source: src/chronoguard/interception.py
---
1. Build one guard and one audit log for the run.
2. Describe your tool's output shape with an adapter.
3. Wrap, then hand the wrapper to the agent.

```python
from chronoguard import AuditLog, MappingAdapter, TemporalGuard, guarded_tool

guard = TemporalGuard("2023-06-01T00:00:00Z")
audit = AuditLog()

@guarded_tool(guard, MappingAdapter(
    content_key=("title", "snippet"),
    source_key="url",
    published_key="date",
), audit=audit)
def web_search(query: str, limit: int = 5) -> list[dict]:
    """Search the web."""
    return my_api(query, limit)
```

`guard_tool(fn, guard, adapter, ...)` is the non-decorator form.

Gotchas: keep a real docstring and type hints, since native tool schemas are
built from them. Pass the same `audit` to every tool. Build the tools with the
same `as_of` as the run, or `AgentRunner` refuses to start.
