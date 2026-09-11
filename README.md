# ChronoGuard

A general-purpose, point-in-time leakage guard for LLM agents.

ChronoGuard runs any LLM agent as if it were operating at a specific past date,
then measures how well that blinding actually holds. It's domain-agnostic and
isn't tied to any agent framework. The primary target is local models served by
[Ollama](https://ollama.com).

## Why two layers

Asking a model to "reason as of 2023-06-01" fails in two independent ways that
need different fixes:

| Channel | What happens | Fix |
| --- | --- | --- |
| **Tool leakage** | A tool (search, RAG store, API) hands back a document published after the as-of date, or one edited after it. | Solvable. Intercept every tool call, filter results by timestamp. |
| **Parametric leakage** | The weights already encode post-as-of facts, so the model answers correctly with zero tool access. | Not solvable by filtering. You can only measure it and pick a better model. |

Something that does only the first and calls itself a sandbox is solving the
easy half. ChronoGuard ships both a filtering layer and a measurement layer, and
never reports a clean bill of health it hasn't earned.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
chronoguard --help
```

Python 3.11+. Runtime dependencies are `pydantic`, `typer` and `httpx`.

## Quickstart

```bash
ollama serve &                # if it isn't already running
chronoguard models            # what's installed, and can it call tools natively
chronoguard report "When will Halden ship Meridian, and what will it cost per seat?"
```

Pointing it at your own data? Check the corpus first. This one needs no model at
all, so it's the fastest way to find out whether a run can tell you anything:

```bash
chronoguard check corpus.json --as-of 2024-03-01T00:00:00Z --published-key created_utc
```

It reports what the guard would drop, and flags the two outcomes that waste a
run: everything dropped (the agent gets no evidence, so it answers from its
weights) and nothing dropped (the corpus never straddled the date, so a clean
run proves nothing). It also names timestamp fields you haven't mapped, which is
how an unmapped `last_modified` gets caught before it leaks.

`chronoguard models` tells you which loop each model will drive:

```
2 model(s) on http://localhost:11434:

  qwen3:4b                             4.0B  native tools
  gemma3:4b                            4.3B  react fallback
```

That's read from the model's own `capabilities`, not a list baked into
ChronoGuard. Models with native tool calling get real tool definitions; the rest
get a text protocol. Both paths are tested against real models.

That runs an agent against packaged fixture corpora guarded at 2023-06-01,
probes the model for what it already knows, classifies the answer's claims, and
prints a verdict:

```
RISK: ELEVATED
  - the model reproduced 2 post-as-of fact(s) with no evidence in context
  - the model's training data runs past the simulated date (2024-08-01), so filtering cannot blind it

TOOL LEAKAGE (contained by filtering)
  1 tool call(s), 3 record(s) retrieved
  kept 2, filtered 1  (allowed=2, revised=1)
    document_store       3 seen,   2 kept,   1 filtered

PARAMETRIC LEAKAGE (measured, not contained)
  leakage 2/8 (25%), control 6/6 (100%), risk elevated
  cutoff risk: high
  produced with zero evidence in context:
    vision-pro-price       expected '$3,499'
    nobel-peace-2023       expected 'Narges Mohammadi'

CLAIMS IN THE ANSWER
  5 claim(s): 5 grounded, 0 benign, 0 suspected leak(s), groundedness 100%

ANSWER
  As of May 20th, 2023, there is no finalized ship date for Meridian and no
  agreement on pricing per seat or depot.
```

Read that carefully, because it's the case the project exists for. The filter
worked, and the one record it withheld is the interesting kind: a launch FAQ
created in February and quietly edited in October, whose current text gives away
the ship date. Its creation date is clean. Filtering on that alone hands it
straight to the agent.

The answer is clean too: every factual claim traces back to evidence, nothing
leaked into the output. And the run still isn't `low`, because the same model
asked directly with no documents at all hands over two facts from after the
as-of date, and its training runs a year past the moment being simulated.

A tool that printed only the first and third sections would call this run fine.
It wasn't fine, it was lucky.

## In Python

```python
from chronoguard import EvidenceRecord, TemporalGuard

guard = TemporalGuard("2023-06-01T00:00:00Z")
result = guard.filter([
    EvidenceRecord.from_source("...", "doc-1", published_at="2023-05-04T09:00:00Z"),
    EvidenceRecord.from_source("...", "doc-2", published_at="2023-08-11T09:00:00Z"),
])
result.kept              # only doc-1
result.filtered_count    # 1
```

Wrap any tool that returns evidence and the agent only ever sees the survivors:

```python
from chronoguard import AuditLog, MappingAdapter, guarded_tool

audit = AuditLog()

@guarded_tool(guard, MappingAdapter(source_key="url", published_key="date"), audit=audit)
def web_search(query: str) -> list[dict]:
    """Search the web."""
    return my_api(query)

web_search("meridian pricing")   # pre-as-of hits only
audit.filtered_count             # what it didn't see
```

`async def` tools work the same way, you just await the wrapped version.

On a mutable source (a wiki, a CMS, a ticket tracker) map the revision field as
well. A page created in 2022 and rewritten in 2024 has a 2022 creation date and
2024 content, and filtering on the creation date alone walks it straight past
the guard:

```python
MappingAdapter(source_key="page_id", published_key="created_at",
               updated_key="last_modified")
```

Then run the whole pipeline:

```python
from chronoguard import ScenarioConfig, run_scenario

report = run_scenario(ScenarioConfig(task="...", as_of="2023-06-01T00:00:00Z"))
report.headline_risk      # high / elevated / low / unknown
report.headline_reasons
report.render()           # the text report
report.summary()          # the JSON summary
```

## Docs

- **[docs/guide.md](docs/guide.md)** walks through every layer with examples.
- **[docs/configuration.md](docs/configuration.md)** is the full option reference.
- **[docs/interpreting-reports.md](docs/interpreting-reports.md)** covers what a
  verdict means and what to do about it.
- **[examples/](examples/)** has a worked end-to-end scenario on your own corpus.
- **[docs/kb/](docs/kb/INDEX.md)** is a knowledge base for agents working in this
  repo: atomic notes, cross-linked, loadable a few at a time.
- **[docs/releasing.md](docs/releasing.md)** covers cutting a version and
  publishing.

## Tests

```bash
pytest                        # everything; model-backed tests skip if Ollama is down
pytest -m "not integration"   # fast offline only
pytest -m integration         # needs a running local Ollama server
```

Integration tests skip with a clear message when Ollama isn't reachable or no
installed model supports the feature under test. They never fail the suite for a
missing server.

The integration suite runs a real agent loop and asserts no post-as-of fixture
content reaches the answer or its cited sources. It also asserts the model
actually called a tool, that the filter actually dropped something, and that the
probe's controls passed, so the tests can't go green because nothing happened.

## Status

Working end to end, and early. The filter, interception, agent runner, probe,
claim classifier and reporting are all in place with tests.

## License

MIT, see [LICENSE](LICENSE).
