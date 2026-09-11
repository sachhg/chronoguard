---
id: repo-conventions
title: Repo conventions in one place
type: map
description: Writing style, commit rules and test discipline for anyone touching this repo.
tags: [conventions, orientation]
links: [module-map, run-the-test-suites]
source: docs/kb/repo-conventions.md
---
What you are walking into:

- No em dashes anywhere, including code comments and commit messages. Plain
  programmer voice, no LLM filler vocabulary.
- Commits are authored by the repo owner only. No `Co-Authored-By`, no tool
  attribution lines, ever.
- Conventional prefixes, small modular commits, never commit red tests.
- `src/chronoguard/` stays domain-agnostic. No sport, ticker or vertical.
  Domain material goes in `examples/`.
- Fast offline suite stays offline and fast; model-backed work is
  integration-marked.

This knowledge base is the working detail.
