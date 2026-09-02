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

Early. Phase 0 (scaffolding) and Phase 1 (the temporal filter) are done. See
[PLAN.md](PLAN.md) for what lands next.

## Layer 1: filtering evidence

```python
from chronoguard import EvidenceRecord, TemporalGuard

guard = TemporalGuard("2023-06-01T00:00:00Z")

result = guard.filter([
    EvidenceRecord.from_source("...", "doc-1", published_at="2023-05-04T09:00:00Z"),
    EvidenceRecord.from_source("...", "doc-2", published_at="2023-08-11T09:00:00Z"),
    EvidenceRecord.from_source("...", "doc-3", published_at="sometime last year"),
])

result.kept              # [doc-1]
result.filtered_count    # 2
result.summary()         # 'as_of=... kept 1/3, filtered 2 (allowed=1, future=1, unparseable=1)'

for j in result.violations:
    print(j.record.source_id, j.verdict.value, j.reason)
```

`from_source()` is the lenient constructor for real tool output: it never raises
on a bad date, it records what it choked on. Use `EvidenceRecord(...)` directly
when you control the data and want naive datetimes to fail loudly.

### The rules

| Case | Default | Why |
| --- | --- | --- |
| `published_at < as_of` | allowed | It existed at the simulated moment. |
| `published_at == as_of` | **rejected** | The boundary is exclusive, see below. |
| `published_at > as_of` | rejected | This is the leak we're here for. |
| No timestamp | rejected | Can't prove it predates the cutoff. `allow_undated=True` overrides. |
| Junk or timezone-naive timestamp | rejected | Same reason. A wall clock with no offset isn't an instant. |
| `retrieved_at` after `as_of` | ignored | Normal. You're running the backtest today. |

**Why the boundary is exclusive.** Plenty of corpora store dates at day
precision, so "published 2023-06-01" becomes `2023-06-01T00:00:00Z`. With an
inclusive boundary and `as_of` at midnight on the 1st, every document published
anywhere in that day sails through, including ones written hours after the
moment you're simulating. Dropping a record published on the exact microsecond
of the cutoff costs you nothing. Admitting a day of hindsight costs you the
experiment. If you want a full day, name the next midnight: `2023-06-02T00:00:00Z`.

### Policies

- `strict` (default) drops violations. The agent never sees them.
- `warn` keeps everything but flags it. Handy for measuring how much leakage a
  corpus *would* have caused, without actually blinding the run.

Records that `warn` lets through still count as violations, so `kept` never gets
mistaken for clean.

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
