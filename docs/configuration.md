# Configuration reference

Every option, in one place. Defaults are the conservative choice everywhere:
if a setting is ambiguous, the default is the one that risks dropping evidence
rather than admitting it.

## Dates, everywhere

Every `as_of` and every timestamp needs an explicit timezone offset.
`2023-06-01T00:00:00Z` works, `2023-06-01` does not, and the error tells you so.
A bare date is not a point on the timeline, and guessing an offset is how you
get off-by-a-day leaks.

The as-of boundary is **exclusive**: `published_at < as_of` is allowed, and a
record published at exactly `as_of` is rejected. Want a full day? Name the next
midnight.

## TemporalGuard

```python
TemporalGuard(as_of, *, policy=GuardPolicy.STRICT, allow_undated=False)
```

| Option | Default | Meaning |
| --- | --- | --- |
| `as_of` | required | The instant being simulated. Aware datetime or ISO string with an offset. |
| `policy` | `strict` | `strict` drops violations. `warn` keeps and flags them, for measuring how much a corpus *would* have leaked. |
| `allow_undated` | `False` | Admit records with no usable publication timestamp. Covers missing, junk and timezone-naive dates together. |

Records the `warn` policy lets through still count as violations, so
`filtered_count` and `violation_count` are separate numbers.

## EvidenceRecord

| Field | Default | Meaning |
| --- | --- | --- |
| `content` | required | The text the agent will see. |
| `source_id` | required | Stable id: a URL, a document id, a primary key. |
| `published_at` | `None` | When the world could first have seen this. The only field filtered on. |
| `retrieved_at` | `None` | When your pipeline fetched it. Never filtered on, audit only. |
| `metadata` | `{}` | Anything else. `MappingAdapter` parks unconsumed fields here. |
| `published_at_raw` | `None` | What a timestamp was before it failed to parse, kept for reporting. |

`EvidenceRecord(...)` is strict and raises on a naive datetime.
`EvidenceRecord.from_source(...)` is lenient, never raises, and takes an extra
`assume_tz` for corpora that store naive timestamps.

## MappingAdapter

```python
MappingAdapter(*, content_key="content", source_key=("source_id", "id", "url"),
               published_key=("published_at", "published", "date"),
               retrieved_key=None, results_key=None, metadata_keys=None,
               assume_tz=None, separator="\n")
```

| Option | Meaning |
| --- | --- |
| `content_key` | One field, or several joined in order. Missing ones are skipped, so `("title", "snippet")` works on records that only have a title. |
| `source_key` | One field or several candidates, first hit wins. Falls back to `record-<n>` when none are present. |
| `published_key` | Same, for the publication timestamp. |
| `retrieved_key` | Optional. |
| `results_key` | For tools returning a wrapper like `{"matches": [...]}`. |
| `metadata_keys` | Which leftovers to keep. Default keeps everything unconsumed. |
| `assume_tz` | Timezone to assume for naive timestamps. Without it, naive means rejected. |

## GuardedTool

```python
guard_tool(fn, guard, adapter=None, *, name=None, audit=None, render=None)
guarded_tool(guard, adapter=None, *, name=None, audit=None, render=None)   # decorator
```

| Option | Default | Meaning |
| --- | --- | --- |
| `adapter` | `RecordAdapter()` | How to map the tool's output. A plain callable works too. |
| `name` | the function's name | Name used in the audit log and tool schema. |
| `audit` | a fresh log | Share one across every tool an agent gets, or run-level counts will be empty. |
| `render` | returns `result.kept` | Turns the `FilterResult` into what the agent sees. Gets the whole result, so it can read what was dropped. |

## AgentConfig

| Option | Default | Meaning |
| --- | --- | --- |
| `task` | required | What to ask the agent. |
| `as_of` | required | Must match the guard on every tool, or the runner refuses to start. |
| `model` | `None` | Discovered from `/api/tags` at runtime when unset. |
| `mode` | `auto` | `auto` reads the model's capabilities and picks. `native` forces tool calling, `react` forces the text protocol. |
| `max_steps` | `6` | Cap on loop iterations. |
| `temperature` | `0.0` | Kept at zero so runs are reproducible. |
| `max_format_retries` | `2` | How many times to nudge a model replying with unparseable text before taking it as the answer. |

## ScenarioConfig

Everything in `AgentConfig` except `temperature` and `max_format_retries`, plus:

| Option | Default | Meaning |
| --- | --- | --- |
| `judge_model` | `None` | Model for claim classification. Reuses the agent model when unset. |
| `policy` | `strict` | Passed to the guard built for the default fixture tools. |
| `probe` | `True` | Run the parametric leakage probe. Skipping drops the verdict to `unknown`. |
| `max_future_cases` | `None` | Cap on probe questions. Capping keeps the cases nearest the as-of date, where leakage shows. |
| `max_control_cases` | `None` | Cap on control questions. `0` asks none. |
| `classify` | `True` | Run claim classification. Skipping drops the verdict to `unknown`. |
| `max_claims` | `8` | Cap on claims classified per answer. |

## LeakageProbe

```python
LeakageProbe(client=None, *, cases=None, cutoffs=None, judge_model=None,
             threshold=0.85, temperature=0.0)
```

`threshold` is the fuzzy match cutoff. `judge_model` enables an LLM judge for
free-text answers that exact and fuzzy matching both miss; it only runs when the
cheap paths fail and the model did not refuse.

## Data files

Both are packaged, both are user-editable, and both accept a custom path.

**`src/chronoguard/data/probe_cases.json`**

```json
{"cases": [{
  "id": "unique-slug",
  "question": "A question with one specific answer.",
  "answer": "The Answer",
  "aliases": ["Answer", "alternative phrasing"],
  "knowable_from": "2024-07-05T00:00:00Z",
  "topic": "world events"
}]}
```

A case is a leakage probe for any `as_of` at or before `knowable_from`, and a
control for any `as_of` after it. One corpus serves both roles. A bare JSON list
works too.

**`src/chronoguard/data/model_cutoffs.json`**

```json
{"cutoffs": {"gemma3": "2024-08-01", "qwen3": "2024-12-01"}}
```

Keyed by model family. A model name is reduced by dropping any registry prefix
and the `:tag`, then matched exactly, then by longest key prefix, so
`library/llama3.2:3b-instruct-q4_0` matches `llama3.2`.

These are approximate and are treated as a prior, not evidence. They decide
whether a run is flagged high risk before scoring starts, nothing more. A model
with no entry is `unknown`, which is never treated as safe.

## CLI

```
chronoguard version                  print the version
chronoguard models                   list installed models and their tool support
chronoguard run TASK                 one agent run against the fixture corpora
chronoguard probe                    parametric leakage, no tools
chronoguard report TASK              all three, plus a verdict
```

Shared: `--host` (Ollama host), `--model`, `--as-of`.

`run`: `--mode`, `--max-steps`, `--policy`, `--json`.

`probe`: `--cases`, `--cutoffs`, `--judge`, `--max-future`, `--max-control`,
`--json`.

`report`: `--judge`, `--mode`, `--policy`, `--max-steps`, `--max-claims`,
`--max-future`, `--max-control`, `--skip-probe`, `--skip-claims`, `--json`,
`--json-out PATH`.

Exit codes: `1` for an unreachable Ollama server, `2` for a bad argument such as
an `as_of` without an offset.

## Environment

`OLLAMA_HOST` sets the default Ollama host. The scheme is optional, so
`localhost:11434` and `http://localhost:11434` both work. Defaults to
`http://localhost:11434`.
