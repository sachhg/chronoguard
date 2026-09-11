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
TemporalGuard(as_of, *, policy=GuardPolicy.STRICT, allow_undated=False,
              revisions=RevisionPolicy.REJECT)
```

| Option | Default | Meaning |
| --- | --- | --- |
| `as_of` | required | The instant being simulated. Aware datetime or ISO string with an offset. |
| `policy` | `strict` | `strict` drops violations. `warn` keeps and flags them, for measuring how much a corpus *would* have leaked. |
| `allow_undated` | `False` | Admit records with no usable publication timestamp. Covers missing, junk and timezone-naive dates together. |
| `revisions` | `reject` | `reject` treats an `updated_at` at or after `as_of` as a violation. `ignore` filters on publication date only. |

Records the `warn` policy lets through still count as violations, so
`filtered_count` and `violation_count` are separate numbers.

`policy` decides what the agent *sees*. `revisions` and `allow_undated` decide
what counts as a violation in the first place, so `warn` and `strict` always
reach the same verdict on the same record.

### Verdicts

| Verdict | Meaning |
| --- | --- |
| `allowed` | Published strictly before `as_of`, and not edited since. |
| `future` | Published at or after `as_of`. |
| `undated` | No publication timestamp. |
| `unparseable` | A publication timestamp was supplied but isn't a usable instant. |
| `revised` | Published before `as_of` but edited at or after it. |

A record with no `updated_at` can never be `revised`, so nothing changes on an
existing corpus until an adapter names the field.

## EvidenceRecord

| Field | Default | Meaning |
| --- | --- | --- |
| `content` | required | The text the agent will see. |
| `source_id` | required | Stable id: a URL, a document id, a primary key. |
| `published_at` | `None` | When the world could first have seen this. Filtered on. |
| `updated_at` | `None` | When it was last revised. Filtered on unless `revisions="ignore"`. |
| `retrieved_at` | `None` | When your pipeline fetched it. Never filtered on, audit only. |
| `metadata` | `{}` | Anything else. `MappingAdapter` parks unconsumed fields here. |
| `published_at_raw` | `None` | What a timestamp was before it failed to parse, kept for reporting. |
| `updated_at_raw` | `None` | Same, for an unparseable revision timestamp. |

Two derived properties: `is_revised` (an `updated_at` strictly later than
`published_at`; equal stamps are an insert, not an edit) and `latest_instant`
(the most recent moment the content is shown to have existed in its current
form).

`EvidenceRecord(...)` is strict and raises on a naive datetime.
`EvidenceRecord.from_source(...)` is lenient, never raises, and takes an extra
`assume_tz` for corpora that store naive timestamps.

## MappingAdapter

```python
MappingAdapter(*, content_key="content", source_key=("source_id", "id", "url"),
               published_key=("published_at", "published", "date"),
               updated_key=None, retrieved_key=None, results_key=None,
               metadata_keys=None, assume_tz=None, separator="\n")
```

| Option | Meaning |
| --- | --- |
| `content_key` | One field, or several joined in order. Missing ones are skipped, so `("title", "snippet")` works on records that only have a title. |
| `source_key` | One field or several candidates, first hit wins. Falls back to `record-<n>` when none are present. |
| `published_key` | Same, for the publication timestamp. |
| `updated_key` | Optional, off by default. Name it on any mutable source or the field lands in `metadata`, where the guard never looks. |
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

Wrapping an `async def` returns an awaitable, so you await the guarded tool
exactly as you would have awaited the original. Filtering happens after the
await, which means nothing is audited until the coroutine actually runs. A class
whose `__call__` is async is handled too. Async generators are not supported.

```python
guarded = guard_tool(async_search, guard, adapter, audit=audit)
kept = await guarded("meridian pricing")
```

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
| `judge_model` | `None` | Model for claim classification. Reuses the agent model when unset. Prefer a fast non-reasoning model here: classification is mechanical, and a thinking model can spend minutes per claim. |
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
chronoguard check CORPUS             what the guard would do to your data, no model
chronoguard cases                    which probe questions apply at an as-of, no model
chronoguard models                   list installed models and their tool support
chronoguard run TASK                 one agent run against the fixture corpora
chronoguard probe                    parametric leakage, no tools
chronoguard report TASK              all three, plus a verdict
```

Shared: `--host` (Ollama host), `--model`, `--as-of`, `--backend`, `--base-url`.
`check` and `cases` take none of those, because they never talk to a model.

