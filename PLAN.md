# ChronoGuard — Build Plan

Phases are built **in order**. A phase is done only when its automated tests
pass; "it ran once by hand" does not count. Each phase is committed in several
milestones (scaffolding -> one commit per module as its tests go green -> a
final docs/wiring commit), never as one giant commit.

Conventions used throughout:

- Conventional commit prefixes: `feat:`, `test:`, `fix:`, `docs:`, `chore:`.
- Unit tests are fast and offline — synthetic fixtures, no network, no Ollama.
- Anything that touches a real model is marked `@pytest.mark.integration` and
  **skips cleanly** with a clear message when Ollama is unreachable.

---

## Phase 0 — Scaffolding

- `pyproject.toml` (Python 3.11+, src layout, hatchling), `.gitignore`, MIT
  `LICENSE`, `README.md` skeleton.
- `DESIGN.md` — the two-layer leakage argument written down so the reasoning
  survives the loss of any prompt or conversation.
- `PLAN.md` — this file.
- CLI entrypoint stub: `chronoguard --help` works, registered as a console
  script and runnable via `python -m chronoguard`.
- `pytest` runs green; `pip install -e ".[dev]"` succeeds.

## Phase 1 — Core temporal filter primitive

Modules: `chronoguard/evidence.py`, `chronoguard/guard.py`

- `EvidenceRecord`: content, `source_id`, `published_at` (timezone-aware),
  `retrieved_at`, `metadata` dict.
- `TemporalGuard(as_of, policy)`: filters a list of records.
  - `strict` — violations are dropped.
  - `warn` — violations are kept but flagged.
- The boundary rule (whether `published_at == as_of` is admitted) is chosen,
  documented, and tested.
- Missing/unparseable timestamps are **rejected by default**; admitting them is
  opt-in.
- Filtered counts are part of the result, not a side effect.
- Unit tests: boundary-exact, missing timestamp, unparseable timestamp, mixed
  timezones, empty input, all-violating input.

## Phase 2 — Tool-call interception middleware

Modules: `chronoguard/interception.py`, `chronoguard/fixtures/`

- A wrapper (decorator / higher-order function) that takes **any** Python
  callable used as an agent tool and returns a guarded version whose output
  passes through the Phase 1 filter before the agent sees it.
- An adapter interface so each tool maps its own raw output shape into
  `EvidenceRecord`s.
- Two deterministic fixture tools, each with a mix of pre- and post-as-of
  content: a fake web search over a local dated JSON corpus, and a fake document
  store for RAG-style retrieval.
- Tests: agent-facing output never contains post-as-of content; the count of
  filtered records is retrievable for reporting.

## Phase 3 — Ollama agent runner

Modules: `chronoguard/ollama.py`, `chronoguard/agent.py`

- Minimal agent loop against local Ollama. Models are **discovered at runtime**
  (`/api/tags`), never hardcoded.
- Native tool-calling where the model supports it; a text-parsing ReAct fallback
  where it doesn't.
- Guarded Phase 2 fixture tools wired in.
- Config surface: `as_of`, model name, task prompt.
- Integration test: a full loop against a locally installed model, asserting no
  post-as-of fixture content reaches the final answer or its cited sources.

## Phase 4 — Parametric leakage probe

Modules: `chronoguard/probe.py`, `chronoguard/data/model_cutoffs.*`

- A small, generic (explicitly non-sports) fixture set of post-cutoff facts:
  question, as-of date, ground-truth answer that only became knowable later.
  Easy to extend.
- A probe that queries a model with **zero tool access** and scores whether it
  reveals the future answer: exact match, plus a fuzzy / LLM-judge fallback for
  free-text answers.
- A user-editable per-model "known training cutoff" config (approximate is
  fine). Any run whose `as_of` predates the model's own cutoff is flagged as
  high leakage risk *before* scoring begins.
- Output: a leakage score per `(model, as_of)` pair.
- Unit tests for scoring against synthetic fixtures; an integration test against
  a real local model.

## Phase 5 — Claim-level leakage classification

Module: `chronoguard/claims.py`

- Given an agent's final answer plus the guarded evidence it actually received,
  an LLM-as-judge prompt decomposes the answer into atomic claims and labels
  each:
  - **grounded** — traceable to the provided evidence;
  - **ungrounded-but-benign** — reasoning or opinion, not a factual assertion;
  - **suspected-parametric-leak** — a specific fact absent from the evidence and
    not generic world knowledge.
- Tests use synthetic answer + evidence fixtures whose correct labels are known
  in advance.

## Phase 6 — Reporting

Modules: `chronoguard/report.py`, CLI command

- One CLI command runs an end-to-end scenario (agent run -> leakage probe ->
  claim classification) and emits both a human-readable report and a
  machine-readable JSON summary: records filtered, probe score, flagged claims
  with reasons.
- Tests for report content and formatting.

## Phase 7 — Docs, example, polish

- Full README: install, quickstart, config reference.
- One worked `examples/` scenario in a clearly non-sports domain (e.g. reasoning
  about a product launch or a policy change as of a past date), end to end.
- Full suite green; final commit.
