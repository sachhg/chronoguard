# ChronoGuard Design

## The problem

You want to know how an LLM agent *would have* behaved at some past moment.
Maybe you're backtesting a research agent, evaluating a forecasting workflow, or
checking whether a retrieval pipeline can reconstruct a decision from the
evidence that actually existed at the time. So you write a prompt saying "answer
as of 2023-06-01" and point the agent at a corpus.

That doesn't work. It fails in two ways that look identical from the outside but
have different causes and different fixes. Mixing them up is the mistake this
project exists to avoid.

## Channel 1: tool leakage

The agent calls a tool (web search, a vector store, a REST API, a SQL query) and
gets back a document published *after* the as-of date. It reasons over
information from the future and produces a suspiciously good answer.

This is a data plumbing problem and it's genuinely solvable. Every piece of
evidence entering the agent's context has, or should have, a publication
timestamp. Intercept every tool call, drop anything stamped later than the as-of
date, and the future stops coming in through that door.

The comparison itself is one line of code. What actually matters is the
discipline around it:

- **One canonical evidence type.** Every tool, whatever shape its native output
  has, gets adapted into a single record type: content, source id, a
  timezone-aware `published_at`, a `retrieved_at`, and free-form metadata.
  Filtering lives in exactly one place and every tool inherits it.
- **Conservative defaults.** A record with a missing or junk timestamp gets
  rejected, not waved through. The dangerous failure is silently admitting
  undated content. The permissive behavior exists, but you have to ask for it.
- **An explicit boundary rule.** "Before the as-of date" is ambiguous at the
  instant itself. ChronoGuard picks a rule, writes it down, and tests it.
- **Counting, not just dropping.** How many records got filtered is part of the
  output. "The guard removed 41 of 60 retrieved documents" tells you something
  real about your corpus, and throwing it away loses the most interesting number
  in the run.

Tool leakage is the easy half. Solving it well is table stakes.

## Channel 2: parametric leakage

The model's weights were trained on text describing things that happened after
your as-of date. It doesn't need a tool call. Ask it who won an election held
after your as-of date, give it zero evidence, and it may well tell you, because
the answer is already in the parameters.

No amount of context filtering touches this. The filtering layer controls what
comes in through the tool channel. Parametric knowledge is already inside the
model before the run starts, and you can't subtract it from the weights. This is
why a system that only filters tool output and then calls itself a "temporal
sandbox" is overclaiming. It locked one door and left the other open.

There are two honest responses:

1. **Model selection.** Prefer models whose real training cutoff predates your
   as-of date. If it doesn't, you aren't blinding anything. You're asking a
   model that already knows the answer to pretend it doesn't, and hoping.
2. **Measurement.** Probe for the leakage and report a number. Don't trust a
   model's self-reported cutoff: models are often wrong about their own cutoff
   in both directions, and post-training on recent data blurs it further. "We
   asked it 20 questions only answerable after the as-of date, no tools, and it
   got 11 right" beats anything printed on a model card.

So ChronoGuard treats parametric leakage as measurement, not containment, and
reports it as a risk score on every run instead of pretending it's gone.

## Side by side

| | Tool leakage | Parametric leakage |
| --- | --- | --- |
| Cause | Retrieved evidence postdates as-of | Training data postdates as-of |
| Gets in via | Tool return values | Model weights |
| Fixable? | Yes, filter it | No, you can only measure it and pick a better model |
| What ChronoGuard does | Intercept and drop | Probe, score, flag |
| If you ignore it | The agent reads the future | The agent *is* the future |

## Architecture

```
                 +------------------------------------------+
   raw tool  --> | adapter  ->  EvidenceRecord[]             |
   output        +--------------------+---------------------+
                                      v
                 +------------------------------------------+
                 | TemporalGuard(as_of, policy)             |  Layer 1
                 |  keep / drop / flag  + filtered counts   |  containment
                 +--------------------+---------------------+
                                      v
                 +------------------------------------------+
                 | agent loop (Ollama, native or ReAct)     |
                 +--------------------+---------------------+
                                      v
             +------------------------+---------------------+
             v                                              v
  +-----------------------+                    +------------------------+
  | parametric probe      |  Layer 2           | claim classifier       |  Layer 2
  | (no tools, scored)    |  measurement       | grounded / benign /    |  measurement
  +-----------------------+                    | suspected leak         |
                                               +------------------------+
                                      |
                                      v
                            human report + JSON summary
```

Layer 1 is deterministic, offline, and fully unit-testable. Layer 2 is
statistical and needs a model, so the scoring logic gets unit tested against
synthetic fixtures while the model-in-the-loop parts sit behind an integration
marker.

The claim classifier bridges the two layers. It takes the agent's final answer
plus the evidence the agent actually received after filtering, and asks which
assertions can't be traced back to that evidence. An untraceable specific fact
is the observable fingerprint of parametric leakage in an end-to-end run, which
is exactly what filtering can never catch.

## Commitments

- **Domain-agnostic core.** No sport, league, ticker, or vertical inside
  `src/chronoguard/`. Domain material lives in `examples/`.
- **Framework-agnostic.** The interception layer wraps a plain Python callable.
  If your tool is a function, it can be guarded, no matter what orchestrator
  calls it.
- **Local first.** Ollama is the primary target. Leakage evaluation means firing
  a lot of probe questions, and that shouldn't need an API budget or ship your
  eval corpus to a third party.
- **Conservative by default, permissive on request.** Missing timestamps, junk
  dates, boundary instants: every ambiguous case defaults to the safe answer,
  with an explicit opt-out.
- **Honest reporting.** The output says what got contained and what could only
  be measured. ChronoGuard never claims an agent was "blinded". It reports how
  much leakage it found and how much risk is left.
