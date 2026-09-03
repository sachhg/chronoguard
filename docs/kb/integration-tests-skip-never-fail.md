---
id: integration-tests-skip-never-fail
title: Integration tests skip, they never fail on a missing server
type: pitfall
description: Fixtures in conftest.py skip with an actionable message; keep new model-backed tests behind them.
tags: [testing]
links: [run-the-test-suites]
source: tests/conftest.py
---
`ollama_client`, `ollama_model` and `tool_capable_model` skip with a message
naming the host or suggesting a pull. A missing server or a missing capability
must never turn the suite red.

New model-backed tests need `@pytest.mark.integration` (or the module-level
`pytestmark`) and must take their client from those fixtures rather than
constructing `OllamaClient()` directly. Constructing one directly is how a test
ends up failing on a laptop with no Ollama.

Check it with `OLLAMA_HOST=localhost:19999 pytest`.
