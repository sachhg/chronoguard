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

The cost is wall clock, and it is worse than it sounds. A single qwen3:4b claim
classification on a long evidence block runs past three minutes, which is what
blew through the old 180 second client timeout and produced a misleading "start
ollama serve" message. See [[slow-model-is-not-an-absent-server]].

Practical guidance:

- **Do not use a reasoning model as the claim judge.** Classification is
  mechanical: pick a label, name the documents. `--judge gemma3:4b` finishes in
  seconds and scores 10/11 on the fixtures. A thinking model spends minutes per
  claim to reach the same kind of answer.
- Cap probe runs with `--max-future` and `--max-control` while iterating.
- Expect the integration suite to be slow whenever `pick_model` lands on a
  thinking model.
