---
id: slow-model-is-not-an-absent-server
title: A slow model is not an absent server
type: contract
description: OllamaTimeout separates a thinking model running long from a server that is actually down, because the advice differs.
tags: [ollama, cli, errors]
links: [thinking-models-and-the-think-flag, run-the-test-suites]
source: src/chronoguard/ollama.py
---

`OllamaTimeout` subclasses `OllamaUnavailable`, so old handlers keep working,
but it means something different: the server answered the connection and then
took too long.

This came from a real failure. A qwen3:4b claim classification exceeded the old
180 second client timeout, and every CLI command answered the resulting error
with "Start one with `ollama serve`". The server was running fine. The advice
sent the reader in exactly the wrong direction.

Now the message names the model, states the limit it blew through, and lists
what actually helps: raise the timeout, cap the work, or use a smaller model.
The CLI only suggests starting the server when the failure was not a timeout.

The client default is 600 seconds because reasoning models genuinely need it.

If you add a code path that talks to Ollama, let `OllamaTimeout` propagate rather
than catching `OllamaUnavailable` and printing generic advice.
