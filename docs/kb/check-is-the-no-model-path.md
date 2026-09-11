---
id: check-is-the-no-model-path
title: chronoguard check answers "will this corpus work" offline
type: procedure
description: Run the guard over a corpus with no model, and read the two failure modes that look like success.
tags: [cli, corpus, howto]
links: [test-that-the-raw-tool-leaks-first, revision-dates-are-a-third-channel, undated-records-rejected-by-default]
source: src/chronoguard/preflight.py
---
`chronoguard check` runs Layer 1 over a corpus file and reports what the guard
would do. No model, no network, no Ollama. It is the thing to run before
spending a model call.

    chronoguard check corpus.json --as-of 2024-03-01T00:00:00Z \
      --published-key created_utc --updated-key amended --source-key doc_id

Accepts a JSON array, a JSONL file, or a wrapper object (auto-detected when it
holds exactly one list, otherwise name it with `--results-key`).

## The two states it exists to catch

Both look fine if you only glance at a report:

- **Everything dropped.** The agent receives no evidence, so whatever it answers
  came from its weights. Almost always a wrong `--published-key` or an as-of
  before the corpus starts, not a corpus that is genuinely all future.
- **Nothing dropped.** The guard never had anything to withhold, so a clean run
  proves nothing. Same argument as [[test-that-the-raw-tool-leaks-first]], one
  level up: if the corpus does not straddle the date, the experiment is vacuous.

`CorpusReport.usable` is false in both cases, and both are reported as errors.

## What else it tells you

- **Unmapped timestamp fields.** A field that parses as a timestamp in most rows
  but is not mapped sits in `metadata`, where the guard never looks. When the
  name suggests a revision (`updated`, `modified`, `amended`, `edited`,
  `revised`, `changed`) it is a warning naming the fix, because that is a leak
  waiting to happen, see [[revision-dates-are-a-third-channel]].
- **Naive date fields.** Values that only parse once a timezone is assumed.
  Reported whether or not the field is mapped, because the common version of
  this is every record dated and every date rejected. The fix is `assume_tz` on
  the adapter, not a different field, see [[naive-datetimes-are-not-instants]].
- **Unparseable revision stamps.** Counted, because a well-dated record with a
  junk `updated_at` is still admitted and you should know how often that happens.
- **`allow_undated` doing damage.** When it is on and there are undated records,
  it says how many are now reaching the agent unchecked.

Advice is conditioned on the numbers rather than fired blindly. A handful of
undated rows in an otherwise well-dated corpus is just how the data is; most of
the corpus coming back undated means the mapping is probably wrong, and only
then does it suggest other fields. A revision field is never offered as the fix
for a missing publication date.
