---
id: backend-protocol
title: Any four-method client can drive ChronoGuard
type: contract
description: ChatBackend is the whole model-server surface; OllamaClient and OpenAICompatClient both satisfy it.
tags: [backends, api, ollama]
links: [adapter-interface, slow-model-is-not-an-absent-server, module-map]
source: src/chronoguard/backends.py
---
The probe, the claim classifier and the agent loop between them call four
things on a client: `chat`, `pick_model`, `supports_tools`, `list_models`.
`ChatBackend` is that surface as a Protocol, plus `is_available`, `model_names`
and a `host` attribute that reports and error messages read. Anything satisfying
it can be passed to `run_agent`, `LeakageProbe`, `ClaimClassifier` or
`run_scenario`. Two ship:

- **`OllamaClient`**, the default everywhere, unchanged.
- **`OpenAICompatClient`**, for any `/v1/chat/completions` server: vLLM, LM
  Studio, llama.cpp's server, text-generation-webui, OpenRouter, the hosted
  APIs. One client, no provider-specific code, no new dependency.

Local-first still holds: most of that list is a local server.

## Where the two APIs actually differ

Three things, all handled in the backend rather than leaking upward:

- **Tool arguments.** Ollama sends an object, this API sends a JSON string. The
  agent loop already parsed both because ReAct output is text, so they pass
  through untouched.
- **Tool results.** Ollama matches a result to its call by name, the OpenAI API
  by id. `ChatMessage` carries `tool_name` and `tool_call_id`, the agent sets
  whichever the model supplied, and each backend emits the field it needs.
- **Temperature.** Nested under `options` for Ollama, top level here.

## Tool support is declared, not discovered

There is no capability endpoint in this API, so `supports_tools` cannot ask.
`OpenAICompatClient(tool_models=...)` takes `True` (the default, right for
essentially every current server), `False`, or an explicit set of names.
`OllamaClient` reads real capabilities from `/api/show` instead.

## Exceptions

`BackendUnavailable` and `BackendTimeout` live in `backends.py`.
`OllamaUnavailable` and `OllamaTimeout` subclass them, so existing handlers keep
working. The timeout split matters for the usual reason, see
[[slow-model-is-not-an-absent-server]].

Deliberately absent: streaming, embeddings, retries, provider SDKs.
