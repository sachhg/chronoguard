# ChronoGuard

A general-purpose, point-in-time leakage guard for LLM agents.

ChronoGuard lets you run any LLM agent *as if* it were operating at a specific
past date — and, more importantly, **measures how well that blinding actually
holds**. It is domain-agnostic and not tied to any single agent framework. The
primary test target is local models served by [Ollama](https://ollama.com).

## Why two layers?

Asking a model to "reason as of 2023-06-01" fails in two independent ways, and
they need different fixes:

| Channel | What happens | Fix |
| --- | --- | --- |
| **Tool leakage** | A tool (search, RAG store, API) returns a document published *after* the as-of date. | Solvable. Intercept every tool call and filter results by timestamp. |
| **Parametric leakage** | The model's weights already encode post-as-of facts, so it answers correctly with *zero* tool access. | Not solvable by filtering. Only measurable — probe for it and report it. |

A tool that only does the first and calls itself a sandbox is solving the easy
half. ChronoGuard ships both a filtering layer and a probing/measurement layer.

See [DESIGN.md](DESIGN.md) for the full reasoning and [PLAN.md](PLAN.md) for the
build plan.

## Status

Early development. Phase 0 (scaffolding) complete; see [PLAN.md](PLAN.md) for
what lands next.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
chronoguard --help
```

## Tests

```bash
pytest                     # fast, offline unit tests
pytest -m integration      # requires a running local Ollama server
```

Integration tests skip cleanly with a clear message when Ollama is unreachable.

## License

MIT — see [LICENSE](LICENSE).
