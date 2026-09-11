---
id: async-tools-are-awaited-then-filtered
title: Async tools are awaited first, then filtered
type: contract
description: Wrapping an async def returns an awaitable; filtering happens inside it, after the await.
tags: [interception, api, async]
links: [guarded-tool-contract, add-a-guarded-tool, audit-log-is-the-reporting-side]
source: src/chronoguard/interception.py
---
`GuardedTool` detects a coroutine function and returns an awaitable from
`__call__`. The call site awaits the wrapped tool exactly as it would have
awaited the unwrapped one.

    guarded = guard_tool(async_search, guard, adapter)
    kept = await guarded("meridian pricing")

Filtering happens inside that coroutine, after the await, because there is
nothing to filter until the tool has produced results. Two consequences worth
knowing:

- **Nothing is audited until the coroutine is awaited.** Calling a guarded async
  tool and dropping the result on the floor logs nothing and filters nothing.
- **Concurrent calls all land in the shared log.** `asyncio.gather` over four
  calls gives four entries. The log is appended to from inside each coroutine,
  and the event loop serialises those appends.

Everything after the call is shared with the sync path: the same adapter, guard,
audit entry and `render` hook, in one `_finish`. The two agree because they are
the same code, not because someone keeps them in step.

Detection covers a class whose `__call__` is async, which
`inspect.iscoroutinefunction` misses when handed the instance. Async generators
are not supported; a tool has to return its results, not yield them.

Everything in [[guarded-tool-contract]] still holds, including signature
preservation, so native tool-calling schemas still build correctly off an async
tool.
