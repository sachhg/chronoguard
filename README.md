# ChronoGuard

A general-purpose, point-in-time leakage guard for LLM agents.

ChronoGuard runs any LLM agent as if it were operating at a specific past date,
then measures how well that blinding actually holds. It's domain-agnostic and
isn't tied to any agent framework. The primary test target is local models
served by [Ollama](https://ollama.com).

## Why two layers

Asking a model to "reason as of 2023-06-01" fails in two independent ways that
need different fixes:

| Channel | What happens | Fix |
| --- | --- | --- |
| **Tool leakage** | A tool (search, RAG store, API) hands back a document published after the as-of date. | Solvable. Intercept every tool call, filter results by timestamp. |
| **Parametric leakage** | The weights already encode post-as-of facts, so the model answers correctly with zero tool access. | Not solvable by filtering. You can only measure it and pick a better model. |

Something that does only the first and calls itself a sandbox is solving the
easy half. ChronoGuard ships both a filtering layer and a measurement layer.

[DESIGN.md](DESIGN.md) has the full reasoning, [PLAN.md](PLAN.md) has the build
order.

## Status

Early. Phase 0 (scaffolding) done. See [PLAN.md](PLAN.md) for what lands next.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
chronoguard --help
```

## Tests

```bash
pytest                        # fast offline unit tests
pytest -m integration         # needs a running local Ollama server
pytest -m "not integration"   # explicitly skip anything model-backed
```

Integration tests skip with a clear message when Ollama isn't reachable, they
don't fail the suite.

## License

MIT, see [LICENSE](LICENSE).
