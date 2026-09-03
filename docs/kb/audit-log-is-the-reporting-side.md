---
id: audit-log-is-the-reporting-side
title: Counts live on the AuditLog, not in the return value
type: contract
description: Share one log across an agent's tools; that is where filtered counts come from.
tags: [interception, report]
links: [guarded-tool-contract, agent-never-told-what-was-filtered, scenario-summary-schema]
source: src/chronoguard/interception.py
---
The agent-facing return value carries only surviving records. Everything about
what was dropped lives on `AuditLog`.

Share one log across every tool an agent gets, which is what
`build_fixture_toolset` does, and the report can then say `filtered_count`,
`counts` by verdict, and `by_tool()` naming the leakiest tool.

`AgentRun.audit` picks up the shared log automatically when all tools point at
the same one, and falls back to an empty log when they do not. If you hand an
agent tools with separate logs, the run-level counts will be empty and you
probably did not mean that.

Keeping counts off the return value is what makes
[[agent-never-told-what-was-filtered]] structurally true rather than a habit.
