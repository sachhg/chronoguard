# ChronoGuard — Design

## The problem

You want to know how an LLM agent *would have* behaved at some past moment.
Maybe you're backtesting a research agent, evaluating a forecasting workflow, or
checking whether a retrieval pipeline can reconstruct a decision from the
evidence that actually existed at the time. So you write a prompt that says
"answer as of 2023-06-01" and you point the agent at a corpus.

That does not work, and it fails in two ways that look identical from the
outside but have completely different causes and completely different fixes.
Conflating them is the central mistake this project exists to avoid.

## Channel 1: Tool leakage

The agent calls a tool — web search, a vector store, a REST API, a SQL query —
and the tool hands back a document that was published *after* the as-of date.
The agent then reasons over information from the future and produces a
suspiciously good answer.

**This is a data-plumbing problem, and it is genuinely solvable.** Every piece
of evidence entering the agent's context has (or should have) a publication
timestamp. If you intercept every tool call and drop everything stamped later
than the as-of date, the future stops entering through that door.

The engineering that matters here is not the comparison itself — that's one
line — it's the discipline around it:

- **A single canonical evidence type.** Every tool, whatever its native output
  shape, gets adapted into one record type carrying content, a source id, a
  timezone-aware `published_at`, a `retrieved_at`, and free-form metadata.
  Filtering logic lives in exactly one place and every tool inherits it.
- **Conservative defaults.** A record with a missing or unparseable timestamp is
  *rejected*, not waved through. The dangerous failure mode is silently
  admitting undated content. The permissive behavior exists but you have to ask
  for it explicitly.
- **An explicit, documented boundary rule.** "Before the as-of date" is
  ambiguous at the instant itself. ChronoGuard picks one rule, writes it down,
  and tests it.
- **Counting, not just dropping.** The number of records filtered out is part of
  the output. "The guard removed 41 of 60 retrieved documents" is a signal about
  your corpus, and silently discarding it wastes the most interesting number in
  the run.

Tool leakage is the easy half. Solving it well is table stakes.

## Channel 2: Parametric leakage

The model's weights were trained on text describing things that happened after
your as-of date. It doesn't need a tool call. Ask it who won an election held
after your as-of date, hand it zero evidence, and it may well tell you —
because the answer is baked into the parameters.

**No amount of context filtering touches this.** The filtering layer controls
what enters through the tool channel; parametric knowledge is already inside the
model before the run starts. You cannot subtract it from the weights. This is
why a system that only filters tool output and then calls itself a "temporal
sandbox" is overclaiming: it has secured one door and left the other wide open.

There are exactly two honest responses:

1. **Model selection.** Prefer models whose actual training cutoff predates your
   as-of date. If it does not, you are not blinding the model — you are asking a
   model that already knows the answer to pretend it doesn't, and hoping.
2. **Measurement.** Probe for the leakage directly and report a number. Do not
   take the model's self-reported cutoff at face value: models are frequently
   wrong about their own cutoff in both directions, and post-training on recent
   data muddies it further. The empirical answer — "we asked it 20 questions
   only answerable after the as-of date with no tools, and it got 11 right" — is
   worth more than any claim in a model card.

So ChronoGuard treats parametric leakage as a *measurement* problem, not a
containment problem, and reports it as a risk score attached to every run rather
than pretending it has been eliminated.

## The two layers, side by side

| | Tool leakage | Parametric leakage |
| --- | --- | --- |
| Cause | Retrieved evidence postdates as-of | Training data postdates as-of |
| Enters via | Tool return values | Model weights |
| Fixable? | Yes — filter it | No — only measured and mitigated by model choice |
| ChronoGuard's job | Intercept and drop | Probe, score, and flag |
| Failure if ignored | Agent reads the future | Agent *is* the future |

## Architecture

```
                 ┌──────────────────────────────────────────┐
   raw tool  ──▶ │ adapter  →  EvidenceRecord[]             │
   output        └──────────────────┬───────────────────────┘
                                    ▼
                 ┌──────────────────────────────────────────┐
                 │ TemporalGuard(as_of, policy)             │  Layer 1
                 │  keep / drop / flag  + filtered counts   │  (containment)
                 └──────────────────┬───────────────────────┘
                                    ▼
                 ┌──────────────────────────────────────────┐
                 │ agent loop (Ollama, native or ReAct)     │
                 └──────────────────┬───────────────────────┘
                                    ▼
             ┌──────────────────────┴───────────────────────┐
             ▼                                              ▼
  ┌───────────────────────┐                    ┌────────────────────────┐
  │ parametric probe      │  Layer 2           │ claim classifier       │  Layer 2
  │ (no tools, scored)    │  (measurement)     │ (grounded / benign /   │  (measurement)
  └───────────────────────┘                    │  suspected leak)       │
                                               └────────────────────────┘
                                    │
                                    ▼
                          human report + JSON summary
```

Layer 1 is deterministic, offline, and fully unit-testable. Layer 2 is
statistical and needs a model, so its scoring logic is unit-tested against
synthetic fixtures while the model-in-the-loop parts live behind an integration
marker.

The claim classifier is the bridge between the two layers: it takes the agent's
final answer plus *the evidence the agent actually received after filtering*,
and asks which assertions in the answer cannot be traced back to that evidence.
An untraceable specific fact is the observable fingerprint of parametric
leakage in an end-to-end run — the thing filtering can never catch.

## Design commitments

- **Domain-agnostic core.** No sport, league, ticker, or vertical appears in
  `src/chronoguard/`. Domain material lives in `examples/`.
- **Framework-agnostic.** The interception layer wraps a plain Python callable.
  If your tool is a function, it can be guarded, whatever orchestrator calls it.
- **Local-first.** Ollama is the primary target: leakage evaluation involves
  sending a lot of probe questions, and that should not require an API budget or
  ship your evaluation corpus to a third party.
- **Conservative by default, permissive on request.** Every ambiguous case
  (missing timestamps, unparseable dates, boundary instants) defaults to the
  safe answer, with an explicit opt-out.
- **Honest reporting.** The output says what was contained *and* what could only
  be measured. ChronoGuard never claims an agent was "blinded" — it reports how
  much leakage it found and how much risk remains.
