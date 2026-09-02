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

Early. Phases 0 to 5 are done: the temporal filter, tool-call interception,
fixture tools, an agent runner for local Ollama models, the parametric leakage
probe, and claim-level classification. See [PLAN.md](PLAN.md) for what lands
next.

## Quickstart

```bash
ollama serve &                # if it isn't already running
chronoguard models            # what's installed, and can it call tools natively
chronoguard run "When will Halden ship Meridian, and what will it cost per seat?"
chronoguard probe --as-of 2023-06-01T00:00:00Z    # what does it already know?
```

That runs an agent against the packaged fixture corpora, guarded at
2023-06-01. The corpus knows the real answer (October 14, $4,900 per seat) but
only in documents published after the cutoff, so a working setup gives you the
answer that was actually knowable at the time:

```
model      gemma3:4b [react]
as of      2023-06-01T00:00:00+00:00 (policy: strict)
tool calls 2
           web_search({"query": "Halden Meridian ship date cost per seat"}) kept 5, filtered 5
           document_store({"query": "Halden Meridian ship date cost per seat"}) kept 2, filtered 3
evidence   7 record(s) reached the agent
filtered   8 record(s) withheld
verdicts   {'allowed': 7, 'future': 5, 'undated': 2, 'unparseable': 1}

answer:
As of June 1st, 2023, Halden has confirmed a summer launch window for Meridian
but has not yet announced a specific ship date or price per seat. Analysts
estimate the cost to be between $2,400 and $2,900 per seat...
```

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

## Guarding a tool

Wrap any callable that returns evidence. The agent calls it normally and only
sees what survived.

```python
from chronoguard import AuditLog, MappingAdapter, TemporalGuard, guarded_tool

guard = TemporalGuard("2023-06-01T00:00:00Z")
audit = AuditLog()

@guarded_tool(guard, MappingAdapter(
    content_key=("title", "snippet"),
    source_key="url",
    published_key="date",
), audit=audit)
def web_search(query: str, limit: int = 5) -> list[dict]:
    """Search the web."""
    return my_search_api(query, limit)

hits = web_search("meridian pricing")   # list[EvidenceRecord], pre-as-of only

audit.filtered_count   # how many got dropped
audit.by_tool()        # {'web_search': {'calls': .., 'total': .., 'kept': .., 'filtered': ..}}
audit.summary()
```

The adapter is what makes this work for arbitrary tools. Search APIs, vector
stores and SQL rows all name their fields differently, so each tool brings a
small mapping and the filtering stays in one place:

- `MappingAdapter` for dict-shaped output. Handles multiple content fields,
  fallback id fields, wrapper dicts (`results_key`), and puts leftover fields
  in `metadata`.
- `RecordAdapter` (the default) for tools that already return `EvidenceRecord`s.
- Any plain `raw -> list[EvidenceRecord]` function.

Counts live on the `AuditLog`, not in the return value, so what the agent sees
stays clean. Share one log across every tool an agent gets and the report can
name which tool was leakiest. `guard_tool(fn, guard, adapter)` is the
non-decorator form. The wrapper keeps the tool's name, docstring and signature,
so agent frameworks can still build a schema from it.

Only wrap tools that return evidence. A calculator has nothing to filter.

## Fixture tools

`chronoguard.fixtures` ships two guarded tools over local corpora, no network
needed. They're about a made-up company launching a made-up product in 2023,
which is the point: no model has this in its weights, so a post-as-of string
showing up in an answer came through a tool and nowhere else.

```python
from chronoguard import TemporalGuard
from chronoguard.fixtures import FIXTURE_AS_OF, POST_AS_OF_CANARIES, build_fixture_toolset

tools = build_fixture_toolset(TemporalGuard(FIXTURE_AS_OF))
tools["web_search"]("meridian price launch", limit=99)
tools["document_store"]("meridian pricing", k=99)
```

The corpora contain pre-as-of documents carrying the plausible wrong answer
(analysts guessing "below $3,000"), the real answers in post-as-of documents
only, a document sitting exactly on the boundary instant, and undated documents
that deliberately hold future facts. `POST_AS_OF_CANARIES` lists the strings
that should never reach an agent, so tests can just grep for them.

## Running an agent

```python
from chronoguard import AuditLog, TemporalGuard
from chronoguard.agent import AgentConfig, run_agent
from chronoguard.fixtures import build_fixture_toolset

guard = TemporalGuard("2023-06-01T00:00:00Z")
tools = build_fixture_toolset(guard, AuditLog())

run = run_agent(AgentConfig(
    task="When will Meridian ship and what will it cost per seat?",
    as_of="2023-06-01T00:00:00Z",
    model=None,        # discovered from /api/tags if unset
    mode="auto",       # native tool calls if the model supports them, else react
    max_steps=6,
), tools)

run.final_answer
run.evidence               # exactly what the agent was shown
run.audit.filtered_count   # what it wasn't
run.summary()
```

Models are discovered at runtime through the Ollama API. Nothing here hardcodes
a model name. `mode="auto"` reads the model's `capabilities` from `/api/show`
and picks native tool calling when it's there, otherwise a text protocol where
the model replies with one JSON object per turn. Small models wrap that JSON in
code fences and prose, so the parser scans for a balanced object and nudges the
model a couple of times before giving up.

Two things the runner does on purpose:

- **The prompt is not the containment.** The "you are operating as of X" line
  keeps the model on task. The guard in front of the tools is what actually
  stops the future getting in. Prompts are a request, the filter is a wall.