`check`: `--published-key`, `--updated-key`, `--source-key`, `--content-key`,
`--results-key`, `--policy`, `--revisions`, `--allow-undated`, `--fail-on`,
`--json`.

`cases`: `--cases`, `--fail-on`, `--json`.

`run`: `--mode`, `--max-steps`, `--policy`, `--json`.

`probe`: `--cases`, `--cutoffs`, `--judge`, `--max-future`, `--max-control`,
`--json`.

`report`: `--judge`, `--mode`, `--policy`, `--max-steps`, `--max-claims`,
`--max-future`, `--max-control`, `--skip-probe`, `--skip-claims`, `--fail-on`,
`--json`, `--json-out PATH`.

### Exit codes

| Code | Means |
| --- | --- |
| `0` | Ran fine, nothing tripped a threshold. |
| `1` | No Ollama server, or no models installed. |
| `2` | Bad argument: an `as_of` without an offset, an unreadable corpus, an unknown policy. |
| `3` | The command completed and `--fail-on` says the result is unacceptable. |

`2` and `3` are separate on purpose. A misconfigured run and a run that came
back badly are different events, and CI usually wants to shout about the first
and gate on the second.

### --fail-on

Off by default (`never`) on both commands, so adding it breaks nothing.

```bash
chronoguard check corpus.json --as-of 2024-03-01T00:00:00Z --fail-on error
chronoguard report "..." --fail-on elevated
```

`report` takes a risk level and fires when the headline risk reaches it or
worse. The order is `low` < `unknown` < `elevated` < `high`, so gating on `low`
also catches a run that couldn't measure something.

`check` takes a finding severity, `error` or `warning`. An unmapped revision
field is a warning rather than an error, so `warning` is the stricter setting.

Output comes first either way. The report is printed and `--json-out` written
before the threshold is checked, because the run that failed the build is the
one whose output you need to read.

## Backends

ChronoGuard needs four things from a model server, and anything providing them
works. Two ship:

```bash
chronoguard report "..."                                   # ollama, the default
chronoguard report "..." --base-url http://localhost:8000  # anything OpenAI-compatible
```

| Backend | Use for | Selected by |
| --- | --- | --- |
| `ollama` | A local Ollama server. The default. | nothing, or `--backend ollama` |
| `openai-compat` | vLLM, LM Studio, llama.cpp's server, text-generation-webui, OpenRouter, hosted APIs. | `--backend openai-compat`, or just `--base-url` |

`--base-url` selects `openai-compat` on its own, since naming an OpenAI API root
and getting an Ollama client is never what anyone meant. It accepts the root with
or without the `/v1` suffix.

```python
from chronoguard import OpenAICompatClient, ScenarioConfig, run_scenario

client = OpenAICompatClient("http://localhost:8000", tool_models={"Qwen/Qwen2.5-7B-Instruct"})
report = run_scenario(ScenarioConfig(task="...", as_of="..."), client=client)
```

| Option | Default | Meaning |
| --- | --- | --- |
| `base_url` | `OPENAI_BASE_URL`, then `http://localhost:8000/v1` | API root, `/v1` optional. |
| `api_key` | `OPENAI_API_KEY` | Bearer token. No header is sent without one, which is what local servers want. |
| `timeout` | `600.0` | Seconds per request. |
| `tool_models` | `True` | Which models get real tool definitions. `True` for all, `False` for none, or a set of names. There's no capability endpoint in this API, so this is a declaration rather than something discoverable. |

Writing your own backend means satisfying `ChatBackend`: `chat`, `pick_model`,
`supports_tools`, `list_models`, `model_names`, `is_available`, and a `host`
attribute. Raise `BackendUnavailable`, or `BackendTimeout` when the server is up
and the model is just slow.

## Environment

| Variable | Used by | Default |
| --- | --- | --- |
| `OLLAMA_HOST` | `OllamaClient` | `http://localhost:11434` |
| `OPENAI_BASE_URL` | `OpenAICompatClient` | `http://localhost:8000/v1` |
| `OPENAI_API_KEY` | `OpenAICompatClient` | none, and no header is sent |

The scheme is optional everywhere, so `localhost:11434` and
`http://localhost:11434` both work.

## Timeouts

`OllamaClient(timeout=...)` defaults to 600 seconds per request. That is
deliberately generous: reasoning models spend a long time thinking, and a single
claim classification against qwen3:4b on a long evidence block can run past
three minutes.

A request that exceeds the limit raises `OllamaTimeout`, which subclasses
`OllamaUnavailable` but means something different, so the CLI does not tell you
to start a server that is already running.
