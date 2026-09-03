---
id: run-the-test-suites
title: How to run the two test suites
type: procedure
description: Fast offline suite versus the Ollama-backed integration suite.
tags: [testing, howto]
links: [integration-tests-skip-never-fail, test-that-the-raw-tool-leaks-first, test-doubles-matching-whole-prompts, thinking-models-and-the-think-flag]
source: pyproject.toml
---
```bash
pytest                        # everything; model-backed tests skip if Ollama is down
pytest -m "not integration"   # fast offline only, sub-second
pytest -m integration         # needs `ollama serve` running
```

The offline suite must stay fast and must never touch the network. If a change
makes it slow, something that needed a model leaked into it.

To check the skip behaviour is still clean, point the host at a dead port:

```bash
OLLAMA_HOST=localhost:19999 pytest
```

Everything integration-marked should skip with a message naming the host, and
nothing should fail.
