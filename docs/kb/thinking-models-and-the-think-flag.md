---
id: thinking-models-and-the-think-flag
title: Leave thinking alone on reasoning models
type: pitfall
description: Ollama's think:false merges reasoning into content and breaks scoring; the default already separates them.
tags: [ollama, probe, claims, prompting]
links: [claim-and-answer-matching, run-the-test-suites, containment-vs-measurement]
source: src/chronoguard/ollama.py
---

qwen3:4b reports a `thinking` capability and produces roughly 13,000 characters
of reasoning per answer. Probe and judge calls are mechanical, so turning that
off looks like an easy win. It is not.

Measured on qwen3:4b against `/api/chat`:

- **Default** (what our client sends): reasoning goes in a separate `thinking`
  field, `content` holds a clean 120 character answer. Correct for us.
- **`think: false`**: accepted without error, but the reasoning appears *inside*
  `content`, so scoring sees a rambling paragraph instead of an answer.

So the client deliberately does not send `think`. `ChatResponse` ignores the
`thinking` field, which is what keeps `content` clean.

The cost is wall clock. A full probe against a reasoning model takes minutes,
not seconds. Cap it with `--max-future` and `--max-control` when iterating, and
expect the integration suite to be slow whenever a thinking model is the one
discovered by `pick_model`.