- **The agent is never told what got filtered.** Saying "4 documents were
  withheld for postdating your cutoff" is itself a hint that the future exists
  and has something interesting in it. Those counts go to the report, not the
  model.

It also refuses to start if a tool's guard disagrees with the run's `as_of`,
which is otherwise a silent footgun: prompt says one date, filter uses another.

## Measuring what filtering can't fix

Everything above handles tool leakage. None of it touches what the model already
knows. The probe measures that directly: it asks questions whose answers only
became knowable after the as-of date, gives the model no tools at all, and
counts how many it gets right. A correct answer with zero evidence in context
came from the weights.

```bash
chronoguard probe --as-of 2023-06-01T00:00:00Z --max-future 4 --max-control 3
```

```
gemma3:4b at 2023-06-01: leakage 3/4 (75%), control 3/3 (100%), risk high, cutoff risk high

gemma3:4b was trained on data up to about 2024-08-01, which is after the simulated
date 2023-06-01. The model has read past the moment you are trying to reconstruct.
Filtering cannot undo that.

asked with no tools:
  LEAK [future ] vision-pro-price       '$3,499'
  LEAK [future ] nobel-peace-2023       'Narges Mohammadi.'
  LEAK [future ] openai-ouster          'Sam Altman was removed as chief executive of OpenAI...'
    .  [future ] uk-pm-2024             'I DO NOT KNOW.'
  ok   [control] gpt4-release           'GPT-4'
  ok   [control] chatgpt-launch         'ChatGPT'
  ok   [control] github-acquisition     'Microsoft.'
```

That's the point of the whole project in one screen. The agent run above looked
perfectly clean, because the guard did its job. The same model, asked directly,
hands over three facts from after the as-of date without being given a single
document. No filter can fix that. You either pick a model whose training predates
your as-of date, or you report the number.

```python
from chronoguard.probe import LeakageProbe

report = LeakageProbe().run("gemma3:4b", "2023-06-01T00:00:00Z")
report.leakage_score   # share of post-as-of facts produced with no evidence
report.control_score   # share of already-knowable facts it got right
report.risk_level      # high / elevated / low / inconclusive
report.explain()
```

### Why there's a control group

A model that scores zero on the future questions might be well blinded, or it
might just be bad at questions. Those look identical in a leakage number. So
every case is a probe or a control depending on where you point it: answers
knowable before the as-of date run as controls. Score zero on both and the
report says `inconclusive`, not `low`.

### Three deliberate choices

- **The probe never tells the model to pretend it's a past date.** That would
  measure instruction-following, not knowledge. We want it trying its hardest.
- **Self-reported cutoffs aren't trusted.** `model_cutoffs.json` is a prior, not
  evidence. It decides whether a run gets flagged before scoring starts. Vendors
  are vague, post-training blurs the line, and models misreport their own cutoff
  in both directions. Edit it freely, approximate is fine.
- **Scoring is exact, then fuzzy, then an optional LLM judge.** Fuzzy slides a
  window the width of the expected answer so a long reply can't dilute the
  score. The judge only runs when the cheap paths fail and the model didn't
  refuse.

### Extending the case set

`src/chronoguard/data/probe_cases.json` holds question, answer, aliases and
`knowable_from`. Add your own, or point at a different file:

```bash
chronoguard probe --cases my_cases.json --cutoffs my_cutoffs.json
```

## Checking what actually came out

The probe measures the model in isolation. This measures one specific answer.
Given an agent's final answer and the evidence it actually received after
filtering, the classifier splits the answer into atomic claims and labels each:

| Label | Meaning |
| --- | --- |
| `grounded` | The provided evidence states or supports it. |
| `ungrounded-but-benign` | Reasoning, hedging, opinion, general background. Nothing to leak. |
| `suspected-parametric-leak` | A specific fact (name, number, date, event) that isn't in the evidence and isn't general background. |

```python
from chronoguard.claims import classify_run

report = classify_run(run, judge_model="gemma3:4b")
report.leaks          # claims asserting facts the agent was never given
report.groundedness   # share of factual claims traceable to evidence
report.explain()
```

That last label is why this exists. Tool leakage shows up in the audit log as a
count of dropped records. Parametric leakage leaves no trace in the plumbing at
all: the agent asks for evidence, gets clean evidence, and then writes down
something it already knew. The output is the only place to catch it.

A couple of notes:

- **The judge is never asked "is this parametric leakage?"** That would mean
  speculating about model internals. It's asked whether the fact appears in the
  documents, and ChronoGuard applies the loaded name to the answer. Keeping the
  judge's job narrow is what makes a small local model usable for it.
- **`groundedness` excludes benign claims.** An answer that's mostly hedging
  isn't well grounded, it just isn't asserting much, so hedges stay out of the
  denominator.
- **Judge quality depends on the judge model.** `tests/claim_fixtures.py` holds
  answer-plus-evidence fixtures with known correct labels, and the integration
  suite grades a real model against them. `gemma3:4b` scores 6/6 on those.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
chronoguard --help
```

## Tests

```bash
pytest                        # everything, model-backed tests skip if Ollama is down
pytest -m "not integration"   # fast offline only
pytest -m integration         # needs a running local Ollama server
```

Integration tests skip with a clear message when Ollama isn't reachable or when
no installed model supports the feature under test. They never fail the suite
for a missing server.

The integration suite runs a real agent loop and asserts no post-as-of fixture
content reaches the answer or the cited sources. It also asserts the model
actually called a tool and that the filter actually dropped something, so the
test can't pass because nothing happened.

## License

MIT, see [LICENSE](LICENSE).
