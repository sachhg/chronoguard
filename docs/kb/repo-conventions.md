---
id: repo-conventions
title: Repo conventions in one place
type: map
description: Writing style, commit rules and test discipline; CLAUDE.md is the source of truth.
tags: [conventions, orientation]
links: [module-map, run-the-test-suites]
source: CLAUDE.md
---
`CLAUDE.md` is authoritative. Summary so you know what you are walking into:

- No em dashes anywhere, including code comments and commit messages. Plain
  programmer voice, no LLM filler vocabulary.
- Commits are authored by the repo owner only. No `Co-Authored-By`, no tool
  attribution lines, ever.
- Conventional prefixes, small modular commits, never commit red tests.
- `src/chronoguard/` stays domain-agnostic. No sport, ticker or vertical.
  Domain material goes in `examples/`.
- Fast offline suite stays offline and fast; model-backed work is
  integration-marked.

`DESIGN.md` is the why, `PLAN.md` is the phase order, this knowledge base is the
working detail.
